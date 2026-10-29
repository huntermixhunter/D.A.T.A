"""Public account/catalog API used by the standalone community service.

Only explicit marketplace fields cross this boundary. Installation, runtime,
private dashboard profiles and bridge tools have no public route here.
"""
from __future__ import annotations

import base64
import json
import re
import time

from marketplace import (DISCLAIMER, MAX_BODY, MarketError, image_data,
                         parse_package, require)


MAX_RESPONSE = 5 * 1024 * 1024
IDENTIFIER = re.compile(r"(?:[0-9a-f]{32}|library-[a-z0-9][a-z0-9-]{0,79}|data-library)")
VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}")
ACCOUNT_FIELDS = {
    "register": {"username", "name", "password", "avatar", "email"},
    "login": {"username", "password"}, "logout": set(), "me": set(),
    "profile": {"name", "avatar"},
    "credentials": {"current_password", "username", "email", "password"},
    "wallets": {"current_password", "wallets"},
}
DOWNLOAD_FIELDS = {"version", "sha256", "permissions", "acknowledge"}


def body_limit(parts):
    if parts == ["listings"]:
        return MAX_BODY
    if parts in (["account", "register"], ["account", "profile"]):
        return 720 * 1024
    return 16 * 1024


def validate_request(parts, method, data, query):
    require(method in ("GET", "POST"), "Method not supported.", 405)
    require(isinstance(parts, list) and all(isinstance(p, str) for p in parts),
            "Unknown marketplace endpoint.", 404)
    allowed = None
    if method == "GET":
        if parts in (["session"], ["catalog"]):
            allowed = set()
        elif len(parts) == 2 and parts[0] == "listings" and IDENTIFIER.fullmatch(parts[1]):
            allowed = set()
        elif (len(parts) == 4 and parts[0] == "listings" and
              IDENTIFIER.fullmatch(parts[1]) and parts[2] == "versions" and VERSION.fullmatch(parts[3])):
            allowed = set()
        elif (len(parts) == 3 and parts[0] == "media" and parts[1] in ("listing", "account")
              and IDENTIFIER.fullmatch(parts[2])):
            allowed = set()
    elif len(parts) == 2 and parts[0] == "account":
        allowed = ACCOUNT_FIELDS.get(parts[1])
    elif parts == ["listings"]:
        allowed = {"listing_id", "title", "description", "price_cents", "rights_confirmed", "package", "image"}
    elif parts == ["package-status"]:
        allowed = {"versions"}
    elif len(parts) == 3 and parts[0] == "listings" and IDENTIFIER.fullmatch(parts[1]):
        allowed = {"download": DOWNLOAD_FIELDS, "review": {"rating", "body"}}.get(parts[2])
    require(allowed is not None, "Unknown marketplace endpoint.", 404)
    require(isinstance(data, dict) and set(data) <= allowed, "Unsupported marketplace fields.")
    for key, value in data.items():
        if key == "versions":
            require(isinstance(value, list) and len(value) <= 100, "Invalid package status list.")
            for entry in value:
                require(isinstance(entry, dict) and set(entry) == {"listing_id", "version"} and
                        isinstance(entry["listing_id"], str) and IDENTIFIER.fullmatch(entry["listing_id"]) and
                        isinstance(entry["version"], str) and VERSION.fullmatch(entry["version"]),
                        "Invalid package status entry.")
        elif key in ("permissions", "wallets"):
            require(isinstance(value, list), "Invalid marketplace field value.")
            if key == "permissions":
                require(len(value) <= 40 and all(isinstance(v, str) and len(v) <= 100 for v in value),
                        "Invalid permission list.")
            else:
                from marketplace import wallet_list
                wallet_list(value)
        elif key in ("acknowledge", "rights_confirmed"):
            require(type(value) is bool, "Invalid acknowledgement.")
        elif key in ("rating", "price_cents"):
            require(type(value) is int, "Invalid marketplace number.")
        else:
            require(isinstance(value, str), "Invalid marketplace field value.")
    query_fields = {"page"} if parts == ["catalog"] and method == "GET" else set()
    require(isinstance(query, dict) and set(query) <= query_fields, "Unsupported marketplace query.")
    if query:
        values = query["page"]
        require(isinstance(values, list) and len(values) == 1 and
                isinstance(values[0], str) and re.fullmatch(r"[1-9][0-9]{0,4}", values[0]), "Invalid catalogue page.")
    raw = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    require(len(raw) <= body_limit(parts), "Marketplace request is too large.", 413)
    return raw


def version_status(store, lid, version):
    with store.connection() as db:
        row = db.execute("SELECT * FROM packages WHERE listing_id=? AND version=?", (lid, version)).fetchone()
        require(row, "Package version not found.", 404)
        return {"version": version, "sha256": row["sha256"], "security_review": store.package_review(row)}


def download(store, lid, data, account):
    """Issue a download receipt. A receipt does not activate a device runtime."""
    require(account, "Sign in to download extensions.", 401)
    with store.connection() as db:
        item = store._listing(db, lid, account)
        require(item["price_cents"] == 0, "Paid checkout is not enabled. No payment was taken.", 402)
        require(data.get("version") == item["latest"] and data.get("sha256") == item["sha256"],
                "Package changed. Open its details and review the current version.", 409)
        require(data.get("permissions") == item["manifest"]["permissions"], "Review the declared permissions first.", 409)
        require(data.get("acknowledge") is True, "Acknowledge the install notice before continuing.")
        require(item["review_status"] != "blocked", "This version was rejected by security review.", 403)
        row = db.execute("SELECT archive FROM packages WHERE listing_id=? AND version=?", (lid, item["latest"])).fetchone()
        raw = bytes(row["archive"])
        require(parse_package(raw)["sha256"] == item["sha256"], "Package integrity check failed.", 409)
        now = int(time.time())
        db.execute("INSERT INTO receipts VALUES (?,?,?,?) ON CONFLICT(listing_id,account_id) "
                   "DO UPDATE SET version=excluded.version,installed=excluded.installed",
                   (lid, account["id"], item["latest"], now))
        store._audit(db, "download", account["id"], lid, item["latest"], item["sha256"])
        return {"account": account, "listing": item, "package": base64.b64encode(raw).decode("ascii")}


def route(store, parts, method, data, account, token, peer, query):
    if len(parts) == 2 and parts[0] == "account":
        return store.account_action(parts[1], data, account, token, peer)
    if method == "GET":
        if parts == ["session"]:
            return {"account": account}, None
        if parts == ["catalog"]:
            page = int(query.get("page", ["1"])[0])
            size = 25
            with store.connection() as db:
                ids = [row[0] for row in db.execute(
                    "SELECT id FROM listings ORDER BY first_party DESC,updated DESC,id LIMIT ? OFFSET ?",
                    (size + 1, (page - 1) * size))]
                listings = [store._listing(db, lid, account) for lid in ids[:size]]
            return {"listings": listings, "next_page": page + 1 if len(ids) > size else None,
                    "disclaimer": DISCLAIMER, "checkout_enabled": False}, None
        if len(parts) == 2 and parts[0] == "listings":
            return store.detail(parts[1], account), None
        if len(parts) == 4 and parts[0] == "listings":
            return version_status(store, parts[1], parts[3]), None
        if len(parts) == 3 and parts[0] == "media":
            raw, mime = store.media(parts[1], parts[2])
            return {"image": f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")}, None
    elif parts == ["package-status"]:
        statuses = []
        for entry in data.get("versions", []):
            try:
                status = version_status(store, entry["listing_id"], entry["version"])
            except MarketError as exc:
                if exc.status != 404:
                    raise
                status = {"version": entry["version"], "unavailable": True}
            statuses.append(dict(status, listing_id=entry["listing_id"]))
        return {"versions": statuses}, None
    elif parts == ["listings"]:
        require(account, "Sign in to submit a listing.", 401)
        store.rate_limit("upload:" + account["id"], 20)
        return store.submit(data, account), None
    elif len(parts) == 3 and parts[0] == "listings":
        if parts[2] == "download":
            return download(store, parts[1], data, account), None
        if parts[2] == "review":
            return store.leave_review(parts[1], data, account), None
    raise MarketError("Unknown marketplace endpoint.", 404)
