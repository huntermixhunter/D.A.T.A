"""
News aggregator for the Daily Briefing panel.

Pulls RSS feeds for four sections (markets / crypto / spiritual / youtube),
parses with feedparser, dedupes by URL, sorts newest first, caches to disk.

Source list lives in news_sources.json (auto-seeded on first run). Cache
lives in news_cache.json. The cache is what /news returns; only /news/refresh
(or the hourly standing order) actually hits the network.

YouTube channel URLs are auto-resolved to their RSS feed:
  https://www.youtube.com/@handle  →  https://www.youtube.com/feeds/videos.xml?channel_id=UCxxx
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("news_aggregator")

HERE             = Path(__file__).resolve().parent
SOURCES_FILE     = HERE / "news_sources.json"
CACHE_FILE       = HERE / "news_cache.json"
OG_CACHE_FILE    = HERE / "og_image_cache.json"   # URL → og:image URL
SECTIONS         = ("markets", "crypto", "spiritual", "youtube", "instagram", "pinterest")
MAX_ITEMS_PER_FEED = 15
MAX_TOTAL_PER_SECTION = 60
FETCH_TIMEOUT    = 12
OG_FETCH_TIMEOUT = 4
OG_MAX_PARALLEL  = 6      # concurrent og:image fetches per refresh

# Default sources — seeded the first time. Captain can edit/replace via UI.
_DEFAULT_SOURCES: dict[str, list[str]] = {
    "markets": [
        # Stocks, commodities, energy, macro — anything that informs the
        # Captain's trading and investing decisions. World news is intentionally
        # omitted; crypto has its own section below.
        "https://feeds.content.dowjones.io/public/rss/mw_topstories",  # MarketWatch
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",       # CNBC top news
        "https://finance.yahoo.com/news/rssindex",                     # Yahoo Finance
        "https://www.kitco.com/rss/KitcoNews.xml",                     # Gold/silver/metals
        "https://oilprice.com/rss/main",                               # Energy
        "https://feeds.feedburner.com/zerohedge/feed",                 # Macro/contrarian
    ],
    "crypto": [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
        "https://decrypt.co/feed",
    ],
    "spiritual": [
        "https://religionnews.com/feed/",
        "https://www.thehindu.com/society/faith/feeder/default.rss",
        "https://www.buddhistdoor.net/rss/articles",
    ],
    "youtube": [
        # Seed empty — Captain adds channels via the SOURCES dialog
    ],
    "instagram": [
        # Always empty — IG section is fed by ginstagram.py via Meta Graph API.
        # The SOURCES dialog still shows the section, but RSS isn't applicable.
    ],
    "pinterest": [
        # Always empty — Pinterest section is fed by gpinterest.py via Pinterest API v5.
    ],
}

_lock = threading.Lock()


# ─── Sources I/O ──────────────────────────────────────────────────────────
def _load_sources() -> dict[str, list[str]]:
    if not SOURCES_FILE.exists():
        SOURCES_FILE.write_text(json.dumps(_DEFAULT_SOURCES, indent=2), encoding="utf-8")
        log.info(f"[news] seeded {SOURCES_FILE.name} with defaults")
        return dict(_DEFAULT_SOURCES)
    try:
        data = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.exception(f"[news] bad sources file: {e}")
        return dict(_DEFAULT_SOURCES)
    # Backfill missing sections with empty lists
    for s in SECTIONS:
        data.setdefault(s, [])
    return data


def save_sources(sources: dict[str, list[str]]) -> None:
    cleaned: dict[str, list[str]] = {}
    for s in SECTIONS:
        urls = sources.get(s, [])
        if isinstance(urls, str):
            urls = [u.strip() for u in urls.splitlines() if u.strip()]
        cleaned[s] = [u.strip() for u in urls if isinstance(u, str) and u.strip()]
    SOURCES_FILE.write_text(json.dumps(cleaned, indent=2), encoding="utf-8")


def get_sources() -> dict[str, list[str]]:
    return _load_sources()


# ─── YouTube URL → RSS feed URL resolution ────────────────────────────────
def _resolve_youtube_feed(url: str) -> Optional[str]:
    """Return the channel-RSS URL for a given YouTube URL/handle.
    Supported forms:
      - already-RSS:  https://www.youtube.com/feeds/videos.xml?channel_id=UCxxx
      - channel page: https://www.youtube.com/channel/UCxxx
      - handle:       https://www.youtube.com/@somename  /  @somename
      - bare handle:  somename  (treated as @somename)
    """
    url = url.strip()
    if not url:
        return None
    if "feeds/videos.xml" in url:
        return url
    m = re.search(r"youtube\.com/channel/([A-Za-z0-9_-]+)", url)
    if m:
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={m.group(1)}"

    handle = None
    m = re.search(r"youtube\.com/@([A-Za-z0-9_.-]+)", url)
    if m:
        handle = m.group(1)
    elif url.startswith("@"):
        handle = url[1:]
    elif re.fullmatch(r"[A-Za-z0-9_.-]+", url):
        handle = url
    if not handle:
        return None
    # Hit the channel page and scrape the canonical channel_id out of meta tags
    try:
        page = urllib.request.urlopen(f"https://www.youtube.com/@{handle}", timeout=FETCH_TIMEOUT).read().decode("utf-8", errors="ignore")
    except Exception as e:
        log.warning(f"[news] yt handle resolve failed for @{handle}: {e}")
        return None
    m = re.search(r'"channelId":"(UC[A-Za-z0-9_-]+)"', page) or \
        re.search(r'channel/(UC[A-Za-z0-9_-]+)', page)
    if not m:
        log.warning(f"[news] could not find channelId in @{handle} page")
        return None
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={m.group(1)}"


def _normalize_url(section: str, url: str) -> Optional[str]:
    url = (url or "").strip()
    if not url:
        return None
    if section == "youtube":
        return _resolve_youtube_feed(url)
    if not url.startswith(("http://", "https://")):
        return None
    return url


# ─── Fetch + parse ────────────────────────────────────────────────────────
def _fetch_feed(url: str) -> list[dict]:
    try:
        import feedparser
    except ImportError:
        log.error("[news] feedparser not installed — pip install feedparser")
        return []
    try:
        # feedparser handles its own HTTP fetch but we pass a UA to dodge 403s
        feed = feedparser.parse(url, agent="DATA-bridge/1.0 (+local)")
    except Exception as e:
        log.warning(f"[news] feed fetch failed {url}: {e}")
        return []
    items: list[dict] = []
    for entry in (feed.entries or [])[:MAX_ITEMS_PER_FEED]:
        link  = entry.get("link", "")
        title = (entry.get("title", "") or "").strip()
        if not link or not title:
            continue
        # Published time — different feeds use different fields
        pub_struct = entry.get("published_parsed") or entry.get("updated_parsed")
        ts = int(time.mktime(pub_struct)) if pub_struct else 0
        source_name = (feed.feed.get("title", "") or "").strip() or _domain(link)
        # YouTube thumbnail when available — pick the LARGEST media_thumbnail
        thumb = ""
        if "media_thumbnail" in entry and entry.media_thumbnail:
            # Each thumbnail entry has width/height; pick the biggest
            best = max(
                entry.media_thumbnail,
                key=lambda m: int(m.get("width") or 0) * int(m.get("height") or 0),
            )
            thumb = best.get("url", "")
        elif "media_content" in entry and entry.media_content:
            for m in entry.media_content:
                if m.get("medium") == "image":
                    thumb = m.get("url", "")
                    break
        # Fallback: scrape image from summary HTML
        if not thumb:
            summary = entry.get("summary", "") or entry.get("description", "") or ""
            m = re.search(r'<img[^>]+src="([^"]+)"', summary)
            if m: thumb = m.group(1)
        # Upgrade YouTube thumbnails — RSS often gives default (120x90) or
        # hqdefault (480x360). Bump to maxresdefault (1280x720); the frontend
        # has an onerror fallback that retries hqdefault if maxres isn't
        # generated for that video.
        if thumb and "ytimg.com/vi/" in thumb:
            thumb = re.sub(
                r"/(default|mqdefault|hqdefault|sddefault|maxresdefault|hq\d+|sd\d+)\.jpg",
                "/maxresdefault.jpg",
                thumb,
            )
        # Drop generic site logos — we'd rather show a text-only card than
        # the same Hindu/logo image stamped on every article.
        if _is_generic_thumbnail(thumb):
            thumb = ""
        # Strip HTML from summary text
        summary_txt = re.sub(r"<[^>]+>", "", entry.get("summary", "") or "")[:240].strip()
        items.append({
            "title":     title,
            "link":      link,
            "source":    source_name[:80],
            "thumbnail": thumb,
            "ts":        ts,
            "iso":       datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else "",
            "summary":   summary_txt,
        })
    return items


def _domain(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else url


# ─── Generic / fallback thumbnail blacklist ───────────────────────────────
# Some sites serve their site-wide logo as og:image when they don't have an
# article-specific one (The Hindu, for instance, ships the article image via
# JS after page load). Treating those as "no thumbnail" is better than stamping
# the same generic logo on every card.
_GENERIC_THUMBNAIL_PATTERNS = (
    "thehindu.com/theme/images/og-image",
    "thehindu.com/theme/images/th-online/og-",
    "thehindu.com/static/content/newsletter",
    "thehindu.com/apple-touch-icon",
    "thehindu.com/theme/images/th-online/thehindu-logo",
    "favicon",
    "default-og.png",
    "og-default.",
    "default_image.",
)

def _is_generic_thumbnail(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    return any(p in u for p in _GENERIC_THUMBNAIL_PATTERNS)


# ─── og:image scraping for items that RSS didn't include a thumbnail for ──
_OG_PATTERNS = [
    re.compile(r'<meta\s+[^>]*property=["\']og:image(?::secure_url)?["\'][^>]*content=["\']([^"\']+)', re.I),
    re.compile(r'<meta\s+[^>]*content=["\']([^"\']+)["\'][^>]*property=["\']og:image(?::secure_url)?["\']', re.I),
    re.compile(r'<meta\s+[^>]*name=["\']twitter:image["\'][^>]*content=["\']([^"\']+)', re.I),
    re.compile(r'<meta\s+[^>]*content=["\']([^"\']+)["\'][^>]*name=["\']twitter:image["\']', re.I),
    re.compile(r'<link\s+[^>]*rel=["\']image_src["\'][^>]*href=["\']([^"\']+)', re.I),
]

def _load_og_cache() -> dict:
    if OG_CACHE_FILE.exists():
        try: return json.loads(OG_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception: pass
    return {}

def _save_og_cache(cache: dict) -> None:
    try:
        # Trim to last 2000 entries to keep file size bounded
        if len(cache) > 2000:
            cache = dict(list(cache.items())[-2000:])
        OG_CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
    except Exception as e:
        log.warning(f"[news] og cache save failed: {e}")

def _fetch_og_image(url: str) -> str:
    """Pull og:image (or twitter:image) from an article page. Reads only the
    first 64KB since meta tags live in <head>. Returns '' on any failure."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; DATA-bridge/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        })
        with urllib.request.urlopen(req, timeout=OG_FETCH_TIMEOUT) as r:
            data = r.read(65536).decode("utf-8", errors="ignore")
        for pat in _OG_PATTERNS:
            m = pat.search(data)
            if m:
                img = m.group(1).strip()
                # Resolve protocol-relative URLs
                if img.startswith("//"):
                    img = "https:" + img
                # Resolve relative URLs against the page's origin
                elif img.startswith("/"):
                    m2 = re.match(r"(https?://[^/]+)", url)
                    if m2: img = m2.group(1) + img
                # Skip known-generic site logos
                if _is_generic_thumbnail(img):
                    return ""
                return img
    except Exception:
        pass
    return ""

def _backfill_thumbnails(items: list) -> None:
    """For items with no thumbnail, fetch og:image in parallel and fill in.
    Caches results so subsequent refreshes don't refetch the same articles."""
    cache = _load_og_cache()
    # Purge cached entries that are now-blacklisted generics so they get
    # re-evaluated. Without this, the bad Hindu logo URL stays cached forever.
    for k in list(cache.keys()):
        if _is_generic_thumbnail(cache[k]):
            del cache[k]
    need = [it for it in items if not it.get("thumbnail")]
    if not need: return

    # Use cached og:image where possible
    to_fetch = []
    for it in need:
        cached = cache.get(it["link"])
        if cached is not None:
            if cached: it["thumbnail"] = cached
        else:
            to_fetch.append(it)

    if not to_fetch: return
    log.info(f"[news] scraping og:image for {len(to_fetch)} items")
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(max_workers=OG_MAX_PARALLEL) as ex:
        futures = {ex.submit(_fetch_og_image, it["link"]): it for it in to_fetch}
        for fut in cf.as_completed(futures, timeout=OG_FETCH_TIMEOUT * 3):
            it = futures[fut]
            try:
                img = fut.result()
            except Exception:
                img = ""
            cache[it["link"]] = img   # cache empty string too — don't re-try
            if img: it["thumbnail"] = img
    _save_og_cache(cache)


# ─── Refresh + cache ──────────────────────────────────────────────────────
def refresh_all() -> dict:
    """Re-fetch every configured feed, write the cache file, return the
    payload that /news GET would return."""
    sources = _load_sources()
    payload = {"updated_at": time.time(), "sections": {}}
    for section in SECTIONS:
        log.info(f"[news] refreshing section={section} ({len(sources.get(section, []))} sources)")
        agg: list[dict] = []
        seen = set()
        for raw in sources.get(section, []):
            url = _normalize_url(section, raw)
            if not url: continue
            for it in _fetch_feed(url):
                if it["link"] in seen: continue
                seen.add(it["link"])
                agg.append(it)

        # YouTube: merge in the captain's actual subscription feed if the
        # OAuth token has been set up. Skips silently otherwise so RSS
        # remains the fallback.
        if section == "youtube":
            try:
                import gyoutube
                if gyoutube.available():
                    sub_items = gyoutube.fetch_subscription_uploads(
                        max_per_channel=3, max_age_days=14)
                    added = 0
                    for it in sub_items:
                        if it["link"] in seen: continue
                        seen.add(it["link"])
                        agg.append(it)
                        added += 1
                    log.info(f"[news] youtube subscriptions: +{added} videos "
                             f"(pulled {len(sub_items)}, deduped {len(sub_items) - added})")
            except Exception as e:
                log.info(f"[news] youtube subscriptions unavailable: {e}")

        # Instagram: section is fed entirely by ginstagram.py (Meta Graph API).
        # No RSS source list — token presence is the only configuration.
        if section == "instagram":
            try:
                import ginstagram
                if ginstagram.available():
                    ig_items = ginstagram.fetch_recent_media(
                        max_items=20, max_age_days=14)
                    for it in ig_items:
                        if it["link"] in seen: continue
                        seen.add(it["link"])
                        agg.append(it)
                    log.info(f"[news] instagram: pulled {len(ig_items)} posts")
            except Exception as e:
                log.info(f"[news] instagram unavailable: {e}")

        # Pinterest: section is fed entirely by gpinterest.py (Pinterest API v5).
        # No RSS source list — token presence is the only configuration.
        if section == "pinterest":
            try:
                import gpinterest
                if gpinterest.available():
                    pin_items = gpinterest.fetch_recent_pins(
                        max_items=30, max_age_days=14)
                    for it in pin_items:
                        if it["link"] in seen: continue
                        seen.add(it["link"])
                        agg.append(it)
                    log.info(f"[news] pinterest: pulled {len(pin_items)} pins")
            except Exception as e:
                log.info(f"[news] pinterest unavailable: {e}")

        agg.sort(key=lambda x: x["ts"] or 0, reverse=True)
        top = agg[:MAX_TOTAL_PER_SECTION]
        # Backfill missing thumbnails by scraping og:image. Skip for youtube
        # since RSS already gives us a thumb (and og:image would just be the
        # same YT thumbnail).
        if section != "youtube":
            _backfill_thumbnails(top)
        payload["sections"][section] = top
    with _lock:
        CACHE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.info(f"[news] refresh complete — {sum(len(v) for v in payload['sections'].values())} items total")
    return payload


def get_cached() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"updated_at": 0, "sections": {s: [] for s in SECTIONS}}
