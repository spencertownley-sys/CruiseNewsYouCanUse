#!/usr/bin/env python3
"""
Generate a 5-10 minute "Audio Overview" podcast episode for a day's cruise
brief, using the Gemini Notebook Enterprise API (formerly NotebookLM
Enterprise; REST surface is still under discoveryengine.googleapis.com,
package google.cloud.notebooklm.v1alpha, status pre-GA/limited-support as of
this writing).

Usage:
    python3 scripts/podcast.py generate --date 2026-10-05

Requires three things in the environment (see README's "Daily podcast"
section for how to provision them):
    NOTEBOOKLM_SA_JSON       - full JSON contents of a service account key
                               that has been granted the "Cloud NotebookLM
                               User" IAM role on the project below.
    NOTEBOOKLM_PROJECT_NUMBER - the GCP project NUMBER (not project ID) that
                               has Gemini Enterprise / Education Premium
                               licensing and the Discovery Engine API enabled.
    NOTEBOOKLM_LOCATION      - optional, defaults to "global".

If any of these are missing, `generate` prints a one-line notice and exits
0 (not an error) so a caller script can treat the podcast step as optional
and keep publishing the rest of the day's brief.

Pipeline:
    1. Read data/<date>.md (the day's digest) and turn it into plain text,
       dropping "Nothing new." sections so the model doesn't waste airtime.
    2. Create a notebook, add that text as a source.
    3. Request an audio overview, steering it with config/podcast.json's
       voice_note and target length.
    4. Poll the notebook until the audio overview is ready, then download it
       to podcast/<date>.mp3 and record it in data/podcast.json.

NOTE ON STEP 4: Google's public docs describe audioOverviews.create in
detail but do not document a get/poll method or the exact shape of the
finished audio's location (the consumer UI just says "open the notebook's
Studio section"). This script polls notebooks.get and searches the response
for the completed overview, which matches the only documented resource
shape available at write time. If Google's actual response differs, the
first real run will likely need a small fix here — run with --debug to dump
raw API responses when troubleshooting.
"""

import argparse
import datetime
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PODCAST_DIR = ROOT / "podcast"
MANIFEST = DATA_DIR / "podcast.json"
CONFIG_PATH = ROOT / "config" / "podcast.json"

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def env_or_none(name):
    val = os.environ.get(name, "").strip()
    return val or None


def check_env():
    missing = [
        name for name in ("NOTEBOOKLM_SA_JSON", "NOTEBOOKLM_PROJECT_NUMBER")
        if not env_or_none(name)
    ]
    return missing


def get_access_token(debug=False):
    try:
        from google.oauth2 import service_account
        import google.auth.transport.requests
    except ImportError:
        sys.exit(
            "The 'google-auth' package is required: "
            "pip install google-auth --break-system-packages"
        )
    sa_info = json.loads(os.environ["NOTEBOOKLM_SA_JSON"])
    credentials = service_account.Credentials.from_service_account_info(
        sa_info, scopes=SCOPES
    )
    credentials.refresh(google.auth.transport.requests.Request())
    if debug:
        print(f"[debug] authenticated as {sa_info.get('client_email', '?')}", file=sys.stderr)
    return credentials.token


def discovery_host():
    location = os.environ.get("NOTEBOOKLM_LOCATION", "global").strip() or "global"
    host_prefix = "" if location == "global" else f"{location}-"
    return f"https://{host_prefix}discoveryengine.googleapis.com/v1alpha"


def api_base():
    location = os.environ.get("NOTEBOOKLM_LOCATION", "global").strip() or "global"
    project_number = os.environ["NOTEBOOKLM_PROJECT_NUMBER"].strip()
    return f"{discovery_host()}/projects/{project_number}/locations/{location}"


def resource_url(resource_name):
    """resource_name is a full resource path like
    'projects/123/locations/global/notebooks/abc' as returned by the API."""
    return f"{discovery_host()}/{resource_name}"


def api_request(method, url, token, json_body=None, debug=False):
    try:
        import requests
    except ImportError:
        sys.exit("The 'requests' package is required: pip install requests --break-system-packages")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if debug:
        print(f"[debug] {method} {url}\n[debug] body: {json.dumps(json_body) if json_body else None}", file=sys.stderr)
    resp = requests.request(method, url, headers=headers, json=json_body, timeout=60)
    if debug:
        print(f"[debug] status {resp.status_code}: {resp.text[:2000]}", file=sys.stderr)
    if not resp.ok:
        sys.exit(f"NotebookLM API error ({method} {url}): {resp.status_code} {resp.text}")
    return resp


def digest_to_source_text(md_text):
    """Turn a data/<date>.md digest into plain text for the notebook source,
    dropping any section whose entire body is 'Nothing new.'"""
    lines = md_text.splitlines()
    out = []
    buf = []
    heading = None

    def flush():
        body = "\n".join(buf).strip()
        if heading and body.strip().rstrip(".").lower() == "nothing new":
            return
        if heading:
            out.append(f"\n{heading}\n")
        if body:
            out.append(body)

    for line in lines:
        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            flush()
            buf = []
            heading = m.group(2).strip()
        else:
            buf.append(line)
    flush()

    text = "\n".join(out)
    text = re.sub(r"\[([A-Z]{2,6})\](?!\()", r"\1", text)      # drop ticker badge brackets
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)        # markdown links -> link text only
    text = re.sub(r"[*_`]+", "", text)
    return text.strip()


def build_episode_focus(config):
    lo = config.get("target_minutes_min", 5)
    hi = config.get("target_minutes_max", 10)
    voice_note = config.get(
        "voice_note",
        "Two co-hosts, casual but well-informed, talking to cruise-industry colleagues.",
    )
    return (
        f"Produce a {lo}-{hi} minute episode covering this cruise industry brief. "
        f"{voice_note}"
    )


def find_completed_audio(notebook_json, debug=False):
    """Best-effort search of a notebooks.get response for a finished audio
    overview. See the NOTE at the top of this file."""
    DONE_STATES = {"SUCCEEDED", "COMPLETE", "COMPLETED", "AUDIO_OVERVIEW_STATUS_SUCCEEDED"}

    def walk(node):
        if isinstance(node, dict):
            keys_lower = {k.lower(): k for k in node}
            has_audio_key = any("audio" in k for k in keys_lower)
            state_key = next((keys_lower[k] for k in keys_lower if k in ("state", "status")), None)
            if has_audio_key and state_key and str(node.get(state_key)).upper() in DONE_STATES:
                return node
            for v in node.values():
                found = walk(v)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = walk(item)
                if found:
                    return found
        return None

    result = walk(notebook_json)
    if debug and result:
        print(f"[debug] matched audio overview node: {json.dumps(result)[:500]}", file=sys.stderr)
    return result


def extract_audio_bytes(audio_node, token, debug=False):
    """Given the dict found by find_completed_audio, get raw audio bytes."""
    import base64
    try:
        import requests
    except ImportError:
        sys.exit("The 'requests' package is required: pip install requests --break-system-packages")

    for key, val in audio_node.items():
        if not isinstance(val, str):
            continue
        lk = key.lower()
        if "uri" in lk or "url" in lk:
            resp = requests.get(val, headers={"Authorization": f"Bearer {token}"}, timeout=120)
            if resp.ok:
                return resp.content
        if "data" in lk or "content" in lk or "bytes" in lk:
            try:
                return base64.b64decode(val)
            except Exception:
                continue
    return None


def cmd_generate(args):
    config = load_config()
    if not config.get("enabled", True):
        print("Podcast generation disabled in config/podcast.json — skipping.")
        return

    missing = check_env()
    if missing:
        print(
            "Podcast generation skipped: missing environment variable(s) "
            f"{', '.join(missing)}. See README's 'Daily podcast' section to set these up."
        )
        return

    date_iso = args.date
    md_path = DATA_DIR / f"{date_iso}.md"
    if not md_path.exists():
        sys.exit(f"No digest found at {md_path} — publish the day's brief first.")

    source_text = digest_to_source_text(md_path.read_text())
    if not source_text:
        print("Digest has no content after stripping 'Nothing new' sections — skipping podcast.")
        return

    token = get_access_token(debug=args.debug)
    base = api_base()

    title_prefix = config.get("notebook_title_prefix", "Cruise Brief Podcast")
    notebook = api_request(
        "POST", f"{base}/notebooks", token,
        {"title": f"{title_prefix} — {date_iso}"}, debug=args.debug,
    ).json()
    notebook_name = notebook["name"]  # e.g. projects/.../locations/.../notebooks/NOTEBOOK_ID
    print(f"Created notebook {notebook_name}")

    api_request(
        "POST", f"{resource_url(notebook_name)}/sources:batchCreate",
        token,
        {"userContents": [{"textContent": {"title": f"Cruise brief {date_iso}", "content": source_text}}]},
        debug=args.debug,
    )
    print("Added digest as a source")

    episode_focus = build_episode_focus(config)
    api_request(
        "POST",
        f"{resource_url(notebook_name)}/audioOverviews",
        token,
        {"episodeFocus": episode_focus, "languageCode": config.get("language_code", "en")},
        debug=args.debug,
    )
    print("Requested audio overview — polling for completion (this can take a few minutes)...")

    deadline = time.time() + args.timeout
    audio_node = None
    while time.time() < deadline:
        time.sleep(args.poll_interval)
        nb = api_request("GET", resource_url(notebook_name), token, debug=args.debug).json()
        audio_node = find_completed_audio(nb, debug=args.debug)
        if audio_node:
            break
        print(f"  ...still waiting ({int(deadline - time.time())}s left)")

    if not audio_node:
        sys.exit(
            f"Timed out waiting for the audio overview on {notebook_name}. "
            "Open the notebook in the Gemini Notebook Enterprise UI to check its "
            "Studio section manually, or re-run with --debug to inspect raw API responses."
        )

    audio_bytes = extract_audio_bytes(audio_node, token, debug=args.debug)
    if not audio_bytes:
        sys.exit(
            f"Audio overview reports complete but no downloadable audio field was "
            f"recognized. Raw node: {json.dumps(audio_node)[:1000]}\n"
            "This is the one part of the API Google's docs don't fully specify — "
            "run with --debug and adjust extract_audio_bytes() to match the real field name."
        )

    PODCAST_DIR.mkdir(exist_ok=True)
    out_path = PODCAST_DIR / f"{date_iso}.mp3"
    out_path.write_bytes(audio_bytes)
    print(f"Wrote {out_path} ({len(audio_bytes)} bytes)")

    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else []
    manifest = [e for e in manifest if e["date"] != date_iso]
    manifest.append({
        "date": date_iso,
        "title": f"{title_prefix} — {date_iso}",
        "url": f"podcast/{date_iso}.mp3",
        "notebook_name": notebook_name,
    })
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Updated {MANIFEST}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    gen = sub.add_parser("generate", help="Generate today's podcast episode")
    gen.add_argument("--date", required=True, help="ISO date (YYYY-MM-DD) matching data/<date>.md")
    gen.add_argument("--timeout", type=int, default=600, help="Max seconds to wait for the audio overview")
    gen.add_argument("--poll-interval", type=int, default=20, help="Seconds between status checks")
    gen.add_argument("--debug", action="store_true", help="Print raw API requests/responses")
    gen.set_defaults(func=cmd_generate)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
