#!/usr/bin/env python3
"""
Publish a new post to the Cruise News You Can Use blog.

Usage:
    python3 scripts/publish_post.py --md data/2026-10-01.md

Expects a Markdown source file shaped like:

    # Cruise brief — Thu Oct 1, 2026
    *Covering roughly Wed Sep 30 - Thu Oct 1, 2026*

    ## Top 3
    1. First headline...
    2. Second headline...
    3. Third headline...

    ## Carnival Corp & brands

    ### Carnival Cruise Line
    Nothing new.

    ### Princess
    [CCL] Some paragraph of news with a [markdown link](https://example.com).

    ## Watch next
    Upcoming earnings, ship deliveries, etc.

Rules for the source file:
  - The first line MUST be an H1 (`# ...`) — this becomes the post title.
  - An optional italic line right after the H1 becomes the "window" subtitle.
  - A `## Top 3` section (numbered list) is pulled out into a highlighted box.
  - A `## Watch next` section (if present) is pulled out into a highlighted
    box at the end, and excluded from the main body.
  - Every other `##`/`###` section is rendered as the article body, in order.
  - Ticker symbols written as `[CCL]` (not followed by a `(`, i.e. not a
    markdown link) are automatically styled as little badges.
  - Regenerates posts/<date>.html, data/posts.json, data/search.json (the
    full-text search index, rebuilt from every data/*.md file), and index.html.
    This script only writes files — it does not run git. Commit and push
    the result yourself (or let the caller script do it).
"""

import argparse
import datetime
import json
import re
import sys
from pathlib import Path
from html import unescape as html_unescape
from xml.sax.saxutils import escape

try:
    import markdown as md
except ImportError:
    sys.exit("The 'markdown' package is required: pip install markdown --break-system-packages")

ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = ROOT / "posts"
DATA_DIR = ROOT / "data"
MANIFEST = DATA_DIR / "posts.json"

SITE_NAME = "Cruise News You Can Use"
SITE_TAGLINE = "Daily updates on the major cruise lines and the rest of the cruise world."
SITE_URL = "https://spencertownley-sys.github.io/CruiseNewsYouCanUse/"

TICKER_RE = re.compile(r"\[([A-Z]{2,6})\](?!\()")


def load_manifest():
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return []


def save_manifest(entries):
    DATA_DIR.mkdir(exist_ok=True)
    MANIFEST.write_text(json.dumps(entries, indent=2) + "\n")


def style_tickers(text):
    return TICKER_RE.sub(r'<span class="ticker">\1</span>', text)


def split_sections(body_lines):
    """Split the markdown body (everything after the H1/subtitle) into a
    list of (heading_level, heading_text, content_lines) blocks, where
    content precedes the first heading goes under heading_text=None."""
    sections = []
    current_heading = None
    current_level = 0
    current_lines = []
    heading_re = re.compile(r"^(#{2,3})\s+(.*)$")
    for line in body_lines:
        m = heading_re.match(line)
        if m and len(m.group(1)) == 2:
            if current_heading is not None or current_lines:
                sections.append((current_level, current_heading, current_lines))
            current_level = 2
            current_heading = m.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_heading is not None or current_lines:
        sections.append((current_level, current_heading, current_lines))
    return sections


def render_markdown_block(text):
    text = style_tickers(text)
    html = md.markdown(text, extensions=["extra"])
    # Flag "Nothing new" one-liners for italic muted styling.
    html = re.sub(
        r"<p>(Nothing new\.?)</p>",
        r'<p class="nothing-new">\1</p>',
        html,
    )
    return html


def parse_source(md_text):
    lines = md_text.splitlines()
    if not lines or not lines[0].startswith("# "):
        sys.exit("Source markdown must start with an H1 title line, e.g. '# Cruise brief — ...'")
    title = lines[0][2:].strip()

    idx = 1
    window = ""
    # skip blank lines
    while idx < len(lines) and lines[idx].strip() == "":
        idx += 1
    if idx < len(lines) and lines[idx].strip().startswith("*") and lines[idx].strip().endswith("*"):
        window = lines[idx].strip().strip("*").strip()
        idx += 1

    rest = lines[idx:]
    sections = split_sections(rest)

    top3_html = ""
    watch_next_html = ""
    body_html_parts = []

    for level, heading, content_lines in sections:
        content = "\n".join(content_lines).strip()
        if heading is None:
            if content:
                body_html_parts.append(render_markdown_block(content))
            continue
        heading_lower = heading.lower()
        if heading_lower == "top 3":
            inner = render_markdown_block(content)
            top3_html = inner
        elif heading_lower == "watch next":
            watch_next_html = render_markdown_block(content)
        else:
            body_html_parts.append(f"<h2>{escape(heading)}</h2>")
            if content:
                body_html_parts.append(render_markdown_block(content))

    return {
        "title": title,
        "window": window,
        "top3_html": top3_html,
        "body_html": add_heading_anchors("\n".join(body_html_parts)),
        "watch_next_html": watch_next_html,
    }


def extract_date_from_title(title, fallback):
    # Try to find something like "Sep 30, 2026" or "October 1, 2026" in the title.
    for fmt_try in (r"([A-Za-z]{3,9}\s+\d{1,2},\s*\d{4})",):
        m = re.search(fmt_try, title)
        if m:
            for fmt in ("%b %d, %Y", "%B %d, %Y"):
                try:
                    return datetime.datetime.strptime(m.group(1), fmt).date()
                except ValueError:
                    continue
    return fallback


SEARCH_INDEX = DATA_DIR / "search.json"


def slugify(text):
    text = html_unescape(re.sub(r"<[^>]+>", "", text)).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "section"


def add_heading_anchors(html):
    """Give every <h2>/<h3> in the rendered body an id so search results
    can deep-link straight to a section."""
    seen = {}

    def repl(m):
        level, inner = m.group(1), m.group(2)
        base = slugify(inner)
        n = seen.get(base, 0)
        seen[base] = n + 1
        slug = base if n == 0 else f"{base}-{n + 1}"
        return f'<h{level} id="{slug}">{inner}</h{level}>'

    return re.sub(r"<h([23])>(.*?)</h\1>", repl, html, flags=re.S)


MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:[^)]+)\)")


def markdown_to_plain(text):
    text = MD_LINK_RE.sub(r"\1", text)          # keep link text, drop URL
    text = re.sub(r"[*_`#>]+", "", text)        # strip emphasis / heading marks
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.M)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def index_source(md_text, entry):
    """Break one post's markdown into searchable section records."""
    lines = md_text.splitlines()
    records = []
    h2 = None
    h3 = None
    buf = []

    def flush():
        if h2 is None and h3 is None:
            return  # preamble (the italic "covering" line) is not a section
        text = markdown_to_plain("\n".join(buf))
        if not text:
            return
        heading = " / ".join(h for h in (h2, h3) if h)
        anchor = slugify(h3 or h2 or "")
        records.append({
            "date": entry["date"],
            "display_date": entry["display_date"],
            "title": entry["title"],
            "url": entry["url"] + (f"#{anchor}" if anchor else ""),
            "section": heading,
            "text": text,
        })

    for line in lines[1:]:  # skip H1
        m = re.match(r"^(#{2,3})\s+(.*)$", line)
        if m:
            flush()
            buf = []
            if len(m.group(1)) == 2:
                h2, h3 = m.group(2).strip(), None
            else:
                h3 = m.group(2).strip()
        else:
            buf.append(line)
    flush()
    return records


def rebuild_search_index(entries):
    """Rebuild data/search.json from every data/<date>.md so the index is
    always complete, even if an earlier run was interrupted."""
    by_date = {e["date"]: e for e in entries}
    records = []
    for md_file in sorted(DATA_DIR.glob("????-??-??.md"), reverse=True):
        entry = by_date.get(md_file.stem)
        if not entry:
            continue
        records.extend(index_source(md_file.read_text(), entry))
    SEARCH_INDEX.write_text(json.dumps(records, ensure_ascii=False) + "\n")
    return len(records)


PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{page_title}</title>
<meta name="description" content="{meta_description}">
<link rel="stylesheet" href="{asset_prefix}assets/style.css">
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🌊</text></svg>">
</head>
<body>
<header class="site-header">
  <div class="site-header-inner">
    <div class="site-brand">
      <span class="wave">🌊</span>
      <h1 class="site-title"><a href="{asset_prefix}index.html">{site_name}</a></h1>
    </div>
    <p class="site-tagline">{site_tagline}</p>
    <nav class="site-nav">
      <a href="{asset_prefix}index.html">Home</a>
      <a href="{asset_prefix}index.html#search">Search</a>
      <a href="{asset_prefix}social.html">Social Pulse</a>
      <a href="https://github.com/spencertownley-sys/CruiseNewsYouCanUse">About this brief</a>
    </nav>
  </div>
</header>
<main>
{content}
</main>
<footer class="site-footer">
  <p>{site_name} &mdash; a daily digest for cruise-industry watchers. Sources linked throughout.</p>
</footer>
</body>
</html>
"""


def render_post_page(entry, parsed, episodes):
    top3_block = ""
    if parsed["top3_html"]:
        top3_block = f"""<div class="top-stories" id="top-3">
  <h2>Top 3</h2>
  {parsed['top3_html']}
</div>"""

    watch_block = ""
    if parsed["watch_next_html"]:
        watch_block = f"""<div class="watch-next" id="watch-next">
  <h2>Watch next</h2>
  {parsed['watch_next_html']}
</div>"""

    window_html = f'<p class="post-window">{escape(parsed["window"])}</p>' if parsed["window"] else ""
    podcast_block = podcast_player_block(entry["date"], episodes, asset_prefix="../")

    content = f"""<a class="back-link" href="../index.html">&larr; All posts</a>
<article>
  <div class="post-header">
    <p class="post-date">{entry['display_date']}</p>
    <h1>{escape(parsed['title'])}</h1>
    {window_html}
  </div>
  {podcast_block}
  {top3_block}
  <div class="post-body">
  {parsed['body_html']}
  </div>
  {watch_block}
</article>"""

    return PAGE_TEMPLATE.format(
        page_title=f"{parsed['title']} — {SITE_NAME}",
        meta_description=escape(entry.get("excerpt", parsed["title"])),
        asset_prefix="../",
        site_name=SITE_NAME,
        site_tagline=SITE_TAGLINE,
        content=content,
    )


PODCAST_MANIFEST = DATA_DIR / "podcast.json"


def load_podcast_manifest():
    if PODCAST_MANIFEST.exists():
        try:
            return {e["date"]: e for e in json.loads(PODCAST_MANIFEST.read_text())}
        except (ValueError, KeyError):
            return {}
    return {}


def podcast_player_block(date_iso, episodes, asset_prefix):
    episode = episodes.get(date_iso)
    if not episode:
        return ""
    return f"""<div class="podcast-player" id="podcast">
  <h2>🎧 Listen to today's brief</h2>
  <audio controls preload="none" src="{asset_prefix}{episode['url']}"></audio>
</div>"""


def social_strip():
    """Compact Social Pulse summary for the homepage, read from
    data/social/latest.json when the social listening run has produced one."""
    latest = DATA_DIR / "social" / "latest.json"
    if not latest.exists():
        return ""
    try:
        rep = json.loads(latest.read_text())
    except ValueError:
        return ""
    t = rep.get("totals", {})
    if not t.get("posts"):
        return ""

    def cls(score):
        return "pos" if score >= 15 else ("neg" if score <= -15 else "neu")

    def fmt(score):
        return f"{score:+d}" if score else "0"

    chips = "".join(
        f'<li><span class="brand">{escape(b["name"])}</span>'
        f'<span class="score {cls(b["net_score"])}">{fmt(b["net_score"])}</span>'
        f'<span class="n">{b["posts"]}</span></li>'
        for b in rep.get("brands", [])[:6])
    standouts = [s["name"] for s in rep.get("standouts", {}).get("ships", [])] + \
                [d["name"] for d in rep.get("standouts", {}).get("destinations", [])]
    so = f'<p class="strip-standouts">Standing out: {escape(", ".join(standouts[:4]))}</p>' if standouts else ""
    day = datetime.date.fromisoformat(rep["date"]).strftime("%b %-d")
    return f"""<section class="social-strip">
  <div class="strip-head">
    <p class="strip-title"><a href="social.html">Social Pulse</a> <span class="muted">{day}</span></p>
    <p class="strip-total">{t["posts"]} posts &middot; net <span class="score {cls(t["net_score"])}">{fmt(t["net_score"])}</span></p>
  </div>
  <ul class="strip-brands">{chips}</ul>
  {so}
  <a class="read-more" href="social.html">See brand, ship and destination scores &rarr;</a>
</section>"""


def render_index_page(entries, episodes):
    if not entries:
        list_html = '<p class="empty-state">No posts yet — check back after the next weekday brief.</p>'
    else:
        cards = []
        for e in sorted(entries, key=lambda x: x["date"], reverse=True):
            badge = ' <span class="podcast-badge" title="Podcast episode available">🎧</span>' if e["date"] in episodes else ""
            cards.append(f"""<li class="post-card">
  <p class="post-date">{e['display_date']}{badge}</p>
  <h2><a href="{e['url']}">{escape(e['title'])}</a></h2>
  <p class="post-excerpt">{escape(e.get('excerpt', ''))}</p>
  <a class="read-more" href="{e['url']}">Read the brief &rarr;</a>
</li>""")
        list_html = f'<ul class="post-list">\n{"".join(cards)}\n</ul>'

    count = len(entries)
    oldest = min(entries, key=lambda x: x["date"])["display_date"] if entries else ""
    archive_note = (
        f'{count} daily brief{"s" if count != 1 else ""} on file since {oldest}. Every day\'s update is kept here and searchable.'
        if entries else "Every day's update will be kept here and searchable."
    )

    content = f"""<p class="intro-blurb">A daily digest of what's new across Carnival Corporation
(Carnival, Princess, Holland America, Seabourn), Norwegian Cruise Line Holdings, Royal Caribbean
Group, and the wider cruise industry &mdash; earnings, bookings, itinerary changes, incidents, and
what's trending with cruisers online.</p>
{social_strip()}
<section class="search" id="search">
  <form class="search-form" role="search" onsubmit="return false">
    <label class="search-label" for="search-input">Search every brief</label>
    <div class="search-row">
      <input id="search-input" type="search" placeholder="e.g. Princess, norovirus, RCL guidance, Alaska" autocomplete="off">
      <button type="button" id="search-clear" class="search-clear" hidden>Clear</button>
    </div>
    <p class="search-hint">{archive_note}</p>
  </form>
  <div id="search-results" class="search-results" hidden></div>
</section>
<h2 class="archive-heading" id="archive">All briefs</h2>
{list_html}
<script src="assets/search.js" defer></script>"""

    return PAGE_TEMPLATE.format(
        page_title=SITE_NAME,
        meta_description=SITE_TAGLINE,
        asset_prefix="",
        site_name=SITE_NAME,
        site_tagline=SITE_TAGLINE,
        content=content,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", required=True, help="Path to the source Markdown file for this post")
    ap.add_argument("--date", help="ISO date (YYYY-MM-DD) for this post; inferred from title if omitted")
    args = ap.parse_args()

    src_path = Path(args.md)
    md_text = src_path.read_text()
    parsed = parse_source(md_text)

    if args.date:
        post_date = datetime.date.fromisoformat(args.date)
    else:
        post_date = extract_date_from_title(parsed["title"], datetime.date.today())

    date_iso = post_date.isoformat()
    display_date = post_date.strftime("%A, %B %-d, %Y")
    slug = date_iso
    url = f"posts/{slug}.html"

    # Build a short excerpt from the first Top 3 item, stripped of markdown/html.
    excerpt_source = parsed["top3_html"] or parsed["body_html"]
    excerpt_text = re.sub(r"<[^>]+>", " ", excerpt_source)
    excerpt_text = re.sub(r"\s+", " ", excerpt_text).strip()
    excerpt = (excerpt_text[:180] + "…") if len(excerpt_text) > 180 else excerpt_text

    entries = load_manifest()
    entries = [e for e in entries if e["date"] != date_iso]  # replace if re-publishing same date
    entry = {
        "date": date_iso,
        "display_date": display_date,
        "title": parsed["title"],
        "window": parsed["window"],
        "excerpt": excerpt,
        "url": url,
    }
    entries.append(entry)
    save_manifest(entries)

    episodes = load_podcast_manifest()

    POSTS_DIR.mkdir(exist_ok=True)
    post_html = render_post_page(entry, parsed, episodes)
    (POSTS_DIR / f"{slug}.html").write_text(post_html)

    index_html = render_index_page(entries, episodes)
    (ROOT / "index.html").write_text(index_html)

    n_records = rebuild_search_index(entries)

    print(f"Wrote posts/{slug}.html")
    print(f"Updated index.html ({len(entries)} post(s) total)")
    print(f"Updated data/posts.json")
    print(f"Rebuilt data/search.json ({n_records} searchable sections)")


if __name__ == "__main__":
    main()
