"""Device-owned installs, isolated by service URL and shared account ID.

Contains validated declarative packages only. No shared password, session,
private account fields, local profile, or conversation is stored here.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager

from marketplace import (MAX_ARCHIVE, MarketError, Marketplace, decode_base64,
                         parse_package, require, text_field)


class DeviceExtensions:
    def __init__(self, store):
        self.path = store.directory / "shared_extensions.sqlite3"
        self.lock = threading.RLock()
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS packages (
                    service TEXT NOT NULL, listing_id TEXT NOT NULL, version TEXT NOT NULL,
                    sha256 TEXT NOT NULL, archive BLOB NOT NULL, manifest TEXT NOT NULL,
                    review TEXT, title TEXT NOT NULL,
                    PRIMARY KEY(service, listing_id, version));
                CREATE TABLE IF NOT EXISTS installations (
                    service TEXT NOT NULL, account_id TEXT NOT NULL, listing_id TEXT NOT NULL,
                    version TEXT NOT NULL, sha256 TEXT NOT NULL, enabled INTEGER NOT NULL,
                    installed INTEGER NOT NULL,
                    PRIMARY KEY(service, account_id, listing_id));
                CREATE TABLE IF NOT EXISTS history (
                    service TEXT NOT NULL, account_id TEXT NOT NULL, listing_id TEXT NOT NULL,
                    version TEXT NOT NULL, sha256 TEXT NOT NULL,
                    PRIMARY KEY(service, account_id, listing_id, version));
            """)

    @contextmanager
    def connection(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    def records(self, service, account):
        if not account:
            return []
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM installations WHERE service=? AND account_id=? ORDER BY installed,listing_id",
                (service, account["id"]))]

    def annotate(self, item, service, account):
        records = {row["listing_id"]: row for row in self.records(service, account)}
        row = records.get(item["id"])
        item["installation"] = {key: row[key] for key in ("version", "sha256", "enabled", "installed")} if row else None
        item["update_available"] = bool(row and row["version"] != item["latest"])
        if "versions" in item:
            with self.connection() as db:
                history = {(r["version"], r["sha256"]) for r in db.execute(
                    "SELECT version,sha256 FROM history WHERE service=? AND account_id=? AND listing_id=?",
                    (service, account["id"] if account else "", item["id"]))}
            for version in item["versions"]:
                version["was_installed"] = (version["version"], version["sha256"]) in history
        return item

    def install(self, service, account, result, data):
        require(account and result.get("account", {}).get("id") == account["id"], "Shared account changed. Sign in again.", 409)
        item = result["listing"]
        raw = decode_base64(result.get("package"), MAX_ARCHIVE)
        package = parse_package(raw)
        manifest = package["manifest"]
        require(item["price_cents"] == 0 and data.get("acknowledge") is True, "Download was not approved.", 409)
        require(data.get("sha256") == item["sha256"] == package["sha256"] and
                data.get("version") == item["latest"] == manifest["version"] and
                data.get("permissions") == manifest["permissions"] and item["manifest"] == manifest,
                "Downloaded package does not match the version and permissions you approved.", 409)
        review = item.get("security_review")
        require(not review or review.get("verdict") != "rejected", "This version was rejected by security review.", 403)
        title = text_field(item["title"], "Listing title", 80)
        lid, uid = item["id"], account["id"]
        with self.connection() as db:
            prior = db.execute("SELECT sha256 FROM packages WHERE service=? AND listing_id=? AND version=?",
                               (service, lid, manifest["version"])).fetchone()
            require(not prior or prior[0] == package["sha256"], "A published package version changed its bytes.", 409)
            existing = db.execute("SELECT 1 FROM installations WHERE service=? AND account_id=? AND listing_id=?", (service, uid, lid)).fetchone()
            count = db.execute("SELECT count(*) FROM installations WHERE service=? AND account_id=?", (service, uid)).fetchone()[0]
            require(existing or count < 100, "This account has reached 100 installed extensions on this device.", 409)
            db.execute("INSERT INTO packages VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(service,listing_id,version) "
                       "DO UPDATE SET review=excluded.review,title=excluded.title",
                       (service, lid, manifest["version"], package["sha256"], raw, json.dumps(manifest),
                        json.dumps(review) if review else None, title))
            db.execute("INSERT INTO installations VALUES (?,?,?,?,?,?,?) ON CONFLICT(service,account_id,listing_id) "
                       "DO UPDATE SET version=excluded.version,sha256=excluded.sha256,enabled=1,installed=excluded.installed",
                       (service, uid, lid, manifest["version"], package["sha256"], 1, int(time.time())))
            db.execute("INSERT OR IGNORE INTO history VALUES (?,?,?,?,?)", (service, uid, lid, manifest["version"], package["sha256"]))
        return self.annotate(item, service, account)

    def check_status(self, service, lid, version, status):
        with self.connection() as db:
            row = db.execute("SELECT * FROM packages WHERE service=? AND listing_id=? AND version=?", (service, lid, version)).fetchone()
            require(row, "Installed package not found.", 404)
            require(not status.get("unavailable") and status.get("version") == version and status.get("sha256") == row["sha256"],
                    "Installed package is no longer available or its integrity changed.", 409)
            review = status.get("security_review")
            db.execute("UPDATE packages SET review=? WHERE service=? AND listing_id=? AND version=?",
                       (json.dumps(review) if review else None, service, lid, version))
            if review and review.get("verdict") == "rejected":
                db.execute("UPDATE installations SET enabled=0 WHERE service=? AND listing_id=? AND version=?",
                           (service, lid, version))
        require(not review or review.get("verdict") != "rejected", "This version was rejected by security review.", 403)

    def manage(self, service, account, lid, action, data, client):
        require(account, "Sign in to manage installed extensions.", 401)
        row = next((r for r in self.records(service, account) if r["listing_id"] == lid), None)
        require(row, "Extension is not installed on this device.", 404)
        require(action in ("enable", "disable", "uninstall", "rollback"), "Unknown extension action.", 404)
        version, digest = row["version"], row["sha256"]
        if action == "rollback":
            version, digest = data.get("version"), data.get("sha256")
            with self.connection() as db:
                prior = db.execute("SELECT 1 FROM history WHERE service=? AND account_id=? AND listing_id=? AND version=? AND sha256=?",
                                   (service, account["id"], lid, version, digest)).fetchone()
            require(prior and data.get("acknowledge") is True, "Only a previously approved version on this device can be restored.", 409)
        if action in ("enable", "rollback"):
            self.check_status(service, lid, version, client.request(["listings", lid, "versions", version]))
        with self.connection() as db:
            key = (service, account["id"], lid)
            if action == "uninstall":
                db.execute("DELETE FROM installations WHERE service=? AND account_id=? AND listing_id=?", key)
            elif action == "disable":
                db.execute("UPDATE installations SET enabled=0 WHERE service=? AND account_id=? AND listing_id=?", key)
            else:
                db.execute("UPDATE installations SET version=?,sha256=?,enabled=1 WHERE service=? AND account_id=? AND listing_id=?",
                           (version, digest, *key))
        return {"ok": True}

    def runtime(self, service, account, client):
        rows = [r for r in self.records(service, account) if r["enabled"]]
        if not rows:
            return []
        statuses = client.request(["package-status"], "POST", {
            "versions": [{"listing_id": r["listing_id"], "version": r["version"]} for r in rows]})
        lookup = {(s["listing_id"], s["version"]): s for s in statuses["versions"]}
        result = []
        for row in rows:
            lid, version = row["listing_id"], row["version"]
            try:
                self.check_status(service, lid, version, lookup.get((lid, version), {"unavailable": True}))
                with self.connection() as db:
                    stored = db.execute("SELECT * FROM packages WHERE service=? AND listing_id=? AND version=?", (service, lid, version)).fetchone()
                package = parse_package(bytes(stored["archive"]))
                require(package["sha256"] == row["sha256"], "Package integrity failure.", 409)
                review = Marketplace.package_review(stored)
                require(not review or review["verdict"] != "rejected", "Package review rejected.", 403)
            except MarketError:
                continue
            result.append({"id": lid, "title": stored["title"], "version": version,
                           "sha256": row["sha256"], "components": package["runtime"]})
        return result


def get_library(store):
    with store.lock:
        if not hasattr(store, "_device_extensions"):
            store._device_extensions = DeviceExtensions(store)
        return store._device_extensions
