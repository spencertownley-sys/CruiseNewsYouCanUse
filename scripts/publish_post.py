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
import html as htmllib
import json
import re
import sys
from pathlib import Path
from html import unescape as html_unescape
from xml.sax.saxutils import escape

try:
    import markdown as md
except ImportError:
    sys.exit("The 'markdown' package is required: pip install -r requirements.txt")

try:
    import nh3
except ImportError:
    sys.exit("The 'nh3' package is required: pip install -r requirements.txt")

ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = ROOT / "posts"
DATA_DIR = ROOT / "data"
MANIFEST = DATA_DIR / "posts.json"

SITE_NAME = "Cruise News You Can Use"
SITE_TAGLINE = "Daily updates on the major cruise lines and the rest of the cruise world."
DISCLAIMER = (
    "Cruise News You Can Use is an automated news digest for general information only. It is not financial, investment, legal or travel advice. Share prices, analyst ratings and price targets are reported from the linked sources, may be out of date or wrong, and are not a recommendation to buy, sell or hold any security. Check the original source and speak to a licensed financial adviser before making an investment decision."
)
SITE_URL = "https://spencertownley-sys.github.io/CruiseNewsYouCanUse/"

# The markdown is machine-written from web content, so its HTML is cleaned
# before it is published: no scripts, no inline handlers, http(s) links only.
ALLOWED_TAGS = {"p", "a", "ul", "ol", "li", "strong", "em", "code", "h2", "h3", "h4",
                "blockquote", "span", "br"}
ALLOWED_ATTRIBUTES = {"a": {"href"}, "span": {"class"}}
ALLOWED_URL_SCHEMES = {"http", "https"}


def sanitize_html(html):
    return nh3.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES,
                     url_schemes=ALLOWED_URL_SCHEMES, link_rel="noopener nofollow")


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
    html = sanitize_html(md.markdown(text, extensions=["extra"]))
    # Flag "Nothing new" one-liners for italic muted styling.
    html = re.sub(
        r"<p>(Nothing new\.?)</p>",
        r'<p class="nothing-new">\1</p>',
        html,
    )
    return html


LI_RE = re.compile(r"<li>(.*?)</li>", re.S)


def warn_unsourced(label, html):
    """Print a warning for every list item without a link, so the routine can
    add a source before publishing ('claim. [Headline](url) (Outlet)')."""
    for m in LI_RE.finditer(html):
        if "<a href" not in m.group(1):
            text = html_unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
            print(f"Unsourced item ({label}): {text[:100]}", file=sys.stderr)


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

    body_joined = "\n".join(body_html_parts)
    warn_unsourced("Top 3", top3_html)
    warn_unsourced("body", body_joined)
    warn_unsourced("Watch next", watch_next_html)

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
{extra_head}</head>
<body>
<header class="site-header">
  <div class="site-header-inner">
    <div class="site-brand">
      <span class="wave">🌊</span>
      <p class="site-title"><a href="{asset_prefix}index.html">{site_name}</a></p>
    </div>
    <p class="site-tagline">{site_tagline}</p>
    <nav class="site-nav">
      <a href="{asset_prefix}index.html">Home</a>
      <a href="{asset_prefix}index.html#search">Search</a>
      <a href="{asset_prefix}about.html">About</a>
    </nav>
  </div>
</header>
<main>
{content}
</main>
<footer class="site-footer">
  <p>{site_name}: a daily digest for cruise-industry watchers. Sources linked throughout. <a href="{asset_prefix}about.html">About and corrections</a></p>
  <p class="disclaimer">Cruise News You Can Use is an automated news digest for general information only. It is not financial, investment, legal or travel advice. Share prices, analyst ratings and price targets are reported from the linked sources, may be out of date or wrong, and are not a recommendation to buy, sell or hold any security. Check the original source and speak to a licensed financial adviser before making an investment decision.</p>
</footer>
</body>
</html>
"""


def attr(text):
    """Escape text for use inside an HTML attribute or <title> (idempotent on already-escaped input)."""
    return htmllib.escape(html_unescape(str(text)), quote=True)


def render_page(page_title, meta_description, asset_prefix, content, noindex=False,
                path=None, og_type="website", extra_head=""):
    """Fill PAGE_TEMPLATE; every generated page goes through here.

    path is the page's location under SITE_URL (e.g. "posts/2026-11-13.html",
    "" for the home page); pages with a path get a canonical link and Open
    Graph tags. Extra head markup (JSON-LD) is passed in through extra_head,
    so it never goes through str.format."""
    head = ""
    if noindex:
        head += '<meta name="robots" content="noindex">\n'
    if path is not None and not noindex:
        url = attr(SITE_URL + path)
        head += (
            f'<link rel="canonical" href="{url}">\n'
            f'<meta property="og:title" content="{attr(page_title)}">\n'
            f'<meta property="og:description" content="{attr(meta_description)}">\n'
            f'<meta property="og:url" content="{url}">\n'
            f'<meta property="og:type" content="{og_type}">\n'
            f'<meta property="og:site_name" content="{attr(SITE_NAME)}">\n'
            '<meta name="twitter:card" content="summary">\n'
        )
    head += extra_head
    return PAGE_TEMPLATE.format(
        extra_head=head,
        page_title=attr(page_title),
        meta_description=attr(meta_description),
        asset_prefix=asset_prefix,
        site_name=SITE_NAME,
        site_tagline=SITE_TAGLINE,
        content=content,
    )


def article_json_ld(entry):
    data = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": entry["title"],
        "datePublished": entry["date"],
        "url": SITE_URL + entry["url"],
    }
    body = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/ld+json">{body}</script>\n'


def render_post_page(entry, parsed):
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

    content = f"""<a class="back-link" href="../index.html">&larr; All posts</a>
<article>
  <div class="post-header">
    <p class="post-date"><time datetime="{entry['date']}">{entry['display_date']}</time></p>
    <h1>{escape(parsed['title'])}</h1>
    {window_html}
  </div>
  {top3_block}
  <div class="post-body">
  {parsed['body_html']}
  </div>
  {watch_block}
</article>"""

    return render_page(f"{parsed['title']} — {SITE_NAME}",
                       entry.get("excerpt", parsed["title"]), "../", content,
                       path=entry["url"], og_type="article", extra_head=article_json_ld(entry))


ISSUES_URL = "https://github.com/spencertownley-sys/CruiseNewsYouCanUse/issues/new"
OWNER_URL = "https://github.com/spencertownley-sys"
GITHUB_PRIVACY_URL = "https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement"
ABOUT_PAGE = ROOT / "about.html"


def render_about_page():
    """about.html: what the site is, how briefs are made, the not-advice note,
    corrections, privacy and storage, and who runs it. Regenerated on every run."""
    content = f"""<article class="about">
  <div class="post-header">
    <h1>About {escape(SITE_NAME)}</h1>
    <p class="post-window">What this site is, how it is made, and how to tell us about a mistake.</p>
  </div>
  <div class="post-body">
    <h2 id="what-this-is">What this is</h2>
    <p>{escape(SITE_NAME)} is a daily digest of cruise-industry news: earnings, bookings, itinerary changes, incidents, and cruise-line stock moves. Every day's brief is kept on the site and is searchable from the <a href="index.html#search">home page</a>.</p>

    <h2 id="how-briefs-are-made">How briefs are made</h2>
    <p>Each brief is compiled automatically by an AI-assisted scheduled routine from public news reports and stock data, then published without line-by-line human editing. [PLACEHOLDER: owner to confirm this wording, and to say whether a person reads each brief before it is published.] Every item links to its source, so you can check the original report.</p>

    <h2 id="not-advice">Not advice</h2>
    <p>{escape(DISCLAIMER)}</p>

    <h2 id="corrections">Corrections</h2>
    <p>If you spot a mistake, <a href="{ISSUES_URL}">report it on GitHub</a> (a free GitHub account is needed) and say which brief and which item. Confirmed errors are fixed in the next run.</p>

    <h2 id="privacy">Privacy and storage</h2>
    <p><em>Last updated: 14 November 2026</em></p>
    <ul>
      <li><strong>Cookies:</strong> this site sets none.</li>
      <li><strong>Browser storage:</strong> the only item stored is <code>cookie-consent</code> in your browser's localStorage. It is necessary: it remembers that you have seen the notice at the bottom of the page, so it does not keep appearing. localStorage is shared with other projects on the same address (spencertownley-sys.github.io), which store their own items separately. You can clear it in your browser settings at any time.</li>
      <li><strong>Other companies:</strong> your browser contacts none. Every file on this site, including the search index, is served from this site.</li>
      <li><strong>Search:</strong> search runs in your browser. The words you type are not sent anywhere.</li>
      <li><strong>Hosting:</strong> the site is hosted on GitHub Pages (GitHub, Inc., United States), which logs visitor IP addresses and browser details to deliver pages and keep the service secure. The site owner does not receive or use those logs. See <a href="{GITHUB_PRIVACY_URL}">GitHub's privacy statement</a>.</li>
      <li><strong>Social Pulse:</strong> if the Social Pulse pages are in use, they show short excerpts of public social posts without handles. Anyone can ask for a post to be removed by <a href="{ISSUES_URL}">opening an issue</a>.</li>
      <li><strong>Who is responsible:</strong> [PLACEHOLDER: owner legal name, city, state and country]. Contact: [PLACEHOLDER: contact email].</li>
      <li><strong>How long data is kept:</strong> this site keeps no personal data of its own. [PLACEHOLDER: owner to state retention for anything else, for example enquiries received by email.]</li>
      <li><strong>Your rights:</strong> visitors in the EU, the UK, California and other US states can ask to access, correct or delete personal information, and EU and UK visitors can complain to their data protection authority. This site does not sell or share personal information, and it honours Global Privacy Control signals because it does no tracking at all. To use a right, email [PLACEHOLDER: contact email]. You will get an answer within 30 days.</li>
      <li><strong>Children:</strong> this site is not directed at children under 13 (under 16 in the EU and UK) and does not knowingly collect their data.</li>
    </ul>

    <h2 id="who-runs-it">Who runs it</h2>
    <p>{escape(SITE_NAME)} is run by its repository owner, <a href="{OWNER_URL}">spencertownley-sys on GitHub</a>. The code that builds the site is public in the <a href="https://github.com/spencertownley-sys/CruiseNewsYouCanUse">repository</a>.</p>
  </div>
</article>"""
    return render_page(f"About {SITE_NAME}",
                       "How Cruise News You Can Use is made, what it is not, how to report a correction, and how privacy is handled.",
                       "", content, path="about.html")


PODCAST_SOURCE_PAGE = ROOT / "podcast-source.html"

PODCAST_SOURCE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — podcast source</title>
<meta name="robots" content="noindex">
</head>
<body>
<p><em>Paste this page's URL into NotebookLM as a source, then generate an Audio Overview. This
page always holds the latest weekday brief — re-add it each morning to get today's content.</em></p>
<h1>{title}</h1>
{window_html}
{top3_html}
{body_html}
{watch_next_html}
</body>
</html>
"""


def render_podcast_source_page(parsed):
    """A minimal, nav-free rendering of the latest brief at a fixed URL,
    meant to be pasted into NotebookLM as an Audio Overview source — see
    the README's 'Daily podcast' section."""
    window_html = f"<p><em>{escape(parsed['window'])}</em></p>" if parsed["window"] else ""
    top3_html = f"<h2>Top 3</h2>\n{parsed['top3_html']}" if parsed["top3_html"] else ""
    watch_next_html = f"<h2>Watch next</h2>\n{parsed['watch_next_html']}" if parsed["watch_next_html"] else ""
    return PODCAST_SOURCE_TEMPLATE.format(
        title=escape(parsed["title"]),
        window_html=window_html,
        top3_html=top3_html,
        body_html=parsed["body_html"],
        watch_next_html=watch_next_html,
    )


def social_strip():  # not used while Social Pulse is stale (CN-05); re-add to render_sidebar when the run works again
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


def week_start(d):
    """Monday of the week containing date d."""
    return d - datetime.timedelta(days=d.weekday())


def fmt_range(start, end):
    if start.month == end.month:
        return f"{start.strftime('%b')} {start.day}–{end.day}"
    return f"{start.strftime('%b')} {start.day} – {end.strftime('%b')} {end.day}"


def sidebar_item(e, current=False):
    d = datetime.date.fromisoformat(e["date"])
    cls = ' class="current"' if current else ""
    label = d.strftime("%a %b %-d") + (" · on this page" if current else "")
    return f"""<li{cls}><a href="{e['url']}">
  <span class="side-day">{label}</span>
  <span class="side-excerpt">{escape(html_unescape(e.get('excerpt', '')))}</span>
</a></li>"""


def render_sidebar(entries, latest):
    """Right-hand rail: this week's other briefs listed individually, every
    earlier week collapsed into one expandable group."""
    latest_week = week_start(datetime.date.fromisoformat(latest["date"]))
    this_week = [e for e in entries
                 if week_start(datetime.date.fromisoformat(e["date"])) == latest_week]
    older = [e for e in entries
             if week_start(datetime.date.fromisoformat(e["date"])) < latest_week]

    week_items = "".join(sidebar_item(e, current=(e["date"] == latest["date"])) for e in this_week)
    if len(this_week) == 1:
        week_items += '<li class="side-note">First brief of the week.</li>'

    groups = {}
    for e in older:
        groups.setdefault(week_start(datetime.date.fromisoformat(e["date"])), []).append(e)
    older_html = ""
    for ws in sorted(groups, reverse=True):
        items = groups[ws]
        n = len(items)
        items_html = "".join(sidebar_item(e) for e in items)
        older_html += f"""<details class="side-week">
  <summary><span>Week of {fmt_range(ws, ws + datetime.timedelta(days=4))}</span><span class="side-count">{n} brief{"s" if n != 1 else ""}</span></summary>
  <ul class="side-list">{items_html}</ul>
</details>"""
    older_block = f"""<h2 class="side-heading">Earlier weeks</h2>
{older_html}""" if older_html else ""

    return f"""<aside class="home-sidebar" aria-label="More briefs">
  <section class="side-block">
    <h2 class="side-heading">This week <span class="muted">{fmt_range(latest_week, latest_week + datetime.timedelta(days=4))}</span></h2>
    <ul class="side-list">{week_items}</ul>
  </section>
  <section class="side-block">
    {older_block}
  </section>
</aside>"""


def render_index_page(entries):
    entries = sorted(entries, key=lambda x: x["date"], reverse=True)
    if not entries:
        content = '<p class="empty-state">No posts yet — check back after the next weekday brief.</p>'
        return render_page(SITE_NAME, SITE_TAGLINE, "", content, path="")

    latest = entries[0]
    src = DATA_DIR / f"{latest['date']}.md"
    parsed = parse_source(src.read_text())

    window_html = f'<p class="post-window">{escape(parsed["window"])}</p>' if parsed["window"] else ""
    top3_block = f"""<div class="top-stories" id="top-3">
  <h2>Top 3</h2>
  {parsed['top3_html']}
</div>""" if parsed["top3_html"] else ""
    watch_block = f"""<div class="watch-next" id="watch-next">
  <h2>Watch next</h2>
  {parsed['watch_next_html']}
</div>""" if parsed["watch_next_html"] else ""

    count = len(entries)
    oldest = entries[-1]["display_date"]
    archive_note = f'{count} daily brief{"s" if count != 1 else ""} on file since {oldest}, all searchable.'

    content = f"""<section class="search" id="search">
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
<div class="home-grid">
  <article class="featured-brief" data-hide-on-search>
    <div class="post-header">
      <p class="post-date"><span class="latest-tag">Latest brief</span> <time datetime="{latest['date']}">{latest['display_date']}</time></p>
      <h1><a href="{latest['url']}">{escape(parsed['title'])}</a></h1>
      {window_html}
    </div>
    {top3_block}
    <div class="post-body">
    {parsed['body_html']}
    </div>
    {watch_block}
    <p class="permalink"><a href="{latest['url']}">Permanent link to this brief &rarr;</a></p>
  </article>
  {render_sidebar(entries, latest)}
</div>
<script src="assets/search.js" defer></script>"""

    page = render_page(SITE_NAME, SITE_TAGLINE, "", content, path="")
    return page.replace("<main>", '<main class="home">', 1)


def build_entry(parsed, post_date):
    """Manifest entry for one post: dates, URL and a short plain-text excerpt."""
    date_iso = post_date.isoformat()
    excerpt_source = parsed["top3_html"] or parsed["body_html"]
    excerpt_text = re.sub(r"<[^>]+>", " ", excerpt_source)
    excerpt_text = html_unescape(re.sub(r"\s+", " ", excerpt_text).strip())
    excerpt = (excerpt_text[:180] + "…") if len(excerpt_text) > 180 else excerpt_text
    return {
        "date": date_iso,
        "display_date": post_date.strftime("%A, %B %-d, %Y"),
        "title": parsed["title"],
        "window": parsed["window"],
        "excerpt": excerpt,
        "url": f"posts/{date_iso}.html",
    }


SITEMAP = ROOT / "sitemap.xml"
NOT_FOUND_PAGE = ROOT / "404.html"


def render_404_page():
    """404.html for GitHub Pages: absolute links so it works from any missing path."""
    base = "/CruiseNewsYouCanUse/"
    content = f"""<article class="about">
  <div class="post-header">
    <h1>Page not found</h1>
    <p class="post-window">That address does not exist, or the page has moved.</p>
  </div>
  <div class="post-body">
    <p>Try the <a href="{base}">latest brief</a> or <a href="{base}index.html#search">search every brief</a>.</p>
  </div>
</article>"""
    return render_page(f"Page not found — {SITE_NAME}", "Page not found.", base, content, noindex=True)


def write_sitemap(entries):
    """sitemap.xml from the manifest: home, About and every post (not social, podcast or 404)."""
    urls = [(SITE_URL, None), (SITE_URL + "about.html", None)]
    urls += [(SITE_URL + e["url"], e["date"]) for e in sorted(entries, key=lambda x: x["date"], reverse=True)]
    rows = "".join(
        f"  <url><loc>{escape(u)}</loc>" + (f"<lastmod>{d}</lastmod>" if d else "") + "</url>\n"
        for u, d in urls)
    SITEMAP.write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                       '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + rows + "</urlset>\n")


def rebuild_all():
    """Regenerate every page from data/<date>.md: all posts, the home page,
    the podcast source page and the search index. Used after template or
    sanitiser changes so no page is left on an old template."""
    entries = []
    latest_parsed = None
    for md_file in sorted(DATA_DIR.glob("????-??-??.md")):
        parsed = parse_source(md_file.read_text())
        post_date = datetime.date.fromisoformat(md_file.stem)
        entry = build_entry(parsed, post_date)
        entries.append(entry)
        POSTS_DIR.mkdir(exist_ok=True)
        (POSTS_DIR / f"{md_file.stem}.html").write_text(render_post_page(entry, parsed))
        latest_parsed = parsed
    save_manifest(entries)
    (ROOT / "index.html").write_text(render_index_page(entries))
    if latest_parsed is not None:
        PODCAST_SOURCE_PAGE.write_text(render_podcast_source_page(latest_parsed))
    ABOUT_PAGE.write_text(render_about_page())
    write_sitemap(entries)
    NOT_FOUND_PAGE.write_text(render_404_page())
    n_records = rebuild_search_index(entries)
    print(f"Rebuilt {len(entries)} post page(s), index.html, data/posts.json and data/search.json ({n_records} searchable sections)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", help="Path to the source Markdown file for this post")
    ap.add_argument("--date", help="ISO date (YYYY-MM-DD) for this post; inferred from title if omitted")
    ap.add_argument("--rebuild-all", action="store_true",
                    help="Regenerate every page from data/*.md instead of adding one post")
    args = ap.parse_args()

    if args.rebuild_all:
        rebuild_all()
        return
    if not args.md:
        ap.error("--md is required unless --rebuild-all is given")

    src_path = Path(args.md)
    md_text = src_path.read_text()
    parsed = parse_source(md_text)

    if args.date:
        post_date = datetime.date.fromisoformat(args.date)
    else:
        post_date = extract_date_from_title(parsed["title"], datetime.date.today())

    date_iso = post_date.isoformat()
    slug = date_iso

    entries = load_manifest()
    entries = [e for e in entries if e["date"] != date_iso]  # replace if re-publishing same date
    entry = build_entry(parsed, post_date)
    entries.append(entry)
    save_manifest(entries)

    POSTS_DIR.mkdir(exist_ok=True)
    post_html = render_post_page(entry, parsed)
    (POSTS_DIR / f"{slug}.html").write_text(post_html)

    index_html = render_index_page(entries)
    (ROOT / "index.html").write_text(index_html)

    PODCAST_SOURCE_PAGE.write_text(render_podcast_source_page(parsed))
    ABOUT_PAGE.write_text(render_about_page())
    write_sitemap(entries)
    NOT_FOUND_PAGE.write_text(render_404_page())

    n_records = rebuild_search_index(entries)

    print(f"Wrote posts/{slug}.html")
    print(f"Updated index.html ({len(entries)} post(s) total)")
    print(f"Updated data/posts.json")
    print(f"Updated podcast-source.html (paste its URL into NotebookLM for today's Audio Overview)")
    print(f"Rebuilt data/search.json ({n_records} searchable sections)")


if __name__ == "__main__":
    main()
