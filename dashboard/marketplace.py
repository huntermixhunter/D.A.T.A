"""Local extension registry, accounts and a data-only package runtime.

This module never executes package code or installs dependencies. Runtime state
belongs outside the dashboard static directory. Configured shared services use
an explicit allowlisted transport; private dashboard state remains local.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import re
import secrets
import sqlite3
import stat
import struct
import threading
import time
import zipfile
from contextlib import contextmanager
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = ROOT.parent / "users" / "marketplace"
MAX_ARCHIVE = 2 * 1024 * 1024
MAX_BODY = 5 * 1024 * 1024
MAX_FILE = 256 * 1024
MAX_EXPANDED = 1024 * 1024
PASSWORD_ROUNDS = 600_000
SESSION_SECONDS = 7 * 86400
POLICY_VERSION = "data-packages-1"
KINDS = {"theme", "skill", "tool", "page", "upgrade"}
TOKENS = {"--data-" + key for key in (
    "orange", "orange2", "yellow", "blue", "blue2", "lightblue", "purple",
    "teal", "red", "green", "pink", "bg", "bg2", "text", "text-dim")}
TOOL_OPERATIONS = {"word-count", "uppercase", "lowercase", "trim-lines", "deduplicate-lines", "sort-lines"}
PROXY_HEADERS = ("Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Real-Ip",
                 "Cf-Connecting-Ip", "Cf-Ray", "Cdn-Loop", "Via")
RESERVED_USERNAMES = {"data", "admin", "official", "sentinel", "support", "data-library"}
MAX_WALLETS = 12
# Receiving addresses the member chooses to record on their own account. Stored
# for their reference only: nothing in DATA signs, sweeps or sends to them.
WALLET_CHAINS = {
    "bitcoin": "Bitcoin", "ethereum": "Ethereum", "solana": "Solana", "base": "Base",
    "polygon": "Polygon", "arbitrum": "Arbitrum", "optimism": "Optimism",
    "bnb": "BNB Smart Chain", "avalanche": "Avalanche", "litecoin": "Litecoin",
    "dogecoin": "Dogecoin", "monero": "Monero", "cardano": "Cardano",
    "tron": "Tron", "other": "Other",
}
EVM_CHAINS = {"ethereum", "base", "polygon", "arbitrum", "optimism", "bnb", "avalanche"}
# A malformed receiving address loses funds, so each known family is shape-checked
# before it is stored. "other" keeps a conservative generic charset.
WALLET_PATTERNS = {
    "evm": re.compile(r"0x[0-9a-fA-F]{40}"),
    "solana": re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}"),
    "bitcoin": re.compile(r"(?:[13][1-9A-HJ-NP-Za-km-z]{25,34}|bc1[02-9ac-hj-np-z]{11,87})"),
    "litecoin": re.compile(r"(?:[LM3][1-9A-HJ-NP-Za-km-z]{25,34}|ltc1[02-9ac-hj-np-z]{11,87})"),
    "dogecoin": re.compile(r"D[1-9A-HJ-NP-Za-km-z]{25,34}"),
    "monero": re.compile(r"4[0-9AB][1-9A-HJ-NP-Za-km-z]{93}"),
    "cardano": re.compile(r"addr1[02-9ac-hj-np-z]{20,110}"),
    "tron": re.compile(r"T[1-9A-HJ-NP-Za-km-z]{33}"),
    "other": re.compile(r"[A-Za-z0-9:_.-]{8,128}"),
}
DISCLAIMER = ("Community packages are unverified unless a version-specific review is shown. "
              "A review reduces uncertainty; it is not a guarantee of safety. Skills can guide "
              "an agent to access files, services or paid tools. Read the permissions and "
              "instructions before use. Installation does not authorize paid actions.")


class MarketError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def require(condition, message, status=400):
    if not condition:
        raise MarketError(message, status)


def text_field(value, label, maximum, minimum=1):
    require(isinstance(value, str), f"{label} must be text.")
    value = value.strip()
    require(minimum <= len(value) <= maximum, f"{label} must be {minimum} to {maximum} characters.")
    require(not any(ord(c) < 32 and c not in "\n\t" for c in value), f"Invalid {label}.")
    return value


def username_field(value):
    """Usernames are the login identifier, so they are normalized before use."""
    username = text_field(value, "Username", 32, 3).lower()
    require(re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,31}", username),
            "Use letters, numbers, dots, hyphens or underscores.")
    return username


def password_field(value, label="Password"):
    require(isinstance(value, str) and 12 <= len(value) <= 128,
            f"{label} must be 12 to 128 characters.")
    require(not any(ord(c) < 32 or ord(c) == 127 for c in value), f"Invalid {label.lower()}.")
    return value


def email_field(value):
    """Optional and never verified: it is a note on the account, not a login or
    a recovery channel. Nothing is sent to it."""
    if value is None or value == "":
        return ""
    require(isinstance(value, str), "Email must be text.")
    email = value.strip()
    require(len(email) <= 254 and email.isascii(), "Email must be at most 254 ASCII characters.")
    require(re.fullmatch(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
                         r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                         r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+", email),
            "Enter an email address like you@example.com, or leave it empty.")
    return email


def wallet_list(value):
    """Validate the member's own receiving addresses. Shape errors are reported
    per row so a typo is corrected rather than silently stored."""
    require(isinstance(value, list), "Wallets must be a list.")
    require(len(value) <= MAX_WALLETS, f"Keep at most {MAX_WALLETS} wallet addresses.")
    wallets, seen = [], set()
    for index, entry in enumerate(value, 1):
        require(isinstance(entry, dict) and set(entry) <= {"chain", "address", "label"},
                f"Wallet {index} has unsupported fields.")
        chain = entry.get("chain")
        require(isinstance(chain, str) and chain in WALLET_CHAINS, f"Wallet {index} uses an unsupported network.")
        address = entry.get("address")
        require(isinstance(address, str), f"Wallet {index} address must be text.")
        address = address.strip()
        require(8 <= len(address) <= 128, f"Wallet {index} address must be 8 to 128 characters.")
        family = "evm" if chain in EVM_CHAINS else chain
        require(WALLET_PATTERNS[family].fullmatch(address),
                f"Wallet {index} is not a valid {WALLET_CHAINS[chain]} address.")
        label = entry.get("label", "")
        require(isinstance(label, str), f"Wallet {index} label must be text.")
        label = label.strip()
        require(len(label) <= 40, f"Wallet {index} label must be at most 40 characters.")
        require(not any(ord(c) < 32 for c in label), f"Wallet {index} label is invalid.")
        key = (chain, address.lower())
        require(key not in seen, f"Wallet {index} repeats an address already in the list.")
        seen.add(key)
        wallets.append({"chain": chain, "address": address, "label": label})
    return wallets


def stored_wallets(raw):
    """Read the wallet column back. Everything here was validated on the way in,
    so this re-checks defensively — but a row that fails is reported with
    `invalid: true` rather than dropped. Dropping it silently would hide an
    address the owner believes is saved, and the next save, rebuilt from what
    was shown, would erase it for good."""
    try:
        entries = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return []
    if not isinstance(entries, list):
        return []
    wallets = []
    for entry in entries[:MAX_WALLETS]:
        try:
            wallets.extend(wallet_list([entry]))
        except MarketError as exc:
            if not isinstance(entry, dict):
                continue
            wallets.append({
                "chain": entry.get("chain") if entry.get("chain") in WALLET_CHAINS else "other",
                "address": str(entry.get("address", ""))[:128],
                "label": str(entry.get("label", ""))[:40],
                "invalid": str(exc),
            })
    return wallets


def json_object(raw):
    def unique(pairs):
        result = {}
        for k, v in pairs:
            require(k not in result, "Duplicate JSON keys are not allowed.")
            result[k] = v
        return result
    try:
        obj = json.loads(raw, object_pairs_hook=unique,
                         parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        require(isinstance(obj, dict), "Expected a JSON object.")
        return obj
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise MarketError("Invalid JSON object.") from exc


def decode_base64(value, maximum):
    require(isinstance(value, str) and len(value) <= (maximum + 2) * 4 // 3 + 4,
            "Upload is too large.", 413)
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise MarketError("Invalid base64 upload.") from exc
    require(len(raw) <= maximum, "Upload is too large.", 413)
    return raw


def image_data(value):
    """Only raster PNG/JPEG; SVG, URLs and active document types are refused."""
    if not value:
        return ""
    require(isinstance(value, str), "Invalid image.")
    match = re.fullmatch(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)", value)
    require(match, "Choose a PNG or JPEG image.")
    raw = decode_base64(match[2], 512 * 1024)
    if match[1] == "png":
        require(len(raw) >= 33 and raw[:8] == b"\x89PNG\r\n\x1a\n" and raw[12:16] == b"IHDR",
                "Invalid PNG image.")
        width, height = struct.unpack(">II", raw[16:24])
    else:
        require(raw[:2] == b"\xff\xd8" and raw[-2:] == b"\xff\xd9", "Invalid JPEG image.")
        pos, width, height = 2, 0, 0
        while pos + 4 <= len(raw):
            require(raw[pos] == 255, "Invalid JPEG markers.")
            marker = raw[pos + 1]
            if marker == 255:
                pos += 1
                continue
            length = int.from_bytes(raw[pos + 2:pos + 4], "big")
            require(length >= 2 and pos + 2 + length <= len(raw), "Invalid JPEG length.")
            if marker in (0xc0, 0xc1, 0xc2):
                require(length >= 8, "Invalid JPEG dimensions.")
                height, width = struct.unpack(">HH", raw[pos + 5:pos + 9])
                break
            if marker == 0xda:
                break
            pos += length + 2
    require(0 < width <= 4096 and 0 < height <= 4096 and width * height <= 8_000_000,
            "Image dimensions must be at most 4096 pixels and 8 megapixels.")
    return value


def safe_name(name):
    require(isinstance(name, str) and 0 < len(name) <= 160, "Invalid package path.")
    require(re.fullmatch(r"[A-Za-z0-9_./-]+", name) and not name.startswith("/"), "Unsafe package path.")
    for part in name.split("/"):
        require(part not in ("", ".", "..") and not part.startswith(".") and not part.endswith("."),
                "Unsafe package path.")
        require(not re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?", part),
                "Reserved package path.")
    return name


def parse_package(raw):
    """Validate all bytes before installation. Archives are never extracted."""
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_ARCHIVE, "Package must be a ZIP under 2 MiB.", 413)
    files, names, expanded = {}, set(), 0
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            require(0 < len(entries) <= 40, "Package must contain 1 to 40 files.")
            for info in entries:
                # Directory entries are unnecessary. This also rejects aliases and links.
                name = safe_name(info.filename)
                require(name.casefold() not in names, "Duplicate package path.")
                names.add(name.casefold())
                mode = info.external_attr >> 16
                require(stat.S_IFMT(mode) in (0, stat.S_IFREG), "Links and directories are not allowed.")
                require(not info.flag_bits & 1, "Encrypted packages are not allowed.")
                require(info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), "Unsupported compression.")
                expanded += info.file_size
                require(info.file_size <= MAX_FILE and expanded <= MAX_EXPANDED,
                        "Expanded package is too large.", 413)
                require(info.file_size <= max(1024, info.compress_size * 100), "Suspicious compression ratio.")
                require(Path(name).suffix.lower() in (".json", ".md", ".txt"),
                        "Only JSON, Markdown and text files are supported. Executable code is not accepted.")
                with archive.open(info) as src:
                    content = src.read(MAX_FILE + 1)
                require(len(content) == info.file_size and len(content) <= MAX_FILE, "Invalid expanded file.")
                files[name] = content.decode("utf-8")
    except MarketError:
        raise
    except (zipfile.BadZipFile, UnicodeError, RuntimeError, OSError, ValueError, NotImplementedError) as exc:
        raise MarketError("Invalid ZIP package.") from exc
    require("manifest.json" in files, "Add manifest.json at the ZIP root.")
    manifest = json_object(files["manifest.json"])
    allowed = {"schema_version", "id", "version", "name", "description", "kind", "license",
               "permissions", "components", "external_costs"}
    require(set(manifest) <= allowed, "Unsupported manifest fields. Hooks and dependencies are not supported.")
    require(type(manifest.get("schema_version")) is int and manifest["schema_version"] == 1,
            "This dashboard supports package schema version 1.")
    require(isinstance(manifest.get("id"), str) and re.fullmatch(r"[a-z][a-z0-9-]{2,59}", manifest["id"]), "Invalid package id.")
    require(isinstance(manifest.get("version"), str) and re.fullmatch(r"(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})", manifest["version"]), "Use a version such as 1.0.0.")
    manifest["name"] = text_field(manifest.get("name"), "Package name", 80)
    manifest["description"] = text_field(manifest.get("description"), "Package description", 4000)
    manifest["license"] = text_field(manifest.get("license"), "License", 80)
    manifest["external_costs"] = text_field(manifest.get("external_costs", "None for installation."), "External costs", 500)
    require(isinstance(manifest.get("kind"), str) and manifest["kind"] in KINDS, "Unknown package type.")
    parts = manifest.get("components")
    require(isinstance(parts, list) and 1 <= len(parts) <= 8, "Add 1 to 8 components.")
    require(manifest["kind"] == "upgrade" or len(parts) == 1, "Multiple components require type upgrade.")
    permissions, runtime, used, findings = set(), [], {"manifest.json"}, []
    for part in parts:
        require(isinstance(part, dict) and set(part) == {"kind", "entry", "label"}, "Invalid component fields.")
        kind = part["kind"]
        require(isinstance(kind, str) and kind in KINDS - {"upgrade"}, "Invalid component type.")
        require(manifest["kind"] in ("upgrade", kind), "Package and component types do not match.")
        entry = safe_name(part["entry"])
        require(entry in files and entry not in used, "Missing or duplicate component entry.")
        used.add(entry)
        label = text_field(part["label"], "Component label", 60)
        if kind == "skill":
            require(entry.endswith(".md"), "Skills require Markdown instructions.")
            require(0 < len(files[entry]) <= 40_000, "Skill instructions must be under 40,000 characters.")
            data = {"instructions": files[entry]}
            permissions.add("agent.instructions")
            findings.append("Skill instructions can influence an agent's actions. Review them before each use.")
            if re.search(r"(?i)(curl[^\n]*\|\s*(sh|bash)|powershell[^\n]*-enc|ignore (all |previous )?instructions|BEGIN .*PRIVATE KEY|sk-[A-Za-z0-9]{24,})", files[entry]):
                raise MarketError("Skill contains a blocked credential or high-risk instruction pattern.")
        else:
            require(entry.endswith(".json"), "Themes, tools and pages require JSON entry files.")
            data = json_object(files[entry])
            if kind == "theme":
                require(set(data) == {"tokens"} and isinstance(data["tokens"], dict), "Themes require a tokens object.")
                require(1 <= len(data["tokens"]) <= len(TOKENS), "Invalid theme token count.")
                for key, value in data["tokens"].items():
                    require(key in TOKENS and isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value),
                            "Only supported color tokens with six-digit hex values are allowed.")
                permissions.add("appearance.colors")
            elif kind == "page":
                require(set(data) == {"blocks"} and isinstance(data["blocks"], list) and 1 <= len(data["blocks"]) <= 40,
                        "Pages require 1 to 40 text blocks.")
                for block in data["blocks"]:
                    require(isinstance(block, dict) and set(block) == {"type", "text"} and
                            block["type"] in ("heading", "paragraph", "check"), "Unsupported page block.")
                    text_field(block["text"], "Block text", 4000)
                permissions.add("navigation.page")
            else:
                require(set(data) == {"operations", "placeholder"}, "Unsupported tool fields.")
                ops = data["operations"]
                require(isinstance(ops, list) and 1 <= len(ops) <= 6 and
                        all(isinstance(op, str) and op in TOOL_OPERATIONS for op in ops) and len(set(ops)) == len(ops),
                        "Tools may use the supported text operations only.")
                text_field(data["placeholder"], "Tool placeholder", 300)
                permissions.update(("navigation.page", "text.local"))
        runtime.append({"kind": kind, "label": label, "entry": entry, "data": data})
    declared = manifest.get("permissions")
    require(isinstance(declared, list) and all(isinstance(p, str) for p in declared)
            and len(set(declared)) == len(declared) and set(declared) == permissions,
            "Declared permissions must exactly match the package components.")
    require(all(name in used or (name.lower().endswith((".md", ".txt")) and
            Path(name).name.lower().startswith(("readme", "license", "notice"))) for name in files),
            "Package contains undeclared files.")
    return {"manifest": manifest, "files": files, "runtime": runtime,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "scan": {"policy": POLICY_VERSION, "risk": "moderate" if "agent.instructions" in permissions else "low",
                     "findings": list(dict.fromkeys(findings)) or ["Data-only components. No scripts, network requests or installation hooks."],
                     "method": "Automatic schema and static checks", "scanned_at": int(time.time())}}


def make_archive(files):
    """Deterministic bytes, so review hashes survive copies and fresh installs."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, content.encode("utf-8"))
    return out.getvalue()


def starter_package(kind):
    require(kind in KINDS, "Unknown starter type.", 404)
    examples = {
        "theme": ("theme.json", {"tokens": {"--data-orange": "#A4C8FF"}}, ["appearance.colors"]),
        "page": ("page.json", {"blocks": [{"type": "heading", "text": "My page"}, {"type": "paragraph", "text": "Add useful information here."}, {"type": "check", "text": "My first checklist item"}]}, ["navigation.page"]),
        "tool": ("tool.json", {"operations": ["word-count", "trim-lines"], "placeholder": "Paste your text here."}, ["navigation.page", "text.local"]),
        "skill": ("SKILL.md", "# My skill\n\nDescribe when to use this skill and its steps. Respect the user's instructions and approval requirements.\n", ["agent.instructions"]),
    }
    types = ["theme", "page"] if kind == "upgrade" else [kind]
    files, parts, perms = {}, [], set()
    for component in types:
        entry, content, component_permissions = examples[component]
        files[entry] = content if isinstance(content, str) else json.dumps(content, indent=2)
        parts.append({"kind": component, "entry": entry, "label": "My " + component})
        perms.update(component_permissions)
    manifest = {"schema_version": 1, "id": "my-" + kind, "version": "1.0.0", "name": "My " + kind,
                "description": "Describe what this extension adds to the dashboard.", "kind": kind,
                "license": "Proprietary", "permissions": sorted(perms), "external_costs": "Describe any external costs before submitting.", "components": parts}
    files["manifest.json"] = json.dumps(manifest, indent=2)
    files["README.md"] = "# Package starter\n\nEdit the manifest and component files. Set your license, describe costs accurately, and include the license text. ZIP the files with manifest.json at the root.\n"
    return make_archive(files)


class Marketplace:
    def __init__(self, directory=DEFAULT_DATA, seed=True):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = self.directory / "marketplace.sqlite3"
        self.lock = threading.RLock()
        self.attempts = {}
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                    avatar TEXT NOT NULL DEFAULT '', password TEXT NOT NULL, salt TEXT NOT NULL,
                    created INTEGER NOT NULL, email TEXT NOT NULL DEFAULT '',
                    wallets TEXT NOT NULL DEFAULT '[]');
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id), expires INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS listings (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES accounts(id), title TEXT NOT NULL,
                    description TEXT NOT NULL, image TEXT NOT NULL DEFAULT '', price_cents INTEGER NOT NULL,
                    kind TEXT NOT NULL, package_id TEXT NOT NULL, latest TEXT NOT NULL, created INTEGER NOT NULL,
                    updated INTEGER NOT NULL, first_party INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS packages (
                    listing_id TEXT NOT NULL REFERENCES listings(id), version TEXT NOT NULL, sha256 TEXT NOT NULL,
                    archive BLOB NOT NULL, manifest TEXT NOT NULL, scan TEXT NOT NULL, review TEXT,
                    PRIMARY KEY(listing_id, version));
                CREATE TABLE IF NOT EXISTS installations (
                    listing_id TEXT NOT NULL REFERENCES listings(id), version TEXT NOT NULL, sha256 TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1, installed_by TEXT NOT NULL REFERENCES accounts(id), installed INTEGER NOT NULL,
                    PRIMARY KEY(listing_id, installed_by));
                CREATE TABLE IF NOT EXISTS receipts (
                    listing_id TEXT NOT NULL, account_id TEXT NOT NULL, version TEXT NOT NULL, installed INTEGER NOT NULL,
                    PRIMARY KEY(listing_id, account_id));
                CREATE TABLE IF NOT EXISTS reviews (
                    listing_id TEXT NOT NULL REFERENCES listings(id), account_id TEXT NOT NULL REFERENCES accounts(id),
                    rating INTEGER NOT NULL, body TEXT NOT NULL, version TEXT NOT NULL, updated INTEGER NOT NULL,
                    PRIMARY KEY(listing_id, account_id));
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, event TEXT NOT NULL, actor TEXT NOT NULL, listing_id TEXT,
                    version TEXT, sha256 TEXT, created INTEGER NOT NULL);
            """)
            # Account settings added after the first release. Existing databases
            # gain the columns in place; nobody has to recreate an account.
            columns = {r["name"] for r in db.execute("PRAGMA table_info(accounts)")}
            for column, default in (("email", "''"), ("wallets", "'[]'")):
                if column not in columns:
                    db.execute(f"ALTER TABLE accounts ADD COLUMN {column} TEXT NOT NULL DEFAULT {default}")
            # Preserve installations created by the early device-wide prototype.
            # `SELECT *` used to be enough only for the one legacy shape that
            # already had installed_by holding a live account id. The prototype
            # that shipped without the column raised on the column count, and a
            # row naming a since-deleted account raised on the foreign key —
            # and because DDL autocommits, the rename and the new table both
            # survived the failure. The next launch then skipped this block
            # (the primary key already matched) and every install was stranded
            # in installations_legacy. So: name the columns, resolve an owner,
            # skip rows that can no longer be attributed, and run it inside an
            # explicit transaction so a failure actually rolls back.
            primary = [r["name"] for r in db.execute("PRAGMA table_info(installations)") if r["pk"]]
            if primary == ["listing_id"]:
                legacy_columns = {r["name"] for r in db.execute("PRAGMA table_info(installations)")}
                db.execute("BEGIN IMMEDIATE")
                try:
                    db.execute("ALTER TABLE installations RENAME TO installations_legacy")
                    db.execute("CREATE TABLE installations (listing_id TEXT NOT NULL REFERENCES listings(id), version TEXT NOT NULL, sha256 TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, installed_by TEXT NOT NULL REFERENCES accounts(id), installed INTEGER NOT NULL, PRIMARY KEY(listing_id,installed_by))")
                    insert = ("INSERT OR IGNORE INTO installations "
                              "(listing_id,version,sha256,enabled,installed_by,installed) SELECT "
                              "old.listing_id,old.version,old.sha256,old.enabled,{owner},old.installed "
                              "FROM installations_legacy old {join} "
                              "WHERE EXISTS (SELECT 1 FROM listings l WHERE l.id=old.listing_id)")
                    if "installed_by" in legacy_columns:
                        db.execute(insert.format(owner="old.installed_by", join="") +
                                   " AND EXISTS (SELECT 1 FROM accounts a WHERE a.id=old.installed_by)")
                    else:
                        # No owner was ever recorded: the prototype installed for
                        # the whole device. Give each row to every real account so
                        # nobody's extensions quietly vanish on upgrade.
                        db.execute(insert.format(owner="a.id", join="CROSS JOIN accounts a") +
                                   " AND a.id<>'data-library'")
                    db.execute("DROP TABLE installations_legacy")
                    db.execute("COMMIT")
                except Exception:
                    db.execute("ROLLBACK")
                    raise
        if seed:
            self.seed_catalog()

    @contextmanager
    def connection(self):
        with self.lock:
            db = sqlite3.connect(self.db, timeout=10)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            try:
                with db:
                    yield db
            finally:
                db.close()

    @staticmethod
    def public_account(row):
        if not row:
            return None
        result = {key: row[key] for key in ("id", "username", "name")}
        result["avatar"] = "/marketplace/media/account/" + row["id"] if row["avatar"] else ""
        return result

    @classmethod
    def private_account(cls, row):
        """The owner's own view. Email and wallet addresses are returned here
        only, never through the catalog, posts, reviews or any public account."""
        result = cls.public_account(row)
        if not result:
            return None
        keys = row.keys()
        result["email"] = row["email"] if "email" in keys else ""
        result["wallets"] = stored_wallets(row["wallets"]) if "wallets" in keys else []
        result["created"] = row["created"]
        return result

    def verify_password(self, account_id, password, label="Current password"):
        """Required before any credential or payout-address change, so a borrowed
        session alone cannot take over the account or redirect an address."""
        require(isinstance(password, str) and password, f"{label} is required.")
        with self.connection() as db:
            row = db.execute("SELECT password,salt FROM accounts WHERE id=?", (account_id,)).fetchone()
        require(row and row["salt"], "Sign in first.", 401)
        hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(row["salt"]), PASSWORD_ROUNDS).hex()
        require(hmac.compare_digest(hashed, row["password"]), "That password is incorrect.", 401)

    def rate_limit(self, key, limit=12):
        now = time.time()
        with self.lock:
            self.attempts = {k: v for k, v in self.attempts.items() if v[0] > now - 300}
            started, count = self.attempts.get(key, (now, 0))
            require(count < limit and len(self.attempts) < 512, "Too many attempts. Try again in five minutes.", 429)
            self.attempts[key] = (started, count + 1)

    def clear_rate_limit(self, key):
        """Called after a change the password actually authorized. Without this
        the budget counts successes, so twelve honest saves lock the owner out
        of their own settings for five minutes."""
        with self.lock:
            self.attempts.pop(key, None)

    def authenticate(self, token):
        if not token or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return None
        hashed = hashlib.sha256(token.encode()).hexdigest()
        with self.connection() as db:
            row = db.execute("SELECT a.* FROM sessions s JOIN accounts a ON a.id=s.account_id WHERE s.token_hash=? AND s.expires>?",
                             (hashed, int(time.time()))).fetchone()
        return self.public_account(row)

    def account_action(self, action, data, account, token, peer):
        if action == "logout":
            with self.connection() as db:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
            return {"account": None}, ""
        if action == "profile":
            require(account, "Sign in first.", 401)
            name = text_field(data.get("name"), "Display name", 60)
            with self.connection() as db:
                current = db.execute("SELECT avatar FROM accounts WHERE id=?", (account["id"],)).fetchone()
                avatar = image_data(data["avatar"]) if "avatar" in data else current["avatar"]
                db.execute("UPDATE accounts SET name=?,avatar=? WHERE id=?", (name, avatar, account["id"]))
            return {"account": self.public_account(dict(account, name=name, avatar=avatar))}, None
        if action in ("me", "credentials", "wallets"):
            require(account, "Sign in first.", 401)
            return self.account_settings(action, data, account, token, peer), None
        require(action in ("register", "login"), "Unknown account action.", 404)
        self.rate_limit("auth:" + peer)
        username = username_field(data.get("username"))
        password = data.get("password")
        require(isinstance(password, str) and 12 <= len(password) <= 128, "Use a password of 12 to 128 characters.")
        if action == "register":
            require(username not in RESERVED_USERNAMES, "That username is reserved.")
            name = text_field(data.get("name"), "Display name", 60)
            avatar = image_data(data.get("avatar", ""))
            email = email_field(data.get("email", ""))
            salt = secrets.token_hex(16)
            hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PASSWORD_ROUNDS).hex()
            uid = secrets.token_hex(16)
            try:
                with self.connection() as db:
                    require(db.execute("SELECT count(*) FROM accounts").fetchone()[0] < 1000, "Local account limit reached.", 409)
                    db.execute("INSERT INTO accounts (id,username,name,avatar,password,salt,created,email,wallets)"
                               " VALUES (?,?,?,?,?,?,?,?,'[]')",
                               (uid, username, name, avatar, hashed, salt, int(time.time()), email))
            except sqlite3.IntegrityError as exc:
                raise MarketError("That username is already in use.", 409) from exc
        else:
            with self.connection() as db:
                row = db.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()
            salt = row["salt"] if row and row["salt"] else "00" * 16
            hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PASSWORD_ROUNDS).hex()
            require(row and hmac.compare_digest(hashed, row["password"]), "Username or password is incorrect.", 401)
            uid = row["id"]
        new_token = secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute("DELETE FROM sessions WHERE expires<=?", (int(time.time()),))
            # Bound per-account sessions and revoke the browser's previous session.
            db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
            db.execute("DELETE FROM sessions WHERE account_id=? AND token_hash NOT IN (SELECT token_hash FROM sessions WHERE account_id=? ORDER BY expires DESC LIMIT 9)", (uid, uid))
            db.execute("INSERT INTO sessions VALUES (?,?,?)", (hashlib.sha256(new_token.encode()).hexdigest(), uid, int(time.time()) + SESSION_SECONDS))
            row = db.execute("SELECT * FROM accounts WHERE id=?", (uid,)).fetchone()
        return {"account": self.public_account(row)}, new_token

    def account_settings(self, action, data, account, token, peer):
        """Account page actions for the signed-in owner. Everything that changes
        a credential or a payout address re-checks the password first, so the
        session cookie alone is never enough to lock the owner out."""
        # Keyed on the account, not the peer: every local request arrives from
        # loopback, so a peer key would be one global budget shared by everyone.
        # The peer key stays as an outer bound on password guessing.
        budget = "settings:" + account["id"]
        if action == "me":
            require(not data, "Unsupported account fields.")
            # Reading does not take the password, so throttle it — a borrowed
            # session should not be able to sweep the account in a loop.
            self.rate_limit("read:" + account["id"], 60)
            with self.connection() as db:
                row = db.execute("SELECT * FROM accounts WHERE id=?", (account["id"],)).fetchone()
            require(row, "Sign in first.", 401)
            return {"account": self.private_account(row)}
        self.rate_limit(budget)
        self.rate_limit("settings-peer:" + peer, 60)
        require(isinstance(data, dict), "Invalid account request.")
        if action == "wallets":
            require(set(data) <= {"current_password", "wallets"}, "Unsupported account fields.")
            self.verify_password(account["id"], data.get("current_password"))
            wallets = wallet_list(data.get("wallets"))
            with self.connection() as db:
                db.execute("UPDATE accounts SET wallets=? WHERE id=?",
                           (json.dumps(wallets, ensure_ascii=False), account["id"]))
                row = db.execute("SELECT * FROM accounts WHERE id=?", (account["id"],)).fetchone()
            self.clear_rate_limit(budget)
            return {"account": self.private_account(row), "changed": ["wallets"]}
        require(set(data) <= {"current_password", "username", "email", "password"},
                "Unsupported account fields.")
        require(any(field in data for field in ("username", "email", "password")),
                "Nothing to change.")
        self.verify_password(account["id"], data.get("current_password"))
        updates, changed = {}, []
        if "username" in data:
            username = username_field(data["username"])
            if username != account["username"]:
                require(username not in RESERVED_USERNAMES, "That username is reserved.")
                updates["username"] = username
                changed.append("username")
        if "email" in data:
            email = email_field(data["email"])
            updates["email"] = email
            changed.append("email")
        rotate = "password" in data
        if rotate:
            password = password_field(data["password"], "New password")
            require(password != data.get("current_password"), "Choose a password you are not already using.")
            salt = secrets.token_hex(16)
            updates["salt"] = salt
            updates["password"] = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                                      bytes.fromhex(salt), PASSWORD_ROUNDS).hex()
            changed.append("password")
        require(updates, "Nothing to change.")
        # The column names are literals set above, never member input. A
        # `require` rather than an `assert`, which python -O compiles away.
        require(set(updates) <= {"username", "email", "salt", "password"},
                "Unsupported account fields.")
        assignments = ",".join(f"{column}=?" for column in updates)
        try:
            with self.connection() as db:
                db.execute(f"UPDATE accounts SET {assignments} WHERE id=?",
                           (*updates.values(), account["id"]))
                if rotate:
                    # A new password ends every other session for this account,
                    # including one an attacker may already hold.
                    db.execute("DELETE FROM sessions WHERE account_id=? AND token_hash<>?",
                               (account["id"], hashlib.sha256(token.encode()).hexdigest()))
                row = db.execute("SELECT * FROM accounts WHERE id=?", (account["id"],)).fetchone()
        except sqlite3.IntegrityError as exc:
            raise MarketError("That username is already in use.", 409) from exc
        self.clear_rate_limit(budget)
        return {"account": self.private_account(row), "changed": changed}

    def seed_catalog(self):
        catalog = ROOT / "marketplace_catalog"
        if not catalog.exists():
            return
        reviews_file = catalog / "reviews.json"
        attestations = json_object(reviews_file.read_text(encoding="utf-8")) if reviews_file.exists() else {}
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO accounts (id,username,name,avatar,password,salt,created,email,wallets)"
                       " VALUES ('data-library','data-library','DATA Library','','','',0,'','[]')")
            for folder in sorted(catalog.iterdir()):
                if not folder.is_dir() or not (folder / "manifest.json").exists():
                    continue
                files = {p.relative_to(folder).as_posix(): p.read_text(encoding="utf-8") for p in folder.rglob("*") if p.is_file()}
                raw = make_archive(files)
                package = parse_package(raw)
                m = package["manifest"]
                lid = "library-" + m["id"]
                now = int(time.time())
                db.execute("INSERT OR IGNORE INTO listings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           (lid, "data-library", m["name"], m["description"], "", 0, m["kind"], m["id"], m["version"], now, now, 1))
                db.execute("INSERT OR IGNORE INTO packages VALUES (?,?,?,?,?,?,NULL)",
                           (lid, m["version"], package["sha256"], raw, json.dumps(m), json.dumps(package["scan"])))
                current = db.execute("SELECT latest FROM listings WHERE id=?", (lid,)).fetchone()[0]
                if tuple(map(int, m["version"].split("."))) > tuple(map(int, current.split("."))):
                    db.execute("UPDATE listings SET latest=?,title=?,description=?,updated=? WHERE id=?", (m["version"], m["name"], m["description"], now, lid))
                review = attestations.get(package["sha256"])
                if review and review.get("sha256") == package["sha256"] and review.get("version") == m["version"] and review.get("package_id") == m["id"]:
                    db.execute("UPDATE packages SET review=? WHERE listing_id=? AND version=? AND sha256=? AND review IS NULL",
                               (json.dumps(review), lid, m["version"], package["sha256"]))

    @staticmethod
    def package_review(row):
        """A badge belongs to the exact reviewed bytes and current policy."""
        if not row["review"]:
            return None
        try:
            review = json.loads(row["review"])
            manifest = json.loads(row["manifest"])
            if (review.get("sha256") == row["sha256"] and review.get("version") == manifest["version"]
                    and review.get("package_id") == manifest["id"]
                    and (review.get("policy") == POLICY_VERSION or review.get("verdict") == "rejected")
                    and review.get("verdict") in ("approved", "rejected")
                    and review.get("risk") in ("low", "moderate", "high")):
                return review
        except (ValueError, TypeError, AttributeError, KeyError):
            pass
        return None

    def _listing(self, db, lid, account=None):
        row = db.execute("SELECT l.*, a.name creator_name, a.username creator_username, a.avatar creator_avatar, p.sha256, p.manifest, p.scan, p.review FROM listings l JOIN accounts a ON a.id=l.owner_id JOIN packages p ON p.listing_id=l.id AND p.version=l.latest WHERE l.id=?", (lid,)).fetchone()
        require(row, "Listing not found.", 404)
        item = dict(row)
        item["image"] = "/marketplace/media/listing/" + lid if row["image"] else ""
        item["creator_avatar"] = "/marketplace/media/account/" + row["owner_id"] if row["creator_avatar"] else ""
        item["manifest"] = json.loads(item["manifest"])
        item["scan"] = json.loads(item["scan"])
        item.pop("review")
        item["security_review"] = self.package_review(row)
        item["review_status"] = ("blocked" if item["security_review"]["verdict"] == "rejected" else "reviewed") if item["security_review"] else "unverified"
        install = db.execute("SELECT version,sha256,enabled,installed FROM installations WHERE listing_id=? AND installed_by=?", (lid, account["id"] if account else "")).fetchone()
        item["installation"] = dict(install) if install else None
        item["update_available"] = bool(install and install["version"] != item["latest"])
        rating = db.execute("SELECT count(*) count, avg(rating) average FROM reviews WHERE listing_id=?", (lid,)).fetchone()
        item["review_count"] = rating["count"]
        item["rating"] = round(rating["average"], 1) if rating["average"] else None
        item["install_count"] = db.execute("SELECT count(*) FROM receipts WHERE listing_id=?", (lid,)).fetchone()[0]
        return item

    def catalog(self, account=None):
        with self.connection() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM listings ORDER BY first_party DESC,updated DESC,id")]
            return [self._listing(db, lid, account) for lid in ids]

    def detail(self, lid, account):
        with self.connection() as db:
            item = self._listing(db, lid, account)
            item["reviews"] = [dict(r, avatar=("/marketplace/media/account/" + r["account_id"] if r["avatar"] else "")) for r in db.execute("SELECT r.rating,r.body,r.version,r.updated,a.id account_id,a.name,a.username,a.avatar FROM reviews r JOIN accounts a ON a.id=r.account_id WHERE listing_id=? ORDER BY r.updated DESC LIMIT 100", (lid,))]
            receipt = db.execute("SELECT version FROM receipts WHERE listing_id=? AND account_id=?", (lid, account["id"] if account else "")).fetchone()
            item["can_review"] = bool(receipt and account["id"] != item["owner_id"])
            item["versions"] = []
            for version in db.execute("SELECT p.*, EXISTS(SELECT 1 FROM audit a WHERE a.listing_id=p.listing_id AND a.version=p.version AND a.event='install' AND a.actor=?) was_installed FROM packages p WHERE p.listing_id=?", (account["id"] if account else "", lid)):
                review = self.package_review(version)
                item["versions"].append({"version": version["version"], "sha256": version["sha256"],
                                         "was_installed": bool(version["was_installed"]), "security_review": review,
                                         "permissions": json.loads(version["manifest"])["permissions"]})
            item["versions"].sort(key=lambda v: tuple(map(int, v["version"].split("."))), reverse=True)
            package = db.execute("SELECT archive FROM packages WHERE listing_id=? AND version=?", (lid, item["latest"])).fetchone()
            # Paid content is not handed out before checkout exists.
            item["files"] = parse_package(bytes(package[0]))["files"] if item["price_cents"] == 0 or (account and account["id"] == item["owner_id"]) else {}
            return item

    def submit(self, data, account):
        require(account, "Create an account or sign in to submit a listing.", 401)
        require(data.get("rights_confirmed") is True, "Confirm that you have permission to distribute this package.")
        raw = decode_base64(data.get("package"), MAX_ARCHIVE)
        package = parse_package(raw)
        m = package["manifest"]
        title = text_field(data.get("title", m["name"]), "Listing title", 80)
        description = text_field(data.get("description", m["description"]), "Listing description", 4000)
        price = data.get("price_cents", 0)
        require(type(price) is int and 0 <= price <= 99_999_999, "Price must be a nonnegative USD amount under $1,000,000.")
        picture = image_data(data["image"]) if "image" in data else None
        now = int(time.time())
        lid = data.get("listing_id") or secrets.token_hex(16)
        require(isinstance(lid, str) and len(lid) <= 100, "Invalid listing id.")
        with self.connection() as db:
            current = db.execute("SELECT * FROM listings WHERE id=?", (lid,)).fetchone()
            if data.get("listing_id"):
                require(current, "Listing not found.", 404)
            if current:
                require(current["owner_id"] == account["id"] and not current["first_party"], "Only the creator can update this listing.", 403)
                require(m["id"] == current["package_id"] and m["kind"] == current["kind"], "Keep the package id and type when updating.")
                require(tuple(map(int, m["version"].split("."))) > tuple(map(int, current["latest"].split("."))), "Upload a newer package version.", 409)
                if picture is None:
                    picture = current["image"]
                db.execute("UPDATE listings SET title=?,description=?,image=?,price_cents=?,latest=?,updated=? WHERE id=?",
                           (title, description, picture, price, m["version"], now, lid))
            else:
                require(db.execute("SELECT count(*) FROM listings WHERE owner_id=?", (account["id"],)).fetchone()[0] < 50, "Local limit: 50 listings per creator.", 409)
                require(db.execute("SELECT count(*) FROM listings").fetchone()[0] < 500, "Local catalogue limit reached.", 409)
                db.execute("INSERT INTO listings VALUES (?,?,?,?,?,?,?,?,?,?,?,0)",
                           (lid, account["id"], title, description, picture or "", price, m["kind"], m["id"], m["version"], now, now))
            require(db.execute("SELECT count(*) FROM packages WHERE listing_id=?", (lid,)).fetchone()[0] < 100, "Local version limit reached.", 409)
            db.execute("INSERT INTO packages VALUES (?,?,?,?,?,?,NULL)",
                       (lid, m["version"], package["sha256"], raw, json.dumps(m), json.dumps(package["scan"])))
            self._audit(db, "submit", account["id"], lid, m["version"], package["sha256"])
        return self.detail(lid, account)

    @staticmethod
    def _audit(db, event, actor, lid, version, digest):
        db.execute("INSERT INTO audit(event,actor,listing_id,version,sha256,created) VALUES (?,?,?,?,?,?)",
                   (event, actor, lid, version, digest, int(time.time())))

    def install(self, lid, data, account):
        require(account, "Sign in to install extensions.", 401)
        with self.connection() as db:
            item = self._listing(db, lid, account)
            require(item["price_cents"] == 0, "Paid checkout is not enabled. No payment was taken.", 402)
            require(item["review_status"] != "blocked", "This version was rejected by security review.", 403)
            require(data.get("sha256") == item["sha256"] and data.get("version") == item["latest"],
                    "Package changed. Open its details and review the current version.", 409)
            require(data.get("permissions") == item["manifest"]["permissions"], "Review the declared permissions first.", 409)
            require(data.get("acknowledge") is True, "Acknowledge the install notice before continuing.")
            row = db.execute("SELECT archive FROM packages WHERE listing_id=? AND version=?", (lid, item["latest"])).fetchone()
            package = parse_package(bytes(row[0]))
            require(package["sha256"] == item["sha256"], "Package integrity check failed.", 409)
            # The transaction atomically activates immutable validated data. No files
            # are extracted, so uninstall cannot delete a user's unrelated work.
            now = int(time.time())
            db.execute("INSERT INTO installations VALUES (?,?,?,?,?,?) ON CONFLICT(listing_id,installed_by) DO UPDATE SET version=excluded.version,sha256=excluded.sha256,enabled=1,installed=excluded.installed",
                       (lid, item["latest"], item["sha256"], 1, account["id"], now))
            db.execute("INSERT INTO receipts VALUES (?,?,?,?) ON CONFLICT(listing_id,account_id) DO UPDATE SET version=excluded.version,installed=excluded.installed",
                       (lid, account["id"], item["latest"], now))
            self._audit(db, "install", account["id"], lid, item["latest"], item["sha256"])
            return self._listing(db, lid, account)

    def manage(self, lid, action, data, account):
        require(account, "Sign in to manage installed extensions.", 401)
        require(action in ("enable", "disable", "uninstall", "rollback"), "Unknown extension action.", 404)
        with self.connection() as db:
            row = db.execute("SELECT * FROM installations WHERE listing_id=? AND installed_by=?", (lid, account["id"])).fetchone()
            require(row, "Extension is not installed.", 404)
            if action == "uninstall":
                db.execute("DELETE FROM installations WHERE listing_id=? AND installed_by=?", (lid, account["id"]))
            elif action == "rollback":
                version = data.get("version")
                prior = db.execute("SELECT p.* FROM packages p WHERE p.listing_id=? AND p.version=? AND EXISTS (SELECT 1 FROM audit a WHERE a.listing_id=p.listing_id AND a.version=p.version AND a.event='install' AND a.actor=?)", (lid, version, account["id"])).fetchone()
                require(prior, "Only a previously installed version can be restored.", 409)
                require(data.get("acknowledge") is True and data.get("sha256") == prior["sha256"], "Review the version and acknowledge its safety status before restoring.", 409)
                package = parse_package(bytes(prior["archive"]))
                require(package["sha256"] == prior["sha256"], "Package integrity check failed.", 409)
                review = self.package_review(prior)
                require(not review or review["verdict"] != "rejected", "This version was rejected by security review.", 403)
                db.execute("UPDATE installations SET version=?,sha256=?,enabled=1 WHERE listing_id=? AND installed_by=?", (version, prior["sha256"], lid, account["id"]))
                db.execute("UPDATE receipts SET version=? WHERE listing_id=? AND account_id=?", (version, lid, account["id"]))
            else:
                if action == "enable":
                    stored = db.execute("SELECT * FROM packages WHERE listing_id=? AND version=?", (lid, row["version"])).fetchone()
                    review = self.package_review(stored)
                    require(not review or review["verdict"] != "rejected", "This version was rejected by security review.", 403)
                db.execute("UPDATE installations SET enabled=? WHERE listing_id=? AND installed_by=?", (int(action == "enable"), lid, account["id"]))
            self._audit(db, action, account["id"], lid, prior["version"] if action == "rollback" else row["version"], prior["sha256"] if action == "rollback" else row["sha256"])
        return {"ok": True}

    def leave_review(self, lid, data, account):
        require(account, "Sign in to leave a review.", 401)
        rating = data.get("rating")
        require(type(rating) is int and 1 <= rating <= 5, "Choose a rating from 1 to 5.")
        body = text_field(data.get("body"), "Review", 2000, 3)
        with self.connection() as db:
            item = self._listing(db, lid)
            require(item["owner_id"] != account["id"], "Creators cannot review their own listings.", 403)
            receipt = db.execute("SELECT version FROM receipts WHERE listing_id=? AND account_id=?", (lid, account["id"])).fetchone()
            require(receipt, "Install this extension before reviewing it.", 403)
            db.execute("INSERT INTO reviews VALUES (?,?,?,?,?,?) ON CONFLICT(listing_id,account_id) DO UPDATE SET rating=excluded.rating,body=excluded.body,version=excluded.version,updated=excluded.updated",
                       (lid, account["id"], rating, body, receipt["version"], int(time.time())))
        return {"ok": True}

    def runtime(self, account=None):
        result = []
        if not account:
            return result
        with self.connection() as db:
            for row in db.execute("SELECT i.*,l.title,p.archive,p.manifest,p.review FROM installations i JOIN listings l ON l.id=i.listing_id JOIN packages p ON p.listing_id=i.listing_id AND p.version=i.version WHERE i.enabled=1 AND i.installed_by=? ORDER BY i.installed,i.listing_id", (account["id"],)):
                try:
                    review = self.package_review(row)
                    require(not review or review["verdict"] != "rejected", "Package review rejected.")
                    package = parse_package(bytes(row["archive"]))
                    require(package["sha256"] == row["sha256"], "Installed package integrity failure.")
                except MarketError:
                    continue  # fail closed if the local database was damaged
                result.append({"id": row["listing_id"], "title": row["title"], "version": row["version"],
                               "sha256": row["sha256"], "components": package["runtime"]})
        return result

    def review_package(self, lid, version, digest, reviewer, findings, risk="moderate", verdict="approved"):
        """Operator-only CLI API. No HTTP route grants or changes review badges."""
        reviewer = text_field(reviewer, "Reviewer", 100)
        findings = text_field(findings, "Review findings", 4000)
        require(risk in ("low", "moderate", "high"), "Choose a low, moderate or high risk profile.")
        require(verdict in ("approved", "rejected"), "Choose an approved or rejected verdict.")
        with self.connection() as db:
            row = db.execute("SELECT * FROM packages WHERE listing_id=? AND version=?", (lid, version)).fetchone()
            require(row, "Package not found.", 404)
            parsed = parse_package(bytes(row["archive"]))
            require(digest == row["sha256"] == parsed["sha256"], "Review digest must match the exact package.", 409)
            review = {"reviewer": reviewer, "method": "Source inspection", "reviewed_at": int(time.time()),
                      "sha256": digest, "version": version, "package_id": parsed["manifest"]["id"],
                      "policy": POLICY_VERSION, "risk": risk, "verdict": verdict, "findings": findings}
            db.execute("UPDATE packages SET review=? WHERE listing_id=? AND version=?", (json.dumps(review), lid, version))
            if verdict == "rejected":
                db.execute("UPDATE installations SET enabled=0 WHERE listing_id=? AND version=?", (lid, version))
            self._audit(db, "security-review", reviewer, lid, version, digest)
            return review

    def review_queue(self):
        with self.connection() as db:
            rows = db.execute("SELECT p.listing_id,l.title,p.version,p.sha256,p.scan,p.manifest,p.review FROM packages p JOIN listings l ON l.id=p.listing_id ORDER BY l.created,p.version")
            return [{key: row[key] for key in ("listing_id", "title", "version", "sha256", "scan")}
                    for row in rows if not self.package_review(row)]

    def media(self, kind, identifier):
        require(kind in ("account", "listing"), "Image not found.", 404)
        with self.connection() as db:
            if kind == "account":
                row = db.execute("SELECT avatar FROM accounts WHERE id=?", (identifier,)).fetchone()
            else:
                row = db.execute("SELECT image FROM listings WHERE id=?", (identifier,)).fetchone()
        require(row and row[0], "Image not found.", 404)
        value = image_data(row[0])
        header, encoded = value.split(",", 1)
        return base64.b64decode(encoded), header[5:].split(";")[0]


_store = None
_store_lock = threading.Lock()


def get_store():
    global _store
    with _store_lock:
        if _store is None:
            _store = Marketplace()
        return _store


def handle_http(handler, store=None):
    """Called before the bridge's wildcard CORS and unbounded body reader."""
    path = urlsplit(handler.path).path
    if not path.startswith("/marketplace/"):
        return False
    cookie = None
    forum_cookie = None
    content_type = "application/json; charset=utf-8"
    try:
        port = handler.server.server_address[1]
        cookie_name = f"data_market_session_{port}"
        hosts = {f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}"}
        host = handler.headers.get("Host", "").lower()
        origin = handler.headers.get("Origin", "")
        require(handler.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1") and
                host in hosts and not any(handler.headers.get(h) for h in PROXY_HEADERS),
                "The local marketplace is available on this computer only.", 403)
        require((not origin or origin == f"http://{host}") and
                handler.headers.get("Sec-Fetch-Site", "") not in ("cross-site", "same-site"),
                "Open the marketplace from this dashboard.", 403)
        if hasattr(handler, "_check_auth") and not handler._check_auth():
            return True
        require(handler.command in ("GET", "POST"), "Method not supported.", 405)
        data = {}
        if handler.command == "POST":
            require(handler.headers.get("X-Marketplace-Request") == "1", "Missing marketplace request header.", 403)
            require(handler.headers.get_content_type() == "application/json", "Use application/json.", 415)
            require(not handler.headers.get("Transfer-Encoding"), "Chunked uploads are not supported.", 400)
            lengths = handler.headers.get_all("Content-Length", [])
            length_text = lengths[0] if len(lengths) == 1 else ""
            require(length_text.isascii() and length_text.isdigit(), "A single Content-Length is required.", 411)
            length = int(length_text)
            require(0 < length <= MAX_BODY, "Upload is too large.", 413)
            handler.connection.settimeout(10)
            body = handler.rfile.read(length)
            require(len(body) == length, "Incomplete upload.")
            data = json_object(body)
        cookies = SimpleCookie()
        try:
            cookies.load(handler.headers.get("Cookie", ""))
            token = cookies[cookie_name].value if cookie_name in cookies else ""
        except Exception:
            token = ""
        store = store or get_store()
        account = store.authenticate(token)
        parts = path.strip("/").split("/")[1:]
        status = 200
        from marketplace_remote import handle_request
        shared = handle_request(handler, store, parts, data)
        if shared is not None:
            result, status, forum_cookie, content_type = shared
        elif parts and parts[0] == "forum":
            from community_transport import cookie_header, proxy_request
            from forum import get_forum
            remote = proxy_request(handler, parts[1:], data)
            if remote is not None:
                result, status, remote_cookie = remote
                if remote_cookie is not None:
                    forum_cookie = cookie_header(port, remote_cookie)
            else:
                result = get_forum(store).route(parts[1:], handler.command, data, account, parse_qs(urlsplit(handler.path).query, keep_blank_values=True, max_num_fields=10))
        elif handler.command == "GET":
            if parts == ["catalog"]:
                result = {"listings": store.catalog(account), "disclaimer": DISCLAIMER, "mode": "local", "checkout_enabled": False}
            elif parts == ["session"]:
                result = {"account": account, "mode": "local", "identity_scope": "local"}
            elif parts == ["runtime"]:
                result = {"extensions": store.runtime(account)}
            elif len(parts) == 2 and parts[0] == "listings":
                result = store.detail(parts[1], account)
            elif len(parts) == 2 and parts[0] == "templates":
                raw = starter_package(parts[1])
                result = {"package": base64.b64encode(raw).decode(), "filename": "data-" + parts[1] + "-starter.zip"}
            elif len(parts) == 3 and parts[0] == "media":
                result, content_type = store.media(parts[1], parts[2])
            else:
                raise MarketError("Unknown marketplace endpoint.", 404)
        elif len(parts) == 2 and parts[0] == "account":
            result, cookie = store.account_action(parts[1], data, account, token, handler.client_address[0])
        elif parts == ["listings"]:
            result = store.submit(data, account)
        elif len(parts) == 3 and parts[0] == "listings":
            lid, action = parts[1:]
            if action == "install":
                result = store.install(lid, data, account)
            elif action == "review":
                result = store.leave_review(lid, data, account)
            else:
                result = store.manage(lid, action, data, account)
        else:
            raise MarketError("Unknown marketplace endpoint.", 404)
    except MarketError as exc:
        result, status = {"error": str(exc)}, exc.status
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        result, status = {"error": "Invalid marketplace request."}, 400
    except (sqlite3.Error, OSError):
        result, status = {"error": "Marketplace storage is unavailable. Please retry."}, 503
    payload = result if isinstance(result, bytes) else json.dumps(result).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Cross-Origin-Resource-Policy", "same-origin")
    handler.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    handler.send_header("Connection", "close")
    handler.close_connection = True
    if cookie is not None:
        handler.send_header("Set-Cookie", f"{cookie_name}={cookie}; HttpOnly; SameSite=Strict; Path=/marketplace; Max-Age={SESSION_SECONDS if cookie else 0}")
    if forum_cookie is not None:
        handler.send_header("Set-Cookie", forum_cookie)
    handler.end_headers()
    handler.wfile.write(payload)
    return True
