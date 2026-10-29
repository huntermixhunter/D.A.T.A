"""One opt-in, origin-bound session for shared DATA services."""
from __future__ import annotations

import hashlib
import os
import re
from urllib.parse import urlencode, urlsplit

import community_transport as transport
import shared_service as service
from marketplace import MarketError, SESSION_SECONDS, require


def configured_url():
    value = os.environ.get("DATA_SERVICES_URL", "").strip()
    if not value:
        return None
    return transport.normalized_origin(value,
        "DATA_SERVICES_URL must be an HTTPS service URL; HTTP is allowed only for localhost development.",
        allow_path=True)


def cookie_name(port):
    return f"data_services_session_{int(port)}"


def cookie_header(port, value):
    require(isinstance(value, str) and (not value or re.fullmatch(r"[0-9a-f]{32}\.[A-Za-z0-9_-]{43}", value)),
            "Invalid shared session.", 502)
    return (f"{cookie_name(port)}={value}; HttpOnly; SameSite=Strict; "
            f"Path=/marketplace; Max-Age={SESSION_SECONDS if value else 0}")


class SharedClient:
    def __init__(self, handler, url):
        self.url = url
        self.port = handler.server.server_address[1]
        self.token = transport._cookie_token(handler, url, cookie_name(self.port))
        self.cookie = None

    def request(self, parts, method="GET", data=None, query=None, forum=False):
        data, query = data or {}, query or {}
        validator = transport.validate_request if forum else service.validate_request
        raw = validator(parts, method, data, query)
        prefix = "/community/" if forum else "/marketplace/"
        target = urlsplit(self.url).path + prefix + "/".join(parts)
        if query:
            target += "?" + urlencode(query, doseq=True)
        result, status = transport._request(self.url, target, method, raw, self.token,
                                             service.MAX_RESPONSE if not forum else transport.MAX_RESPONSE)
        issued = result.pop(transport.TOKEN_FIELD, None)
        session_action = (method == "POST" and parts in (
            ["account", "register"], ["account", "login"], ["account", "logout"]))
        if session_action and status == 200:
            require(isinstance(issued, str) and
                    (issued == "" if parts[1] == "logout" else bool(transport._TOKEN.fullmatch(issued))),
                    "Shared service did not return a valid session.", 502)
            self.cookie = hashlib.sha256(self.url.encode("ascii")).hexdigest()[:32] + "." + issued if issued else ""
            self.token = issued
        else:
            require(issued is None, "Shared service returned an unexpected session.", 502)
        # A wrong confirmation password also returns 401. Only clear an expired
        # session, otherwise a harmless typo would sign the owner out everywhere.
        if status == 401 and result.get("session_expired"):
            self.cookie = ""
            self.token = ""
        if status != 200:
            raise MarketError(result.get("error", "Shared service request failed."), status)
        return result

    def metadata(self):
        return {"mode": "shared", "identity_scope": "shared", "service_url": self.url,
                "community_origin": self.url}
