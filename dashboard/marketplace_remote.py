"""Translate the existing dashboard API to shared services and device installs."""
from __future__ import annotations

import base64
from urllib.parse import parse_qs, urlsplit

from device_extensions import get_library
from marketplace import MarketError, image_data, require, starter_package
from shared_service import DOWNLOAD_FIELDS, IDENTIFIER, VERSION
from shared_transport import SharedClient, configured_url, cookie_header


def _local_request(parts, method, data):
    """Local-only operations are never forwarded as public service methods."""
    if method == "GET" and parts == ["runtime"]:
        require(not data, "Unsupported runtime fields.")
        return True
    if method == "GET" and len(parts) == 2 and parts[0] == "templates":
        return True
    if method == "POST" and len(parts) == 3 and parts[0] == "listings" and IDENTIFIER.fullmatch(parts[1]):
        action = parts[2]
        fields = {"install": DOWNLOAD_FIELDS, "rollback": {"version", "sha256", "acknowledge"},
                  "enable": set(), "disable": set(), "uninstall": set()}.get(action)
        if fields is not None:
            require(set(data) <= fields, "Unsupported installation fields.")
            if action in ("install", "rollback"):
                require(isinstance(data.get("version"), str) and VERSION.fullmatch(data["version"]) and
                        isinstance(data.get("sha256"), str) and len(data["sha256"]) == 64,
                        "Invalid package version or checksum.")
            return True
    return False


def handle_request(handler, store, parts, data):
    """Return None only when shared services are deliberately unconfigured."""
    client = None
    mime = "application/json; charset=utf-8"
    try:
        url = configured_url()
        if url is None:
            return None
        client = SharedClient(handler, url)
        parsed = urlsplit(handler.path)
        require(parsed.path == "/marketplace/" + "/".join(parts) and not parsed.fragment,
                "Unknown marketplace endpoint.", 404)
        require(len(parsed.query) <= 2048, "Marketplace query is too large.", 413)
        query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=8)
        method = handler.command
        library = get_library(store)
        if parts[:1] == ["forum"]:
            forum_parts = parts[1:]
            # The legacy forum-only API remains supported. Unified mode uses
            # the same account routes and cookie as Settings and Marketplace.
            is_account = forum_parts[:1] == ["account"]
            result = client.request(forum_parts, method, data, query, forum=not is_account)
        elif _local_request(parts, method, data):
            require(not query, "Unsupported installation query.")
            if parts[:1] == ["templates"]:
                raw = starter_package(parts[1])
                result = {"package": base64.b64encode(raw).decode("ascii"), "filename": "data-" + parts[1] + "-starter.zip"}
            else:
                account = client.request(["session"])["account"]
                if parts == ["runtime"]:
                    result = {"extensions": library.runtime(url, account, client)}
                else:
                    require(account, "Sign in to manage installed extensions.", 401)
                    lid, action = parts[1:]
                    if action == "install":
                        downloaded = client.request(["listings", lid, "download"], "POST", data)
                        require(downloaded.get("listing", {}).get("id") == lid, "Downloaded listing does not match.", 502)
                        result = library.install(url, account, downloaded, data)
                    else:
                        result = library.manage(url, account, lid, action, data, client)
        else:
            result = client.request(parts, method, data, query)
            if method == "GET" and parts == ["catalog"]:
                # Each public response is bounded. The desktop currently shows
                # one filterable catalogue, so collect its bounded pages here.
                next_page = result.get("next_page")
                seen = {int(query.get("page", ["1"])[0])}
                while next_page is not None:
                    require(type(next_page) is int and next_page not in seen and len(seen) < 20,
                            "Invalid shared catalogue pagination.", 502)
                    seen.add(next_page)
                    page = client.request(["catalog"], query={"page": [str(next_page)]})
                    result["listings"].extend(page["listings"])
                    next_page = page.get("next_page")
                result["next_page"] = None
                account = client.request(["session"])["account"]
                result["listings"] = [library.annotate(item, url, account) for item in result["listings"]]
            elif method == "GET" and len(parts) == 2 and parts[0] == "listings":
                account = client.request(["session"])["account"]
                result = library.annotate(result, url, account)
            elif method == "GET" and parts[:1] == ["media"]:
                value = image_data(result["image"])
                header, encoded = value.split(",", 1)
                result, mime = base64.b64decode(encoded), header[5:].split(";")[0]
        if isinstance(result, dict):
            result.update(client.metadata())
        status = 200
    except MarketError as exc:
        result, status = {"error": str(exc)}, exc.status
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, UnicodeError, RecursionError):
        result, status = {"error": "Invalid shared service response or request."}, 502
    except OSError:
        result, status = {"error": "Shared service is unavailable. Please retry."}, 502
    value = cookie_header(client.port, client.cookie) if client and client.cookie is not None else None
    return result, status, value, mime
