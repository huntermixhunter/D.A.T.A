"""Narrow, opt-in transport from the local forum to a shared community.

DATA_COMMUNITY_URL is an origin, for example https://community.example.com.
Only the forum and its account forms cross this boundary. Local marketplace
sessions, bridge credentials, browser headers and local account data do not.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import threading
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlencode, urlsplit

from marketplace import MarketError, SESSION_SECONDS, json_object, require


MAX_BODY = 128 * 1024
MAX_AUTH_BODY = 8 * 1024
MAX_RESPONSE = 2 * 1024 * 1024
REQUEST_TIMEOUT = 10
CONNECT_TIMEOUT = 5
TOKEN_FIELD = "_session_token"
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
_IDENTIFIER = re.compile(r"[0-9a-f]{32}")
_IN_FLIGHT = threading.BoundedSemaphore(8)


def cookie_name(port):
    return f"data_forum_session_{int(port)}"


def cookie_header(port, value):
    """The dashboard is loopback HTTP; its session never enters JavaScript."""
    require(isinstance(value, str) and
            (not value or re.fullmatch(r"[0-9a-f]{32}\.[A-Za-z0-9_-]{43}", value)),
            "Invalid community session.", 502)
    return (f"{cookie_name(port)}={value}; HttpOnly; SameSite=Strict; "
            f"Path=/marketplace/forum; Max-Age={SESSION_SECONDS if value else 0}")


def configured_origin():
    value = os.environ.get("DATA_COMMUNITY_URL", "").strip()
    if not value:
        return None
    message = "DATA_COMMUNITY_URL must be an HTTPS origin; HTTP is allowed only for localhost development."
    return normalized_origin(value, message)


def normalized_origin(value, message, allow_path=False):
    require(not any(c.isspace() or ord(c) < 32 for c in value) and
            not any(c in value for c in ("\\", "?", "#", "%")), message, 503)
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
    except ValueError as exc:
        raise MarketError(message, 503) from exc
    require(parsed.scheme in ("http", "https") and host and
            parsed.username is None and parsed.password is None and
            (allow_path or parsed.path in ("", "/")) and not parsed.query and not parsed.fragment, message, 503)
    require(host.isascii(), message, 503)
    try:
        address = ipaddress.ip_address(host)
        loopback = address.is_loopback
    except ValueError:
        require(len(host) <= 253 and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                        for label in host.lower().split(".")), message, 503)
        loopback = host.lower() == "localhost"
    require(parsed.scheme == "https" or loopback, message, 503)
    require(port is None or 1 <= port <= 65535, message, 503)
    authority = f"[{host.lower()}]" if ":" in host else host.lower()
    default_port = 443 if parsed.scheme == "https" else 80
    if port is not None and port != default_port:
        authority += f":{port}"
    path = parsed.path.rstrip("/") if allow_path else ""
    require(not path or (path.startswith("/") and len(path) <= 200 and
                        all(re.fullmatch(r"[a-zA-Z0-9_-]+", part) for part in path[1:].split("/"))), message, 503)
    return f"{parsed.scheme}://{authority}{path}"


def validate_request(parts, method, data, query):
    """The same allowlist protects both the proxy and the standalone service."""
    require(isinstance(parts, list) and all(isinstance(p, str) for p in parts),
            "Unknown community endpoint.", 404)
    require(method in ("GET", "POST"), "Method not supported.", 405)
    allowed = None
    query_fields = set()
    if method == "GET":
        if parts in (["meta"], ["session"]):
            allowed = set()
        elif parts == ["threads"]:
            allowed = set()
            query_fields = {"category", "q", "sort", "view", "page"}
        elif len(parts) == 2 and parts[0] == "threads" and _IDENTIFIER.fullmatch(parts[1]):
            allowed = set()
            query_fields = {"page"}
    else:
        if len(parts) == 2 and parts[0] == "account":
            allowed = {
                "register": {"username", "name", "password"},
                "login": {"username", "password"}, "logout": set(), "profile": {"name"},
            }.get(parts[1])
        elif parts == ["threads"]:
            allowed = {"category", "title", "body", "request_id"}
        elif len(parts) == 3 and _IDENTIFIER.fullmatch(parts[1]):
            if parts[0] == "threads":
                allowed = {"reply": {"body", "request_id"}, "follow": {"following"}}.get(parts[2])
            elif parts[0] == "posts":
                allowed = {"edit": {"body", "title"}, "delete": set(),
                           "report": {"reason"}, "like": {"liked"}}.get(parts[2])
    require(allowed is not None, "Unknown community endpoint.", 404)
    require(isinstance(data, dict) and set(data) <= allowed, "Unsupported community fields.")
    require(all(isinstance(value, (str, bool)) for value in data.values()), "Invalid community field value.")
    require(isinstance(query, dict) and set(query) <= query_fields, "Unsupported community query.")
    for values in query.values():
        require(isinstance(values, list) and len(values) == 1 and isinstance(values[0], str) and
                len(values[0]) <= 512 and not any(ord(c) < 32 for c in values[0]), "Invalid community query.")
    try:
        raw = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise MarketError("Invalid community request.") from exc
    limit = MAX_AUTH_BODY if parts[0] == "account" else MAX_BODY
    require(len(raw) <= limit, "Community request is too large.", 413)
    return raw


def _cookie_token(handler, origin, name=None):
    cookies = SimpleCookie()
    try:
        cookies.load(handler.headers.get("Cookie", ""))
        item = cookies.get(name or cookie_name(handler.server.server_address[1]))
        value = item.value if item else ""
    except Exception:
        return ""
    prefix = hashlib.sha256(origin.encode("ascii")).hexdigest()[:32] + "."
    return value[len(prefix):] if value.startswith(prefix) and _TOKEN.fullmatch(value[len(prefix):]) else ""


def _interrupt_connection(state):
    state["cancelled"].set()
    connection = state.get("connection")
    sock = state.get("socket")
    if sock:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    if connection:
        try:
            connection.close()
        except OSError:
            pass


def _send(origin, target, method, raw, token, state):
    parsed = urlsplit(origin)
    connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    options = {"timeout": CONNECT_TIMEOUT}
    if parsed.scheme == "https":
        options["context"] = ssl.create_default_context()
    connection = connection_class(parsed.hostname, parsed.port, **options)
    state["connection"] = connection
    headers = {"Accept": "application/json", "Accept-Encoding": "identity", "Connection": "close"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if method == "POST":
        headers["Content-Type"] = "application/json"
    response = None
    try:
        connection.connect()
        state["socket"] = connection.sock
        require(not state["cancelled"].is_set(), "Community service timed out. Please retry.", 504)
        connection.request(method, target, body=raw if method == "POST" else None, headers=headers)
        response = connection.getresponse()
        require(not 300 <= response.status < 400, "Community redirects are not permitted.", 502)
        require(response.getheader("Content-Type", "").split(";")[0].strip().lower() == "application/json",
                "Community service returned an invalid response.", 502)
        require(not response.getheader("Transfer-Encoding") and
                response.getheader("Content-Encoding", "identity").lower() == "identity",
                "Community service returned unsupported response encoding.", 502)
        lengths = response.headers.get_all("Content-Length", [])
        require(len(lengths) == 1 and lengths[0].isascii() and lengths[0].isdigit(),
                "Community service returned an invalid response length.", 502)
        length = int(lengths[0])
        require(0 < length <= state.get("max_response", MAX_RESPONSE), "Community response is too large.", 502)
        payload = response.read(length)
        require(len(payload) == length, "Community service returned an incomplete response.", 502)
        try:
            result = json_object(payload)
        except MarketError as exc:
            raise MarketError("Community service returned invalid JSON.", 502) from exc
        require(200 <= response.status <= 599, "Community service returned an invalid status.", 502)
        return result, response.status
    finally:
        if response is not None:
            response.close()
        connection.close()


def _request(origin, target, method, raw, token, maximum=MAX_RESPONSE):
    """Bound wall-clock time, including DNS, without leaving unlimited workers."""
    require(_IN_FLIGHT.acquire(blocking=False), "Community service is busy. Please retry.", 503)
    complete = threading.Event()
    state = {"cancelled": threading.Event(), "max_response": maximum}

    def work():
        try:
            state["result"] = _send(origin, target, method, raw, token, state)
        except Exception as exc:
            state["error"] = exc
        finally:
            _IN_FLIGHT.release()
            complete.set()

    worker = threading.Thread(target=work, name="community-request", daemon=True)
    try:
        worker.start()
    except Exception:
        _IN_FLIGHT.release()
        raise
    if not complete.wait(REQUEST_TIMEOUT):
        _interrupt_connection(state)
        raise MarketError("Community service timed out. Please retry.", 504)
    if "error" in state:
        error = state["error"]
        if isinstance(error, MarketError):
            raise error
        if isinstance(error, (TimeoutError, socket.timeout)):
            raise MarketError("Community service timed out. Please retry.", 504) from error
        raise MarketError("Community service is unavailable. Please retry.", 502) from error
    return state["result"]


def proxy_request(handler, parts, data):
    """Return None for local mode; otherwise (JSON object, status, cookie value).

    The hub's _session_token field is a server-to-server transport detail. It
    never reaches dashboard JavaScript. Failed remote requests stay remote.
    """
    try:
        origin = configured_origin()
        if origin is None:
            return None
        parsed = urlsplit(handler.path)
        require(parsed.path == "/marketplace/forum/" + "/".join(parts),
                "Unknown community endpoint.", 404)
        require(len(parsed.query) <= 2048, "Community query is too large.", 413)
        try:
            query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=8)
        except ValueError as exc:
            raise MarketError("Invalid community query.") from exc
        raw = validate_request(parts, handler.command, data, query)
        target = "/community/" + "/".join(parts)
        if query:
            target += "?" + urlencode(query, doseq=True)
        token = _cookie_token(handler, origin)
        result, status = _request(origin, target, handler.command, raw, token)
        cookie = "" if status == 401 and token else None
        issues_session = (status == 200 and handler.command == "POST" and len(parts) == 2 and
                          parts[0] == "account" and parts[1] in ("register", "login", "logout"))
        require(not issues_session or TOKEN_FIELD in result,
                "Community service did not return a session.", 502)
        if TOKEN_FIELD in result:
            issued = result.pop(TOKEN_FIELD)
            require(issues_session,
                    "Community service returned an unexpected session.", 502)
            require(isinstance(issued, str) and
                    (issued == "" if parts[1] == "logout" else bool(_TOKEN.fullmatch(issued))),
                    "Community service returned an invalid session.", 502)
            cookie = hashlib.sha256(origin.encode("ascii")).hexdigest()[:32] + "." + issued if issued else ""
        if parts == ["meta"] and status == 200:
            result["mode"] = "shared"
            result["community_origin"] = origin
        return result, status, cookie
    except MarketError as exc:
        return {"error": str(exc)}, exc.status, None
    except (ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError):
        return {"error": "Invalid community request."}, 400, None
    except (OSError, http.client.HTTPException):
        return {"error": "Community service is unavailable. Please retry."}, 502, None
