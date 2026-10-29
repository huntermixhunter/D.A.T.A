"""Read-only discovery of public skills. No credentials, installs or AI calls.

Uses the same public endpoint as vercel-labs/skills/src/find.ts. This endpoint
is not the authenticated v1 API; keep failures visible if its contract changes.
"""

import json
import http.client
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from difflib import SequenceMatcher

SEARCH_PATH = "/upgrades/skills/search"
SEARCH_URL = "https://skills.sh/api/search"
RESULT_LIMIT = 60
CACHE_TTL = 600
CACHE_LIMIT = 64
MAX_BYTES = 1_000_000
_cache = OrderedDict()
_lock = threading.Lock()
_source = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
_slug = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,149}\Z")


class SearchUnavailable(Exception):
    pass


def _words(value):
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold())


def _relevance(item, query):
    """Group close keyword matches; popularity decides within each match tier."""
    terms = list(dict.fromkeys(_words(query)))
    title = _words(item["name"])
    words = set(title + _words(item["skill_id"]))
    if terms == title or terms == _words(item["skill_id"]):
        return 5
    if all(term in words for term in terms):
        return 4
    if all(any(word.startswith(term) or SequenceMatcher(None, term, word).ratio() >= .8
               for word in words) for term in terms):
        return 3
    searchable = words | set(_words(item["description"] + " " + item["source"]))
    if all(term in searchable for term in terms):
        return 2
    if any(term in words for term in terms):
        return 1
    # Preserve semantic candidates returned by the catalog, even when a task
    # description uses different words from the skill's name.
    return 0


def _normalize(rows):
    items, seen = [], set()
    for row in rows[:200]:
        if not isinstance(row, dict):
            continue
        source = row.get("source")
        slug = row.get("skillId") or row.get("slug")
        if not slug and isinstance(row.get("id"), str):
            slug = row["id"].rsplit("/", 1)[-1]
        if not isinstance(source, str) or not _source.fullmatch(source):
            continue
        if not isinstance(slug, str) or not _slug.fullmatch(slug):
            continue
        key = source + "/" + slug
        if key.casefold() in seen:
            continue
        seen.add(key.casefold())
        installs = row.get("installs")
        # Unknown is different from zero; never invent a count or a rating.
        if type(installs) is not int or not 0 <= installs <= 2**53 - 1:
            installs = None
        name = row.get("name")
        description = row.get("description")
        items.append({
            "id": key, "skill_id": slug,
            "name": name[:160] if isinstance(name, str) and name.strip() else slug,
            "description": description[:600] if isinstance(description, str) else "",
            "source": source, "installs": installs,
            "url": "https://skills.sh/" + key,
            "source_url": "https://github.com/" + source,
        })
    return items


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fetch(query):
    url = SEARCH_URL + "?" + urllib.parse.urlencode({"q": query, "limit": RESULT_LIMIT})
    request = urllib.request.Request(url, headers={
        "Accept": "application/json", "User-Agent": "DATA-Skill-Search/1.0",
    })
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=10) as response:
            body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise SearchUnavailable("The skill catalog returned too much data. Try a narrower search.")
        payload = json.loads(body)
        if not isinstance(payload, dict) or not isinstance(payload.get("skills"), list):
            raise SearchUnavailable("The skill catalog response has changed. Try the catalog website.")
        return _normalize(payload["skills"])
    except urllib.error.HTTPError as exc:
        message = ("The skill catalog is busy. Try again in a minute."
                   if exc.code == 429 else "The skill catalog is unavailable. Please try again.")
        exc.close()
        raise SearchUnavailable(message) from exc
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise SearchUnavailable("Could not reach the skill catalog. Check your connection and try again.") from exc


def search(query, sort="relevance"):
    query = " ".join(query.split())
    if not 2 <= len(query) <= 160 or not _words(query):
        raise ValueError("Enter between 2 and 160 characters, including a keyword.")
    if sort not in {"relevance", "installs"}:
        raise ValueError("Choose Best match or Most installed.")
    key = query.casefold()
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
        if cached:
            _cache.move_to_end(key)
    fresh = cached is not None and now - cached[0] < CACHE_TTL
    items = cached[1] if fresh else _fetch(query)
    if not fresh:
        with _lock:
            _cache[key] = (time.monotonic(), items)
            _cache.move_to_end(key)
            while len(_cache) > CACHE_LIMIT:
                _cache.popitem(last=False)
    ranked = [dict(item, relevance=_relevance(item, query)) for item in items]
    def order(item):
        popularity = item["installs"] if item["installs"] is not None else -1
        primary = (-item["relevance"], -popularity) if sort == "relevance" else (-popularity, -item["relevance"])
        return (*primary, item["name"].casefold(), item["id"])
    ranked.sort(key=order)
    return {"query": query, "sort": sort, "skills": ranked[:RESULT_LIMIT],
            "count": len(ranked[:RESULT_LIMIT]), "limit": RESULT_LIMIT,
            "source": "skills.sh", "cached": fresh}


def handle_http(handler):
    """Shared by the authenticated bridge and the loopback development preview."""
    parsed = urllib.parse.urlsplit(handler.path)
    if parsed.path != SEARCH_PATH:
        return False
    if hasattr(handler, "_check_auth") and not handler._check_auth():
        return True
    try:
        args = urllib.parse.parse_qs(parsed.query, max_num_fields=10)
        payload = search(args.get("q", [""])[0], args.get("sort", ["relevance"])[0])
        status = 200
    except ValueError as exc:
        status, payload = 400, {"error": str(exc)}
    except SearchUnavailable as exc:
        status, payload = 503, {"error": str(exc)}
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    if hasattr(handler, "send_cors"):
        handler.send_cors()
    handler.end_headers()
    handler.wfile.write(body)
    return True
