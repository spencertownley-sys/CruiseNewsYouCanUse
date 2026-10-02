#!/usr/bin/env python3
"""
Social Pulse: social listening for Cruise News You Can Use.

Reads config/social_listening.json (the backend controls), takes a day's
gathered posts from X, Threads, Bluesky, Instagram and Facebook, rates each
post positive / neutral / negative, and produces:

  data/social/<date>.posts.json   the rated posts (kept as the record)
  data/social/<date>.json         the day's scored report
  data/social/history.json        rolling per-day, per-brand scores
  data/social/latest.json         copy of the newest report
  social.html                     the Social Pulse page (latest day + archive)
  social/<date>.html              one page per day

Subcommands
-----------
  template --date YYYY-MM-DD
      Write an empty posts file for the day with the search window and the
      list of queries to run, so whoever gathers posts (the daily routine, or
      a person) has the exact shape to fill in.

  fetch --date YYYY-MM-DD
      Pull Bluesky posts for the configured queries through Bluesky's public
      search API and merge them into the day's posts file. The other four
      platforms have no public API; the daily routine gathers them via web
      search (site:x.com, site:threads.net, site:instagram.com,
      site:facebook.com) and appends them to the same file. If the network
      blocks public.api.bsky.app this prints a notice and exits cleanly.

  hydrate --date YYYY-MM-DD [--urls urls.txt]
      Add posts from a plain list of post URLs (one per line, optionally
      followed by ' | positive|neutral|negative | reason') and fill in the
      text, date, author and likes of X posts through X's public embed
      endpoint. Dates for X, Threads and Instagram posts are decoded from the
      post id when missing, so posts outside the window are dropped at scoring.

  score --date YYYY-MM-DD
      Rate any post that has no 'sentiment' yet (a small cruise-aware lexicon;
      the routine normally rates posts itself and the lexicon is the fallback),
      attribute every post to brands / ships / destinations / focus topics,
      compute net sentiment scores, pick standouts, and write the report.

  render [--date YYYY-MM-DD]
      Build social.html and social/<date>.html from the report(s).

  run --date YYYY-MM-DD
      score + render.

Posts file shape (data/social/<date>.posts.json)
-------------------------------------------------
{
  "date": "2026-10-02",
  "window": {"since": "2026-10-01T15:00:00Z", "until": "2026-10-02T15:00:00Z"},
  "posts": [
    {
      "id": "x:1234567890",            # any stable id; "<platform>:<post id>"
      "platform": "x",                 # x | threads | bluesky | instagram | facebook
      "url": "https://x.com/.../status/1234567890",
      "author": "@handle or page name",
      "posted_at": "2026-10-02T03:14:00Z",   # ISO 8601 or null if unknown
      "text": "the post text (captions for Instagram)",
      "sentiment": "negative",         # positive | neutral | negative | null
      "sentiment_reason": "guest says the port was skipped with no refund",
      "engagement": {"likes": 120, "reposts": 14, "replies": 33},   # optional
      "brands": ["princess"],          # optional override; otherwise inferred
      "ships": ["Grand Princess"],     # optional override; otherwise inferred
      "destinations": ["Mexican Riviera"]   # optional override; otherwise inferred
    }
  ]
}

Net sentiment = (positive - negative) / total * 100, from -100 to +100.
"""

import argparse
import datetime as dt
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "social_listening.json"
SOCIAL_DIR = ROOT / "data" / "social"
PAGES_DIR = ROOT / "social"
HISTORY = SOCIAL_DIR / "history.json"
LATEST = SOCIAL_DIR / "latest.json"

PLATFORM_ORDER = ["x", "threads", "bluesky", "instagram", "facebook"]
SENTIMENTS = ("positive", "neutral", "negative")


# --------------------------------------------------------------------------
# Config and helpers
# --------------------------------------------------------------------------

def load_config():
    if not CONFIG_PATH.exists():
        sys.exit(f"Missing {CONFIG_PATH.relative_to(ROOT)}")
    return json.loads(CONFIG_PATH.read_text())


def posts_path(date):
    return SOCIAL_DIR / f"{date}.posts.json"


def report_path(date):
    return SOCIAL_DIR / f"{date}.json"


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def window_for(date, cfg):
    """Search window ending at 15:00 UTC on the given date (just after the
    7:50am PT run), 24h on Tue-Fri, 72h on Monday."""
    day = dt.date.fromisoformat(date)
    until = dt.datetime(day.year, day.month, day.day, 15, 0, 0)
    hours = cfg.get("monday_window_hours", 72) if day.weekday() == 0 else cfg.get("window_hours", 24)
    since = until - dt.timedelta(hours=hours)
    return {"since": iso(since), "until": iso(until), "hours": hours}


def phrase_regex(phrase):
    """Whole-word, case-insensitive match for a phrase; handles @, #, & and dots."""
    esc = re.escape(phrase.strip())
    esc = esc.replace(r"\ ", r"\s+")
    lead = r"(?<![\w@#])" if phrase[0].isalnum() else r"(?<!\w)"
    tail = r"(?![\w])" if phrase[-1].isalnum() else ""
    return re.compile(lead + esc + tail, re.I)


def net_score(pos, neg, total):
    if not total:
        return 0
    return round((pos - neg) / total * 100)


def excerpt(text, n):
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


# --------------------------------------------------------------------------
# Fallback lexicon sentiment (the routine rates posts itself; this covers
# anything it left unrated so a run never stalls)
# --------------------------------------------------------------------------

POSITIVE_PHRASES = [
    "best cruise", "best vacation", "can't wait", "cant wait", "cannot wait", "highly recommend",
    "would recommend", "loved it", "love this ship", "amazing crew", "great service", "top notch",
    "worth every penny", "10/10", "five stars", "5 stars", "bucket list", "trip of a lifetime",
    "already booked", "booked again", "counting down", "so excited", "exceeded expectations",
    "well done", "thank you", "shout out", "shoutout", "blown away", "absolutely stunning",
]
NEGATIVE_PHRASES = [
    "never again", "worst cruise", "worst experience", "never cruising", "stay away", "do not book",
    "don't book", "waste of money", "rip off", "ripoff", "rip-off", "false advertising", "no refund",
    "still waiting", "on hold for", "nickel and dime", "nickel-and-dime", "went downhill", "gone downhill",
    "cut corners", "food poisoning", "got sick", "so disappointed", "very disappointed",
    "missed our port", "skipped our port", "cancelled our", "canceled our", "lost our luggage",
    "class action", "lawsuit", "hidden fees", "price gouging", "bait and switch",
]
POSITIVE_WORDS = {
    "amazing", "awesome", "beautiful", "best", "brilliant", "delicious", "delightful", "enjoyed",
    "excellent", "excited", "fabulous", "fantastic", "favorite", "favourite", "flawless", "fun",
    "gorgeous", "great", "happy", "impressed", "incredible", "love", "loved", "lovely", "magical",
    "memorable", "outstanding", "perfect", "phenomenal", "pleased", "recommend", "relaxing",
    "smooth", "spectacular", "stunning", "superb", "thrilled", "unforgettable", "wonderful", "wow",
    "yay", "grateful", "friendly", "attentive", "spotless", "clean", "upgrade", "upgraded",
}
NEGATIVE_WORDS = {
    "angry", "annoyed", "appalling", "awful", "bad", "broken", "cancelled", "canceled", "chaos",
    "complaint", "complaints", "cramped", "dirty", "disappointed", "disappointing", "disaster",
    "disgusting", "embarrassing", "fail", "failed", "filthy", "frustrated", "frustrating",
    "garbage", "greedy", "gross", "horrible", "horrendous", "ignored", "infuriating", "issue",
    "issues", "lousy", "mediocre", "mess", "miserable", "mold", "nightmare", "noisy", "norovirus",
    "outbreak", "overpriced", "pathetic", "poor", "problem", "problems", "refund", "ridiculous",
    "rude", "ruined", "sad", "scam", "shameful", "sick", "slow", "stranded", "stuck", "terrible",
    "unacceptable", "underwhelming", "unhappy", "upset", "useless", "waste", "worse", "worst",
    "delayed", "delay", "evacuated", "injured", "overboard", "rerouted", "diverted",
}
NEGATORS = {"not", "no", "never", "nothing", "nobody", "hardly", "barely", "isn't", "wasn't",
            "aren't", "weren't", "don't", "doesn't", "didn't", "can't", "cannot", "couldn't",
            "won't", "wouldn't", "ain't", "without"}


def lexicon_sentiment(text):
    """Return (label, score, reason). Phrases first, then words with simple
    negation (a negator within the two preceding words flips the word)."""
    t = (text or "").lower()
    score = 0
    hits = []
    for p in POSITIVE_PHRASES:
        if p in t:
            score += 2
            hits.append(f"+{p}")
    for p in NEGATIVE_PHRASES:
        if p in t:
            score -= 2
            hits.append(f"-{p}")
    words = re.findall(r"[a-z'’]+", t.replace("’", "'"))
    for i, w in enumerate(words):
        polarity = 0
        if w in POSITIVE_WORDS:
            polarity = 1
        elif w in NEGATIVE_WORDS:
            polarity = -1
        if not polarity:
            continue
        if any(prev in NEGATORS for prev in words[max(0, i - 2):i]):
            polarity = -polarity
            hits.append(("+" if polarity > 0 else "-") + "not " + w)
        else:
            hits.append(("+" if polarity > 0 else "-") + w)
        score += polarity
    if score > 0:
        label = "positive"
    elif score < 0:
        label = "negative"
    else:
        label = "neutral"
    reason = "lexicon: " + (", ".join(hits[:6]) if hits else "no sentiment cues")
    return label, score, reason


# --------------------------------------------------------------------------
# Attribution
# --------------------------------------------------------------------------

class Matcher:
    def __init__(self, cfg):
        self.cfg = cfg
        self.brand_by_key = {b["key"]: b for b in cfg["brands"]}
        self.brand_patterns = [(b["key"], phrase_regex(a)) for b in cfg["brands"] for a in b.get("aliases", [])]
        self.ship_patterns = [(b["key"], s, phrase_regex(s)) for b in cfg["brands"] for s in b.get("ships", [])]
        self.dest_patterns = [(d["name"], phrase_regex(a)) for d in cfg["destinations"] for a in d.get("aliases", [])]
        self.focus_patterns = [(t["label"], phrase_regex(k)) for t in cfg["focus"]["topics"] for k in t.get("keywords", [])]
        self.muted = [phrase_regex(k) for k in cfg.get("muted_keywords", [])]

    def is_muted(self, text):
        return any(p.search(text) for p in self.muted)

    def attribute(self, post):
        text = f"{post.get('text', '')} {post.get('author', '')}"
        brands = list(post.get("brands") or [])
        ships = list(post.get("ships") or [])
        dests = list(post.get("destinations") or [])
        if not ships:
            for key, ship, pat in self.ship_patterns:
                if pat.search(text):
                    ships.append(ship)
                    if key not in brands:
                        brands.append(key)
        else:
            for key, ship, _ in self.ship_patterns:
                if ship in ships and key not in brands:
                    brands.append(key)
        if not post.get("brands"):
            for key, pat in self.brand_patterns:
                if key not in brands and pat.search(text):
                    brands.append(key)
        if not dests:
            for name, pat in self.dest_patterns:
                if name not in dests and pat.search(text):
                    dests.append(name)
        topics = []
        for label, pat in self.focus_patterns:
            if label not in topics and pat.search(text):
                topics.append(label)
        return brands, ships, dests, topics


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def bucket():
    return {"posts": 0, "positive": 0, "neutral": 0, "negative": 0, "weighted": 0.0, "weighted_total": 0.0}


def add(bk, sentiment, weight):
    bk["posts"] += 1
    bk[sentiment] += 1
    bk["weighted_total"] += weight
    if sentiment == "positive":
        bk["weighted"] += weight
    elif sentiment == "negative":
        bk["weighted"] -= weight


def finish(bk):
    bk["net_score"] = net_score(bk["positive"], bk["negative"], bk["posts"])
    bk["weighted_score"] = round(bk["weighted"] / bk["weighted_total"] * 100) if bk["weighted_total"] else 0
    bk.pop("weighted", None)
    bk.pop("weighted_total", None)
    return bk


def sample_posts(posts, n, prefer=("negative", "positive", "neutral")):
    """Pick up to n posts, most-engaged first within each sentiment, so the
    samples show the extremes before the middle."""
    def engagement(p):
        e = p.get("engagement") or {}
        return sum(int(e.get(k, 0) or 0) for k in ("likes", "reposts", "replies"))
    picked = []
    for s in prefer:
        pool = sorted([p for p in posts if p["sentiment"] == s], key=engagement, reverse=True)
        picked.extend(pool[: max(1, n // 2)])
    seen = set()
    out = []
    for p in picked:
        if p["id"] in seen:
            continue
        seen.add(p["id"])
        out.append(p)
        if len(out) >= n:
            break
    return out


def slim(post, cfg):
    return {
        "id": post["id"],
        "platform": post["platform"],
        "url": post.get("url"),
        "author": post.get("author"),
        "posted_at": post.get("posted_at"),
        "excerpt": excerpt(post.get("text", ""), cfg["scoring"].get("excerpt_chars", 160)),
        "sentiment": post["sentiment"],
        "sentiment_reason": post.get("sentiment_reason"),
        "rated_by": post.get("rated_by", "model"),
        "brands": post.get("brands", []),
        "ships": post.get("ships", []),
        "destinations": post.get("destinations", []),
        "topics": post.get("topics", []),
    }


def score_day(date, cfg, data):
    matcher = Matcher(cfg)
    platforms = cfg["platforms"]
    enabled = {k for k, v in platforms.items() if v.get("enabled", True)}
    win = data.get("window") or window_for(date, cfg)

    posts = []
    dropped = {"muted": 0, "disabled_platform": 0, "empty": 0, "duplicate": 0}
    seen_ids = set()
    for p in data.get("posts", []):
        p = normalise_post(dict(p))
        if not (p.get("text") or "").strip():
            dropped["empty"] += 1
            continue
        if not in_window(p.get("posted_at"), win):
            dropped["outside_window"] = dropped.get("outside_window", 0) + 1
            continue
        plat = (p.get("platform") or "").lower()
        if plat not in enabled:
            dropped["disabled_platform"] += 1
            continue
        pid = p.get("id") or f"{plat}:{p.get('url')}"
        if pid in seen_ids:
            dropped["duplicate"] += 1
            continue
        seen_ids.add(pid)
        if matcher.is_muted(p.get("text", "")):
            dropped["muted"] += 1
            continue
        p = dict(p)
        p["id"] = pid
        p["platform"] = plat
        s = (p.get("sentiment") or "").lower()
        if s not in SENTIMENTS:
            label, _, reason = lexicon_sentiment(p.get("text", ""))
            p["sentiment"] = label
            p["sentiment_reason"] = p.get("sentiment_reason") or reason
            p["rated_by"] = "lexicon"
        else:
            p["sentiment"] = s
            p["rated_by"] = p.get("rated_by") or "model"
        brands, ships, dests, topics = matcher.attribute(p)
        p["brands"], p["ships"], p["destinations"], p["topics"] = brands, ships, dests, topics
        posts.append(p)

    totals = bucket()
    by_platform = {k: bucket() for k in PLATFORM_ORDER if k in enabled}
    by_brand = {}
    by_ship = {}
    by_dest = {}
    by_topic = {}
    unattributed = bucket()

    for p in posts:
        w = float(platforms.get(p["platform"], {}).get("weight", 1.0))
        s = p["sentiment"]
        add(totals, s, w)
        add(by_platform.setdefault(p["platform"], bucket()), s, w)
        if not p["brands"]:
            add(unattributed, s, w)
        for key in p["brands"]:
            b = by_brand.setdefault(key, {**bucket(), "platforms": {}, "_posts": []})
            add(b, s, w)
            add(b["platforms"].setdefault(p["platform"], bucket()), s, w)
            b["_posts"].append(p)
        for ship in p["ships"]:
            sb = by_ship.setdefault(ship, {**bucket(), "_posts": []})
            add(sb, s, w)
            sb["_posts"].append(p)
        for d in p["destinations"]:
            db = by_dest.setdefault(d, {**bucket(), "_posts": []})
            add(db, s, w)
            db["_posts"].append(p)
        for t in p["topics"]:
            tb = by_topic.setdefault(t, {**bucket(), "_posts": []})
            add(tb, s, w)
            tb["_posts"].append(p)

    st = cfg["standouts"]
    n_samples = cfg["scoring"].get("sample_posts_per_brand", 3)

    def is_standout(bk):
        return bk["posts"] >= st["min_mentions"] and (
            abs(bk["net_score"]) >= st["min_abs_score"] or bk["posts"] >= st["spike_mentions"])

    ship_brand = {s: b["key"] for b in cfg["brands"] for s in b.get("ships", [])}

    ships_out = []
    for name, bk in by_ship.items():
        ps = bk.pop("_posts")
        finish(bk)
        bk.update({"name": name, "brand": ship_brand.get(name), "standout": is_standout(bk),
                   "samples": [slim(p, cfg) for p in sample_posts(ps, 2)]})
        ships_out.append(bk)
    ships_out.sort(key=lambda x: (-x["standout"], -x["posts"], x["name"]))

    dests_out = []
    for name, bk in by_dest.items():
        ps = bk.pop("_posts")
        finish(bk)
        bk.update({"name": name, "standout": is_standout(bk),
                   "samples": [slim(p, cfg) for p in sample_posts(ps, 2)]})
        dests_out.append(bk)
    dests_out.sort(key=lambda x: (-x["standout"], -x["posts"], x["name"]))

    brands_out = []
    for b in sorted(cfg["brands"], key=lambda b: b.get("priority", 99)):
        bk = by_brand.get(b["key"])
        if not bk:
            continue
        ps = bk.pop("_posts")
        finish(bk)
        for pk in list(bk["platforms"]):
            finish(bk["platforms"][pk])
        bk.update({
            "key": b["key"], "name": b["name"], "group": b.get("group"),
            "ships": [s for s in ships_out if s["brand"] == b["key"]],
            "destinations": sorted(
                [{"name": d, **finish({**bucket(), **_sub(ps, lambda p, d=d: d in p["destinations"])})}
                 for d in {d for p in ps for d in p["destinations"]}],
                key=lambda x: -x["posts"])[:5],
            "samples": [slim(p, cfg) for p in sample_posts(ps, n_samples)],
        })
        brands_out.append(bk)

    topic_cfg = {t["label"]: t for t in cfg["focus"]["topics"]}
    topics_out = []
    for label, bk in by_topic.items():
        ps = bk.pop("_posts")
        finish(bk)
        tc = topic_cfg.get(label, {})
        bk.update({"label": label, "highlight": bool(tc.get("highlight")), "boost": float(tc.get("boost", 1.0)),
                   "rank": bk["posts"] * float(tc.get("boost", 1.0)),
                   "brands": sorted({b for p in ps for b in p["brands"]}),
                   "samples": [slim(p, cfg) for p in sample_posts(ps, 3)]})
        topics_out.append(bk)
    topics_out.sort(key=lambda x: (-x["highlight"], -x["rank"], x["label"]))

    finish(totals)
    finish(unattributed)
    for pk in by_platform:
        finish(by_platform[pk])

    report = {
        "date": date,
        "generated_at": iso(dt.datetime.utcnow()),
        "window": win,
        "config_version": cfg.get("version"),
        "totals": totals,
        "dropped": dropped,
        "rated_by": {"model": sum(1 for p in posts if p.get("rated_by") == "model"),
                     "lexicon": sum(1 for p in posts if p.get("rated_by") == "lexicon")},
        "platforms": {k: {**v, "label": platforms[k]["label"]} for k, v in by_platform.items()},
        "brands": brands_out,
        "unattributed": unattributed,
        "standouts": {
            "ships": [s for s in ships_out if s["standout"]][: st.get("max_listed", 8)],
            "destinations": [d for d in dests_out if d["standout"]][: st.get("max_listed", 8)],
        },
        "ships": ships_out,
        "destinations": dests_out,
        "topics": topics_out,
        "posts": [slim(p, cfg) for p in sorted(posts, key=lambda p: (p["platform"], p.get("posted_at") or ""))],
    }
    return report, posts


def _sub(posts, pred):
    bk = bucket()
    for p in posts:
        if pred(p):
            add(bk, p["sentiment"], 1.0)
    return bk


def update_history(report, cfg):
    hist = json.loads(HISTORY.read_text()) if HISTORY.exists() else {}
    hist[report["date"]] = {
        "posts": report["totals"]["posts"],
        "net_score": report["totals"]["net_score"],
        "brands": {b["key"]: {"posts": b["posts"], "net_score": b["net_score"]} for b in report["brands"]},
    }
    keep = cfg.get("history_days", 30)
    dates = sorted(hist)[-keep:]
    hist = {d: hist[d] for d in dates}
    HISTORY.write_text(json.dumps(hist, indent=2) + "\n")
    return hist



# --------------------------------------------------------------------------
# Post dating and hydration (no API keys needed)
# --------------------------------------------------------------------------

TWITTER_EPOCH_MS = 1288834974657
META_EPOCH_MS = 1314220021721
SHORTCODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

URL_PATTERNS = [
    ("x", re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/([^/]+)/status/(\d+)", re.I)),
    ("threads", re.compile(r"https?://(?:www\.)?threads\.(?:com|net)/@([^/]+)/(?:post|video)/([A-Za-z0-9_-]+)", re.I)),
    ("instagram", re.compile(r"https?://(?:www\.)?instagram\.com/(?:([^/]+)/)?(?:p|reel|reels)/([A-Za-z0-9_-]+)", re.I)),
    ("bluesky", re.compile(r"https?://bsky\.app/profile/([^/]+)/post/([A-Za-z0-9]+)", re.I)),
    ("facebook", re.compile(r"https?://(?:www\.|m\.)?facebook\.com/(.+)", re.I)),
]


def parse_url(url):
    """Return (platform, author, post_id) for a post URL, or (None, None, None)."""
    for plat, pat in URL_PATTERNS:
        m = pat.search(url or "")
        if m:
            if plat == "facebook":
                return plat, None, re.sub(r"[^A-Za-z0-9]+", "-", m.group(1)).strip("-")[:80]
            return plat, m.group(1), m.group(2)
    return None, None, None


def snowflake_date(post_id):
    """X status ids encode their creation time."""
    try:
        ms = (int(post_id) >> 22) + TWITTER_EPOCH_MS
        return iso(dt.datetime.utcfromtimestamp(ms / 1000))
    except (TypeError, ValueError):
        return None


def shortcode_date(code):
    """Instagram and Threads shortcodes encode the media id, which encodes
    its creation time."""
    try:
        n = 0
        for ch in code:
            n = n * 64 + SHORTCODE_ALPHABET.index(ch)
        ms = (n >> 23) + META_EPOCH_MS
        d = dt.datetime.utcfromtimestamp(ms / 1000)
        if d.year < 2011 or d > dt.datetime.utcnow() + dt.timedelta(days=1):
            return None
        return iso(d)
    except (TypeError, ValueError):
        return None


def date_from_id(platform, post_id):
    if platform == "x":
        return snowflake_date(post_id)
    if platform in ("threads", "instagram"):
        return shortcode_date(post_id)
    return None


def hydrate_x(post_id):
    """Pull text, date, author and likes for a public X post through the
    embed endpoint (no credentials needed). Returns a dict or None."""
    url = f"https://cdn.syndication.twimg.com/tweet-result?id={post_id}&token=a"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 CruiseNewsYouCanUse"}), timeout=20) as r:
            d = json.loads(r.read().decode())
    except Exception:  # noqa: BLE001
        return None
    if not d or d.get("__typename") not in (None, "Tweet"):
        return None
    text = html.unescape(d.get("text", ""))
    user = d.get("user", {}) or {}
    created = d.get("created_at")
    if created:
        created = created.replace(".000Z", "Z")
    return {
        "text": text,
        "author": f"@{user.get('screen_name')}" if user.get("screen_name") else None,
        "posted_at": created,
        "engagement": {"likes": d.get("favorite_count", 0), "reposts": d.get("conversation_count", 0), "replies": 0},
    }


def normalise_post(p):
    """Fill platform / id / author / posted_at from the URL where missing."""
    plat, author, pid = parse_url(p.get("url", ""))
    if plat and not p.get("platform"):
        p["platform"] = plat
    if pid and not p.get("id"):
        p["id"] = f"{p.get('platform') or plat}:{pid}"
    if author and not p.get("author"):
        p["author"] = f"@{author}"
    if pid and not p.get("posted_at"):
        p["posted_at"] = date_from_id(p.get("platform") or plat, pid)
    return p


def in_window(posted_at, window):
    if not posted_at or not window:
        return True  # unknown dates are kept; the gatherer is trusted on the window
    try:
        return window["since"] <= posted_at[:19] + "Z" <= window["until"]
    except KeyError:
        return True


def hydrate(date, cfg, urls_file=None):
    """Add posts from a list of URLs (one per line, optional ' | sentiment | reason'
    after the URL) and fill in text/date/author for X posts via the embed
    endpoint. Threads, Instagram and Facebook have no public endpoint, so the
    gatherer supplies their text; their dates are decoded from the post id."""
    path = posts_path(date)
    if not path.exists():
        write_template(date, cfg)
    data = json.loads(path.read_text())
    existing = {p.get("id") for p in data["posts"]}
    added = 0
    if urls_file:
        for line in Path(urls_file).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [x.strip() for x in line.split("|")]
            url = parts[0]
            post = normalise_post({"url": url, "text": "", "sentiment": parts[1] if len(parts) > 1 else None,
                                   "sentiment_reason": parts[2] if len(parts) > 2 else None})
            if not post.get("platform"):
                print(f"skip (unrecognised url): {url}")
                continue
            if post["id"] in existing:
                continue
            existing.add(post["id"])
            data["posts"].append(post)
            added += 1
    filled = 0
    for p in data["posts"]:
        normalise_post(p)
        if p.get("platform") == "x" and not (p.get("text") or "").strip():
            pid = p["id"].split(":", 1)[-1]
            h = hydrate_x(pid)
            if h:
                p.update({k: v for k, v in h.items() if v})
                filled += 1
    out_of_window = [p["id"] for p in data["posts"] if not in_window(p.get("posted_at"), data.get("window"))]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"Added {added} post(s) from URLs, hydrated {filled} X post(s); {len(out_of_window)} outside the window "
          f"(dropped at scoring): {', '.join(out_of_window[:6])}")


def fetch_x_api(date, cfg, token):
    """Optional: X API v2 recent search when X_BEARER_TOKEN is set."""
    path = posts_path(date)
    if not path.exists():
        write_template(date, cfg)
    data = json.loads(path.read_text())
    existing = {p.get("id") for p in data["posts"]}
    limit = cfg["queries"].get("max_posts_per_platform", 40)
    added = 0
    for t in cfg["queries"]["templates"]:
        q = t.replace("{site}", "").strip() + " -is:retweet lang:en"
        params = urllib.parse.urlencode({
            "query": q, "max_results": 25, "start_time": data["window"]["since"], "end_time": data["window"]["until"],
            "tweet.fields": "created_at,public_metrics,author_id", "expansions": "author_id", "user.fields": "username"})
        req = urllib.request.Request(f"https://api.x.com/2/tweets/search/recent?{params}",
                                     headers={"Authorization": f"Bearer {token}", "User-Agent": "CruiseNewsYouCanUse/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                payload = json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001
            print(f"X API unavailable ({type(e).__name__}: {str(e)[:80]}); falling back to web search + hydrate.")
            return
        users = {u["id"]: u.get("username") for u in payload.get("includes", {}).get("users", [])}
        for tw in payload.get("data", []):
            pid = f"x:{tw['id']}"
            if pid in existing:
                continue
            existing.add(pid)
            m = tw.get("public_metrics", {})
            handle = users.get(tw.get("author_id"), "i")
            data["posts"].append({
                "id": pid, "platform": "x", "url": f"https://x.com/{handle}/status/{tw['id']}",
                "author": f"@{handle}", "posted_at": tw.get("created_at"), "text": tw.get("text", ""),
                "sentiment": None, "sentiment_reason": None,
                "engagement": {"likes": m.get("like_count", 0), "reposts": m.get("retweet_count", 0), "replies": m.get("reply_count", 0)},
            })
            added += 1
            if added >= limit:
                break
        if added >= limit:
            break
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"Added {added} X posts via the X API to {path.relative_to(ROOT)}")

# --------------------------------------------------------------------------
# Gathering helpers
# --------------------------------------------------------------------------

def write_template(date, cfg):
    SOCIAL_DIR.mkdir(parents=True, exist_ok=True)
    path = posts_path(date)
    if path.exists():
        print(f"{path.relative_to(ROOT)} already exists; leaving it alone.")
        return path
    win = window_for(date, cfg)
    queries = []
    for pk, pv in cfg["platforms"].items():
        if not pv.get("enabled", True):
            continue
        for t in cfg["queries"]["templates"]:
            queries.append({"platform": pk, "query": t.replace("{site}", f"site:{pv['site']}")})
    focus_queries = [{"platform": "all", "query": f"cruise {kw}"}
                     for t in cfg["focus"]["topics"] if t.get("highlight") for kw in t["keywords"][:2]]
    data = {
        "date": date,
        "window": {"since": win["since"], "until": win["until"]},
        "_instructions": (
            "Fill 'posts' with public posts published inside the window. Every post needs platform, url, "
            "author, text and a sentiment of positive / neutral / negative with a short sentiment_reason. "
            "Run the queries below (web search; Bluesky can also use `fetch`). Skip ads, job posts and "
            "giveaways. Keep the text as posted; the scorer makes the excerpts."),
        "_queries": queries + focus_queries,
        "_focus_highlights": [t["label"] for t in cfg["focus"]["topics"] if t.get("highlight")],
        "posts": [],
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {path.relative_to(ROOT)} ({len(queries) + len(focus_queries)} queries to run)")
    return path


def fetch_bluesky(date, cfg):
    path = posts_path(date)
    if not path.exists():
        write_template(date, cfg)
    data = json.loads(path.read_text())
    since = data["window"]["since"]
    until = data["window"]["until"]
    existing = {p.get("id") for p in data["posts"]}
    limit = cfg["queries"].get("max_posts_per_platform", 40)
    added = 0
    for t in cfg["queries"]["templates"]:
        q = t.replace("{site}", "").strip()
        params = urllib.parse.urlencode({"q": q, "limit": 25, "sort": "latest", "since": since, "until": until})
        url = f"https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?{params}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "CruiseNewsYouCanUse/1.0"}), timeout=20) as r:
                payload = json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001
            print(f"Bluesky fetch unavailable ({type(e).__name__}: {str(e)[:80]}). "
                  "Gather Bluesky posts via web search (site:bsky.app) instead.")
            return
        for item in payload.get("posts", []):
            rec = item.get("record", {})
            uri = item.get("uri", "")
            rkey = uri.rsplit("/", 1)[-1]
            handle = item.get("author", {}).get("handle", "")
            pid = f"bluesky:{rkey}"
            if pid in existing:
                continue
            existing.add(pid)
            data["posts"].append({
                "id": pid,
                "platform": "bluesky",
                "url": f"https://bsky.app/profile/{handle}/post/{rkey}",
                "author": f"@{handle}",
                "posted_at": rec.get("createdAt"),
                "text": rec.get("text", ""),
                "sentiment": None,
                "sentiment_reason": None,
                "engagement": {"likes": item.get("likeCount", 0), "reposts": item.get("repostCount", 0),
                               "replies": item.get("replyCount", 0)},
            })
            added += 1
            if added >= limit:
                break
        if added >= limit:
            break
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"Added {added} Bluesky posts to {path.relative_to(ROOT)}")


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def _page_template():
    sys.path.insert(0, str(ROOT / "scripts"))
    import publish_post  # noqa: E402  (shares the site header/footer)
    return publish_post.PAGE_TEMPLATE, publish_post.SITE_NAME, publish_post.SITE_TAGLINE


def e(s):
    return html.escape(str(s if s is not None else ""))


def score_class(score):
    if score >= 15:
        return "pos"
    if score <= -15:
        return "neg"
    return "neu"


def fmt_score(score):
    return f"{score:+d}" if score else "0"


def sentiment_bar(bk):
    """Diverging stacked bar centred on neutral: negative grows left from the
    centre, positive grows right, neutral straddles the centre. Every segment
    carries its count as text so colour never works alone."""
    total = bk["posts"] or 1
    # The track spans -100% .. +100% with the neutral midpoint at its centre,
    # so each share takes half its percentage of the track width and an
    # all-negative or all-positive bar fills exactly one half.
    neg_w = bk["negative"] / total * 50
    neu_w = bk["neutral"] / total * 50
    pos_w = bk["positive"] / total * 50
    left = 50 - neg_w - neu_w / 2
    return (
        f'<div class="sbar" role="img" aria-label="{bk["negative"]} negative, {bk["neutral"]} neutral, {bk["positive"]} positive">'
        f'<div class="sbar-track">'
        f'<span class="sbar-seg neg" style="left:{left:.2f}%;width:{neg_w:.2f}%" title="Negative: {bk["negative"]}">{bk["negative"] or ""}</span>'
        f'<span class="sbar-seg neu" style="left:{left + neg_w:.2f}%;width:{neu_w:.2f}%" title="Neutral: {bk["neutral"]}">{bk["neutral"] or ""}</span>'
        f'<span class="sbar-seg pos" style="left:{left + neg_w + neu_w:.2f}%;width:{pos_w:.2f}%" title="Positive: {bk["positive"]}">{bk["positive"] or ""}</span>'
        f'<span class="sbar-mid"></span>'
        f'</div></div>'
    )


def sentiment_chip(s):
    glyph = {"positive": "▲", "negative": "▼", "neutral": "●"}[s]
    return f'<span class="chip {s}">{glyph} {s}</span>'


def sample_html(samples, platforms):
    if not samples:
        return ""
    items = []
    for p in samples:
        plat = platforms.get(p["platform"], {}).get("label", p["platform"])
        link = f'<a href="{e(p["url"])}" rel="nofollow noopener" target="_blank">{e(plat)}</a>' if p.get("url") else e(plat)
        reason = f'<span class="why">{e(p["sentiment_reason"])}</span>' if p.get("sentiment_reason") else ""
        items.append(
            f'<li>{sentiment_chip(p["sentiment"])} <span class="who">{e(p.get("author") or "")}</span> on {link}'
            f'<span class="what">{e(p["excerpt"])}</span>{reason}</li>')
    return '<ul class="samples">' + "".join(items) + "</ul>"


def sparkline(series, width=140, height=36):
    """Tiny SVG line of net scores (-100..100) with a zero baseline and a
    <title> per point for hover."""
    pts = [(d, s) for d, s in series if s is not None]
    if len(pts) < 2:
        return ""
    n = len(pts)
    xs = [i * (width - 8) / (n - 1) + 4 for i in range(n)]
    ys = [height / 2 - s / 100 * (height / 2 - 4) for _, s in pts]
    path = " ".join(f"{'M' if i == 0 else 'L'}{xs[i]:.1f},{ys[i]:.1f}" for i in range(n))
    dots = "".join(
        f'<circle cx="{xs[i]:.1f}" cy="{ys[i]:.1f}" r="2.5"><title>{pts[i][0]}: {fmt_score(pts[i][1])}</title></circle>'
        for i in range(n))
    return (f'<svg class="spark" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="net score trend, {n} days">'
            f'<line class="zero" x1="0" x2="{width}" y1="{height / 2:.1f}" y2="{height / 2:.1f}"/>'
            f'<path d="{path}"/>{dots}</svg>')


def render_report(report, cfg, hist, all_dates, asset_prefix):
    tmpl, site_name, site_tagline = _page_template()
    platforms = cfg["platforms"]
    t = report["totals"]
    date = report["date"]
    day = dt.date.fromisoformat(date)
    display_date = day.strftime("%A, %B %-d, %Y")
    win = report["window"]
    try:
        _since = dt.datetime.strptime(win["since"], "%Y-%m-%dT%H:%M:%SZ")
        _until = dt.datetime.strptime(win["until"], "%Y-%m-%dT%H:%M:%SZ")
        hours = int((_until - _since).total_seconds() // 3600)
    except (KeyError, ValueError):
        hours = win.get("hours") or cfg.get("window_hours", 24)

    # ---- header + hero
    plat_chips = "".join(
        f'<li><span class="plat">{e(v["label"])}</span> <b>{v["posts"]}</b> <span class="muted">{fmt_score(v["net_score"])}</span></li>'
        for k, v in report["platforms"].items() if v["posts"])
    missing = [platforms[k]["label"] for k in platforms if platforms[k].get("enabled", True) and not report["platforms"].get(k, {}).get("posts")]
    missing_html = f'<p class="muted small">No posts gathered from: {e(", ".join(missing))}.</p>' if missing else ""
    hero = f"""<section class="pulse-hero">
  <div class="hero-score {score_class(t['net_score'])}">
    <span class="hero-num">{fmt_score(t['net_score'])}</span>
    <span class="hero-label">net sentiment</span>
  </div>
  <div class="hero-meta">
    <p class="hero-line"><b>{t['posts']}</b> public posts in the last {hours} hours &mdash;
      <span class="pos-ink">{t['positive']} positive</span>, <span class="neu-ink">{t['neutral']} neutral</span>,
      <span class="neg-ink">{t['negative']} negative</span>.</p>
    {sentiment_bar(t)}
    <ul class="plat-list">{plat_chips}</ul>
    {missing_html}
  </div>
</section>"""

    # ---- highlighted focus topics
    highlights = [x for x in report["topics"] if x["highlight"] and x["posts"]]
    hl_html = ""
    if highlights:
        cards = "".join(
            f'<li class="focus-card {score_class(x["net_score"])}"><p class="focus-label">{e(x["label"])}</p>'
            f'<p class="focus-stat"><b>{x["posts"]}</b> posts &middot; net {fmt_score(x["net_score"])}'
            + (f' &middot; {e(", ".join(_brand_names(x["brands"], cfg)))}' if x["brands"] else "") + "</p>"
            f'{sample_html(x["samples"][:2], platforms)}</li>'
            for x in highlights)
        hl_html = f'<section><h2 class="sec">Watch topics</h2><p class="muted small">Pinned in the backend config as highlights.</p><ul class="focus-grid">{cards}</ul></section>'

    # ---- brand scorecards
    cards = []
    for b in report["brands"]:
        series = [(d, hist.get(d, {}).get("brands", {}).get(b["key"], {}).get("net_score")) for d in sorted(hist)]
        spark = sparkline(series)
        plats = " &middot; ".join(f'{e(platforms[k]["label"])} {v["posts"]} ({fmt_score(v["net_score"])})'
                                  for k, v in b["platforms"].items() if v["posts"])
        ships = [s for s in b["ships"] if s["posts"]]
        ships_html = ""
        if ships:
            rows = "".join(
                f'<li{" class=standout" if s["standout"] else ""}><span>{e(s["name"])}</span>'
                f'<span class="muted">{s["posts"]} post{"s" if s["posts"] != 1 else ""}</span>'
                f'<span class="score {score_class(s["net_score"])}">{fmt_score(s["net_score"])}</span></li>'
                for s in ships[:6])
            ships_html = f'<p class="sub">Ships mentioned</p><ul class="mini-list">{rows}</ul>'
        dests_html = ""
        if b["destinations"]:
            rows = "".join(
                f'<li><span>{e(d["name"])}</span><span class="muted">{d["posts"]}</span>'
                f'<span class="score {score_class(d["net_score"])}">{fmt_score(d["net_score"])}</span></li>'
                for d in b["destinations"][:4])
            dests_html = f'<p class="sub">Destinations in the conversation</p><ul class="mini-list">{rows}</ul>'
        cards.append(f"""<li class="brand-card">
  <div class="brand-head">
    <div><h3>{e(b['name'])}</h3><p class="muted small">{e(b.get('group') or '')}</p></div>
    <div class="brand-score {score_class(b['net_score'])}"><span class="num">{fmt_score(b['net_score'])}</span><span class="lbl">net</span></div>
  </div>
  <p class="brand-line"><b>{b['posts']}</b> posts &middot; {b['positive']} positive / {b['neutral']} neutral / {b['negative']} negative
    &middot; weighted {fmt_score(b['weighted_score'])}</p>
  {sentiment_bar(b)}
  <p class="muted small">{plats}</p>
  {('<div class="trend"><span class="muted small">Trend</span>' + spark + '</div>') if spark else ''}
  {ships_html}{dests_html}
  <p class="sub">Sample posts</p>{sample_html(b['samples'], platforms)}
</li>""")
    brands_html = ('<ul class="brand-grid">' + "".join(cards) + "</ul>") if cards else '<p class="empty-state">No brand-attributed posts in this window.</p>'
    unat = report["unattributed"]
    unat_html = f'<p class="muted small">{unat["posts"]} posts mentioned cruising without a specific brand (net {fmt_score(unat["net_score"])}).</p>' if unat["posts"] else ""

    # ---- standouts
    def standout_table(rows, kind):
        if not rows:
            return f'<p class="muted small">No {kind} stood out in this window (needs {cfg["standouts"]["min_mentions"]}+ mentions and a net score beyond ±{cfg["standouts"]["min_abs_score"]}, or {cfg["standouts"]["spike_mentions"]}+ mentions).</p>'
        trs = "".join(
            f'<tr><td>{e(r["name"])}{(" <span class=muted>(" + e(_brand_names([r["brand"]], cfg)[0]) + ")</span>") if r.get("brand") else ""}</td>'
            f'<td class="num">{r["posts"]}</td><td class="num pos-ink">{r["positive"]}</td><td class="num neg-ink">{r["negative"]}</td>'
            f'<td class="num"><span class="score {score_class(r["net_score"])}">{fmt_score(r["net_score"])}</span></td>'
            f'<td>{sample_html(r["samples"][:1], platforms)}</td></tr>'
            for r in rows)
        return (f'<div class="table-wrap"><table class="pulse-table"><thead><tr><th>{kind.title()}</th><th>Posts</th><th>Pos</th><th>Neg</th><th>Net</th><th>Example</th></tr></thead>'
                f'<tbody>{trs}</tbody></table></div>')

    standouts_html = f"""<section><h2 class="sec">Standouts</h2>
<p class="sub">Ships</p>{standout_table(report['standouts']['ships'], 'ships')}
<p class="sub">Destinations</p>{standout_table(report['standouts']['destinations'], 'destinations')}
</section>"""

    # ---- all topics
    topics = [x for x in report["topics"] if x["posts"]]
    topics_html = ""
    if topics:
        pin = ' <span class="pin" title="highlighted in config">★</span>'
        trs = "".join(
            f'<tr><td>{e(x["label"])}{pin if x["highlight"] else ""}</td>'
            f'<td class="num">{x["posts"]}</td><td class="num pos-ink">{x["positive"]}</td><td class="num neg-ink">{x["negative"]}</td>'
            f'<td class="num"><span class="score {score_class(x["net_score"])}">{fmt_score(x["net_score"])}</span></td>'
            f'<td>{e(", ".join(_brand_names(x["brands"], cfg)))}</td></tr>'
            for x in topics)
        topics_html = (f'<section><h2 class="sec">Focus topics</h2><div class="table-wrap"><table class="pulse-table"><thead><tr><th>Topic</th><th>Posts</th><th>Pos</th><th>Neg</th><th>Net</th><th>Brands</th></tr></thead>'
                       f'<tbody>{trs}</tbody></table></div></section>')

    # ---- all posts table (the accessible table view)
    rows = "".join(
        f'<tr><td>{e(platforms.get(p["platform"], {}).get("label", p["platform"]))}</td>'
        f'<td>{sentiment_chip(p["sentiment"])}</td>'
        f'<td>{e(", ".join(_brand_names(p["brands"], cfg)) or "—")}</td>'
        f'<td>{e(", ".join(p["ships"] + p["destinations"]) or "—")}</td>'
        f'<td><a href="{e(p["url"])}" rel="nofollow noopener" target="_blank">{e(p.get("author") or "post")}</a>: {e(p["excerpt"])}</td></tr>'
        for p in report["posts"])
    posts_html = (f'<details class="all-posts"><summary>All {len(report["posts"])} rated posts</summary>'
                  f'<div class="table-wrap"><table class="pulse-table"><thead><tr><th>Platform</th><th>Sentiment</th><th>Brand</th><th>Ship / place</th><th>Post</th></tr></thead>'
                  f'<tbody>{rows}</tbody></table></div></details>') if report["posts"] else ""

    # ---- archive
    others = [d for d in all_dates if d != date]
    archive_html = ""
    if others:
        links = "".join(f'<li><a href="{asset_prefix}social/{d}.html">{dt.date.fromisoformat(d).strftime("%a %b %-d, %Y")}</a>'
                        f' <span class="muted">{hist.get(d, {}).get("posts", "")} posts, net {fmt_score(hist.get(d, {}).get("net_score", 0))}</span></li>'
                        for d in sorted(others, reverse=True))
        archive_html = f'<section><h2 class="sec">Earlier days</h2><ul class="archive">{links}</ul></section>'

    rb = report["rated_by"]
    method = f"""<section class="method"><h2 class="sec">How this is measured</h2>
<p>Public posts mentioning the cruise lines are gathered from X, Threads, Bluesky, Instagram and Facebook inside the
window, then each post is rated positive, neutral or negative ({rb['model']} rated by the daily brief's reviewer,
{rb['lexicon']} by the fallback word list). Net sentiment is positives minus negatives as a share of all posts, from
&minus;100 to +100; the weighted figure applies the per-platform weights. Ships and destinations are recognised from the
lists in the backend config, and a ship or place is called out as a standout when it draws enough mentions with a
clearly positive or negative tilt. Brands, ships, destinations, watch topics, platform weights and standout thresholds
are all adjustable in <a href="https://github.com/spencertownley-sys/CruiseNewsYouCanUse/blob/main/config/social_listening.json">config/social_listening.json</a>.
Sample sizes are small, so treat single-day scores as a pulse, not a poll.</p></section>"""

    content = f"""<a class="back-link" href="{asset_prefix}index.html">&larr; All briefs</a>
<article class="pulse">
  <div class="post-header">
    <p class="post-date">{display_date}</p>
    <h1>Social Pulse</h1>
    <p class="post-window">What cruisers are saying across X, Threads, Bluesky, Instagram and Facebook &mdash; window {e(win['since'][:16].replace('T', ' '))} to {e(win['until'][:16].replace('T', ' '))} UTC</p>
  </div>
  {hero}
  {hl_html}
  <section><h2 class="sec">By brand</h2>{brands_html}{unat_html}</section>
  {standouts_html}
  {topics_html}
  {posts_html}
  {archive_html}
  {method}
</article>"""
    return tmpl.format(
        page_title=f"Social Pulse — {display_date} — {site_name}",
        meta_description=f"Social sentiment on the major cruise lines for {display_date}: {t['posts']} posts, net {fmt_score(t['net_score'])}.",
        asset_prefix=asset_prefix,
        site_name=site_name,
        site_tagline=site_tagline,
        content=content,
    )


def _brand_names(keys, cfg):
    names = {b["key"]: b["name"] for b in cfg["brands"]}
    return [names.get(k, k) for k in keys]


def render(date, cfg):
    reports = sorted(p.stem for p in SOCIAL_DIR.glob("????-??-??.json"))
    if not reports:
        sys.exit("No reports to render; run `score` first.")
    hist = json.loads(HISTORY.read_text()) if HISTORY.exists() else {}
    latest = date or reports[-1]
    PAGES_DIR.mkdir(exist_ok=True)
    for d in reports:
        rep = json.loads(report_path(d).read_text())
        (PAGES_DIR / f"{d}.html").write_text(render_report(rep, cfg, hist, reports, "../"))
    rep = json.loads(report_path(latest).read_text())
    (ROOT / "social.html").write_text(render_report(rep, cfg, hist, reports, ""))
    print(f"Wrote social.html (latest: {latest}) and {len(reports)} day page(s) in social/")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def cmd_score(date, cfg):
    path = posts_path(date)
    if not path.exists():
        sys.exit(f"No posts file at {path.relative_to(ROOT)}; run `template` or `fetch` first.")
    data = json.loads(path.read_text())
    report, posts = score_day(date, cfg, data)
    # Persist the ratings/attributions back into the posts file so the record is complete.
    data["posts"] = [{k: v for k, v in p.items() if k not in ("topics",)} for p in posts] + [
        p for p in data.get("posts", []) if (p.get("id") or f"{(p.get('platform') or '').lower()}:{p.get('url')}") not in {q["id"] for q in posts}]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    SOCIAL_DIR.mkdir(parents=True, exist_ok=True)
    report_path(date).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    LATEST.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    update_history(report, cfg)
    t = report["totals"]
    print(f"Scored {t['posts']} posts for {date}: net {fmt_score(t['net_score'])} "
          f"({t['positive']} pos / {t['neutral']} neu / {t['negative']} neg); "
          f"{len(report['brands'])} brands, {len(report['standouts']['ships'])} standout ships, "
          f"{len(report['standouts']['destinations'])} standout destinations; dropped {report['dropped']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["template", "fetch", "hydrate", "score", "render", "run"])
    ap.add_argument("--date", help="ISO date YYYY-MM-DD (defaults to today, UTC)")
    ap.add_argument("--urls", help="hydrate: text file of post URLs, one per line, optionally '| sentiment | reason'")
    args = ap.parse_args()
    cfg = load_config()
    date = args.date or dt.datetime.utcnow().date().isoformat()
    dt.date.fromisoformat(date)
    if args.command == "template":
        write_template(date, cfg)
    elif args.command == "fetch":
        import os
        if os.environ.get("X_BEARER_TOKEN"):
            fetch_x_api(date, cfg, os.environ["X_BEARER_TOKEN"])
        fetch_bluesky(date, cfg)
    elif args.command == "hydrate":
        hydrate(date, cfg, args.urls)
    elif args.command == "score":
        cmd_score(date, cfg)
    elif args.command == "render":
        render(args.date, cfg)
    elif args.command == "run":
        cmd_score(date, cfg)
        render(date, cfg)


if __name__ == "__main__":
    main()
