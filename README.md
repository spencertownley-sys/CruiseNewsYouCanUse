# Cruise News You Can Use

Daily updates on the major cruise lines and the rest of the cruise world — Carnival Corporation
(Carnival, Princess, Holland America, Seabourn), Norwegian Cruise Line Holdings, Royal Caribbean
Group, and the wider industry: earnings, bookings, itinerary changes, incidents, and what's
trending with cruisers online. Every day's brief is kept on the site and is full-text searchable
from the homepage.

This site is generated automatically. A scheduled run produces a digest, converts it into a
post with `scripts/publish_post.py`, and pushes the result here.

**Live site:** https://spencertownley-sys.github.io/CruiseNewsYouCanUse/
(enable GitHub Pages under Settings → Pages → Deploy from branch `main` / root, if it isn't
already on.)

## How a new post gets added

1. The day's digest is written as Markdown to `data/<YYYY-MM-DD>.md`, following the shape
   shown in `scripts/publish_post.py`'s docstring: an `# H1` title, an optional italic
   "covering ..." subtitle, a `## Top 3` numbered list, one `##`/`###` section per topic
   (write `Nothing new.` as the entire content of a section with nothing to report), and an
   optional closing `## Watch next` section.
2. Run:
   ```
   python3 scripts/publish_post.py --md data/<YYYY-MM-DD>.md --date <YYYY-MM-DD>
   ```
   This renders `posts/<YYYY-MM-DD>.html`, rewrites `index.html`, updates
   `data/posts.json` (the manifest the homepage is built from), and rebuilds
   `data/search.json` (the full-text search index, one record per section of every post).
3. Commit and push:
   ```
   git add -A
   git commit -m "Add <YYYY-MM-DD> cruise brief"
   git push
   ```

## Social Pulse (social listening)

`social.html` shows what cruisers are saying across X, Threads, Bluesky, Instagram and Facebook:
each post is rated positive, neutral or negative, and the page gives a net sentiment score
(positives minus negatives as a share of all posts, from -100 to +100) overall, per brand, per
platform, and for any ship or destination that stands out, plus the focus topics being watched.
One page per day is kept under `social/`, with a rolling trend per brand.

**Backend controls** are in `config/social_listening.json`. Edit it to steer the daily run:

| Section | What it controls |
|---|---|
| `platforms` | Which of the five networks are gathered, and a weight per platform for the weighted score |
| `queries.templates` | The search queries run on every platform each day (`{site}` becomes `site:x.com` etc.) |
| `brands` | Each brand's aliases, handles and hashtags, and its list of ships (a ship mention attributes the post to its brand) |
| `destinations` | Ports, regions and private islands to recognise, with aliases |
| `focus.topics` | Topics to watch; `highlight: true` pins a topic to the top of the page and makes the run search for it explicitly, `boost` raises its rank |
| `muted_keywords` | Posts containing these are dropped (job ads, giveaways, spam) |
| `standouts` | How many mentions and how strong a tilt a ship or destination needs before it is called out |
| `scoring` | How many sample posts to show per brand, excerpt length |

**Pipeline** (`scripts/social_listening.py`):

```
python3 scripts/social_listening.py template --date 2026-10-05       # posts file + the queries to run
python3 scripts/social_listening.py fetch    --date 2026-10-05       # Bluesky public API; X API if X_BEARER_TOKEN is set
python3 scripts/social_listening.py hydrate  --date 2026-10-05 --urls urls.txt   # add post URLs (+ ratings), fill X posts
python3 scripts/social_listening.py run      --date 2026-10-05       # score + render social.html and social/<date>.html
```

`urls.txt` is one post URL per line, optionally followed by `| positive | reason`. The daily
routine rates each post itself; any post left unrated falls back to a small cruise-aware word
list. X posts are filled in automatically (text, date, likes) through X's public embed endpoint;
dates for X, Threads and Instagram posts are decoded from their ids, so anything outside the
day's window is dropped. Threads, Instagram, Facebook and Bluesky text comes from search results
or page fetches, since those networks have no public read API. Outputs are kept in
`data/social/` (`<date>.posts.json` rated posts, `<date>.json` report, `history.json`,
`latest.json`, which also feeds the homepage strip).

## Daily podcast (manual, via NotebookLM)

Each weekday's brief can become a 5–15 minute two-host "Audio Overview" podcast episode
through Google's consumer NotebookLM (now called Gemini Notebook) — no extra cost beyond a
Google AI Pro/Plus subscription, which already includes it.

This repo always publishes `podcast-source.html` — a plain, nav-free rendering of the
**latest** weekday brief at one fixed URL:
https://spencertownley-sys.github.io/CruiseNewsYouCanUse/podcast-source.html

NotebookLM's website sources are a one-time snapshot, not a live feed, so the daily routine
is:
1. Open your "Cruise Brief Podcast" notebook in NotebookLM (create it once).
2. Add a source → Website → paste the URL above. (Remove the previous day's source first,
   or just leave it — the new one is what matters, and old ones don't hurt.)
3. Click **Generate Audio Overview**. Google AI Pro allows 20/day, so this is nowhere close
   to the limit.
4. Listen in NotebookLM, or use its share link — no need to upload the file anywhere.

Takes about a minute. Every time `publish_post.py` runs it overwrites `podcast-source.html`
with that day's content, so the same bookmarked URL always has the latest brief.

## Structure

```
index.html          Homepage — newest posts first, regenerated by the script
posts/*.html         One page per weekday brief
assets/style.css     Shared site styling
assets/search.js     Client-side search over data/search.json (no server needed)
social.html          Social Pulse, latest day (+ social/<date>.html per day)
config/social_listening.json   Backend controls for the social listening run
scripts/social_listening.py    Gathers, rates, scores and renders the Social Pulse
data/*.md            Source markdown for each post (kept for the record / re-runs)
data/posts.json      Manifest the homepage is built from
data/search.json     Search index, rebuilt from every data/*.md on each publish
podcast-source.html   Latest brief, nav-free — paste this URL into NotebookLM (see "Daily podcast")
scripts/publish_post.py   Turns a day's markdown into a styled post + updated homepage
```

## Design

Editorial look: navy/teal masthead, serif body text, card-based homepage, a highlighted
"Top 3" callout per post, and a closing "Watch next" box. Ticker symbols written as `[CCL]`
are automatically styled as small badges.

## Search

The homepage has a search box that runs entirely in the browser: it loads `data/search.json`
and matches every word you type against every section of every brief, newest first, with
snippets that link straight to the matching section. `index.html?q=princess` opens the
homepage with a search already run.
