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
  - Regenerates posts/<date>.html, data/posts.json, and index.html.
    This script only writes files — it does not run git. Commit and push
    the result yourself (or let the caller script do it).
"""

import argparse
import datetime
import json
import re
import sys
from pathlib import Path
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
SITE_TAGLINE = "A weekday brief on Carnival, Royal Caribbean, Norwegian, and the rest of the cruise world."
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
        "body_html": "\n".join(body_html_parts),
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
      <a href="https://github.com/spencertownley-sys/CruiseNewsYouCanUse">About this brief</a>
    </nav>
  </div>
</header>
<main>
{content}
</main>
<footer class="site-footer">
  <p>{site_name} &mdash; an automated weekday digest for cruise-industry watchers.<br>
  Built with <a href="https://claude.com">Claude</a>. Sources linked throughout.</p>
</footer>
</body>
</html>
"""


def render_post_page(entry, parsed):
    top3_block = ""
    if parsed["top3_html"]:
        top3_block = f"""<div class="top-stories">
  <h2>Top 3</h2>
  {parsed['top3_html']}
</div>"""

    watch_block = ""
    if parsed["watch_next_html"]:
        watch_block = f"""<div class="watch-next">
  <h2>Watch next</h2>
  {parsed['watch_next_html']}
</div>"""

    window_html = f'<p class="post-window">{escape(parsed["window"])}</p>' if parsed["window"] else ""

    content = f"""<a class="back-link" href="../index.html">&larr; All posts</a>
<article>
  <div class="post-header">
    <p class="post-date">{entry['display_date']}</p>
    <h1>{escape(parsed['title'])}</h1>
    {window_html}
  </div>
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


def render_index_page(entries):
    if not entries:
        list_html = '<p class="empty-state">No posts yet — check back after the next weekday brief.</p>'
    else:
        cards = []
        for e in sorted(entries, key=lambda x: x["date"], reverse=True):
            cards.append(f"""<li class="post-card">
  <p class="post-date">{e['display_date']}</p>
  <h2><a href="{e['url']}">{escape(e['title'])}</a></h2>
  <p class="post-excerpt">{escape(e.get('excerpt', ''))}</p>
  <a class="read-more" href="{e['url']}">Read the brief &rarr;</a>
</li>""")
        list_html = f'<ul class="post-list">\n{"".join(cards)}\n</ul>'

    content = f"""<p class="intro-blurb">A weekday digest of what's new across Carnival Corporation
(Carnival, Princess, Holland America, Seabourn), Norwegian Cruise Line Holdings, Royal Caribbean
Group, and the wider cruise industry &mdash; earnings, bookings, itinerary changes, incidents, and
what's trending with cruisers online. Published automatically after each weekday run.</p>
{list_html}"""

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

    POSTS_DIR.mkdir(exist_ok=True)
    post_html = render_post_page(entry, parsed)
    (POSTS_DIR / f"{slug}.html").write_text(post_html)

    index_html = render_index_page(entries)
    (ROOT / "index.html").write_text(index_html)

    print(f"Wrote posts/{slug}.html")
    print(f"Updated index.html ({len(entries)} post(s) total)")
    print(f"Updated data/posts.json")


if __name__ == "__main__":
    main()
