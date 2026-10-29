"""Account-backed discussions. Posts are plain text, never executable content."""
from __future__ import annotations

import re
import secrets
import time

from marketplace import MarketError, require, text_field


CATEGORIES = (
    ("general", "Dashboard General", "Conversations about using DATA, workflows, and everyday discoveries."),
    ("help", "Help & Troubleshooting", "Ask a question, compare notes, and help someone get unstuck."),
    ("ideas", "Ideas & Requests", "Suggest improvements and discuss what DATA could do next."),
    ("showcase", "Show & Tell", "Share something you made, a useful setup, or a small win."),
    ("extensions", "Extensions", "Talk about themes, skills, tools, and marketplace creations."),
    ("off-topic", "Off Topic", "Everything else. A place to get to know the people behind the dashboards."),
)
CATEGORY_IDS = {category[0] for category in CATEGORIES}
PAGE_SIZE = 25
POST_PAGE_SIZE = 30


def now():
    return time.time_ns() // 1_000_000


def page_number(query):
    value = query.get("page", ["1"])[0]
    require(isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,5}", value), "Invalid page.")
    return int(value)


class Forum:
    def __init__(self, store):
        self.store = store
        with store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS forum_threads (
                    id TEXT PRIMARY KEY, author_id TEXT NOT NULL REFERENCES accounts(id),
                    category TEXT NOT NULL, title TEXT NOT NULL, created INTEGER NOT NULL,
                    updated INTEGER NOT NULL, locked INTEGER NOT NULL DEFAULT 0,
                    hidden INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS forum_posts (
                    id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES forum_threads(id),
                    author_id TEXT NOT NULL REFERENCES accounts(id), body TEXT NOT NULL,
                    created INTEGER NOT NULL, edited INTEGER, deleted INTEGER NOT NULL DEFAULT 0,
                    hidden INTEGER NOT NULL DEFAULT 0, request_id TEXT NOT NULL,
                    is_topic INTEGER NOT NULL DEFAULT 0, UNIQUE(author_id, request_id));
                CREATE TABLE IF NOT EXISTS forum_likes (
                    post_id TEXT NOT NULL REFERENCES forum_posts(id),
                    account_id TEXT NOT NULL REFERENCES accounts(id), PRIMARY KEY(post_id, account_id));
                CREATE TABLE IF NOT EXISTS forum_follows (
                    thread_id TEXT NOT NULL REFERENCES forum_threads(id),
                    account_id TEXT NOT NULL REFERENCES accounts(id), PRIMARY KEY(thread_id, account_id));
                CREATE TABLE IF NOT EXISTS forum_reads (
                    thread_id TEXT NOT NULL REFERENCES forum_threads(id),
                    account_id TEXT NOT NULL REFERENCES accounts(id), seen INTEGER NOT NULL,
                    PRIMARY KEY(thread_id, account_id));
                CREATE TABLE IF NOT EXISTS forum_reports (
                    id TEXT PRIMARY KEY, post_id TEXT NOT NULL REFERENCES forum_posts(id),
                    account_id TEXT NOT NULL REFERENCES accounts(id), reason TEXT NOT NULL,
                    created INTEGER NOT NULL, resolved INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(post_id, account_id));
                CREATE TABLE IF NOT EXISTS forum_restrictions (
                    account_id TEXT PRIMARY KEY REFERENCES accounts(id), reason TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS forum_actions (
                    account_id TEXT NOT NULL REFERENCES accounts(id), kind TEXT NOT NULL, created INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS forum_moderation (
                    id INTEGER PRIMARY KEY, action TEXT NOT NULL, target TEXT NOT NULL,
                    reason TEXT NOT NULL, created INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS forum_topic_activity ON forum_threads(hidden, updated DESC);
                CREATE INDEX IF NOT EXISTS forum_category ON forum_threads(category, hidden, updated DESC);
                CREATE INDEX IF NOT EXISTS forum_post_order ON forum_posts(thread_id, is_topic DESC, created, id);
                CREATE INDEX IF NOT EXISTS forum_action_limits ON forum_actions(account_id, created);
            """)

    @staticmethod
    def author(row):
        # Display names are editable; the unique handle and ID remain visible.
        return {"id": row["author_id"], "name": row["name"], "username": row["username"]}

    @staticmethod
    def signed_in(account):
        require(account, "Sign in to join the conversation.", 401)
        return account["id"]

    def writable(self, db, account, kind):
        uid = self.signed_in(account)
        require(not db.execute("SELECT 1 FROM forum_restrictions WHERE account_id=?", (uid,)).fetchone(),
                "Posting is disabled for this account. Contact the community moderator.", 403)
        stamp = now()
        db.execute("DELETE FROM forum_actions WHERE created<?", (stamp - 3_600_000,))
        require(db.execute("SELECT count(*) FROM forum_actions WHERE account_id=? AND created>?", (uid, stamp - 300_000)).fetchone()[0] < 30,
                "Please slow down. Try again in a few minutes.", 429)
        if kind in ("thread", "report"):
            span = 900_000 if kind == "thread" else 3_600_000
            require(db.execute("SELECT count(*) FROM forum_actions WHERE account_id=? AND kind=? AND created>?", (uid, kind, stamp - span)).fetchone()[0] < 8,
                    "You have reached the posting limit. Please try again later.", 429)
        db.execute("INSERT INTO forum_actions VALUES (?,?,?)", (uid, kind, stamp))
        return uid, stamp

    @staticmethod
    def thread_row(db, tid):
        row = db.execute("SELECT * FROM forum_threads WHERE id=? AND hidden=0", (tid,)).fetchone()
        require(row, "This discussion is unavailable.", 404)
        return row

    def topic(self, db, row, account):
        uid = account["id"] if account else ""
        result = {key: row[key] for key in ("id", "category", "title", "created", "updated")}
        result.update(author=self.author(row), locked=bool(row["locked"]),
                      replies=row["replies"], following=bool(row["following"]),
                      unread=bool(uid and (row["seen"] or 0) < row["updated"]))
        return result

    @staticmethod
    def topic_select():
        return """SELECT t.*,a.name,a.username,
            (SELECT count(*) FROM forum_posts p WHERE p.thread_id=t.id AND p.is_topic=0 AND p.deleted=0 AND p.hidden=0) replies,
            EXISTS(SELECT 1 FROM forum_follows f WHERE f.thread_id=t.id AND f.account_id=?) following,
            (SELECT seen FROM forum_reads r WHERE r.thread_id=t.id AND r.account_id=?) seen
            FROM forum_threads t JOIN accounts a ON a.id=t.author_id """

    def meta(self, account):
        with self.store.connection() as db:
            counts = dict(db.execute("SELECT category,count(*) FROM forum_threads WHERE hidden=0 GROUP BY category"))
            posts = db.execute("SELECT count(*) FROM forum_posts p JOIN forum_threads t ON t.id=p.thread_id WHERE p.hidden=0 AND p.deleted=0 AND t.hidden=0").fetchone()[0]
            restricted = bool(account and db.execute("SELECT 1 FROM forum_restrictions WHERE account_id=?", (account["id"],)).fetchone())
        return {"account": account, "mode": "local", "restricted": restricted,
                "categories": [{"id": cid, "name": name, "description": description, "threads": counts.get(cid, 0)} for cid, name, description in CATEGORIES],
                "threads": sum(counts.values()), "posts": posts}

    def threads(self, query, account):
        category = query.get("category", ["all"])[0]
        view = query.get("view", ["all"])[0]
        sort = query.get("sort", ["active"])[0]
        search = query.get("q", [""])[0].strip()
        page = page_number(query)
        require(category == "all" or category in CATEGORY_IDS, "Choose a valid category.")
        require(view in ("all", "following", "mine"), "Unknown discussion filter.")
        require(sort in ("active", "newest", "unanswered"), "Unknown sort order.")
        require(len(search) <= 120, "Search is limited to 120 characters.")
        uid = account["id"] if account else ""
        if view != "all":
            self.signed_in(account)
        clauses, values = ["t.hidden=0"], []
        if category != "all":
            clauses.append("t.category=?")
            values.append(category)
        if view == "mine":
            clauses.append("t.author_id=?")
            values.append(uid)
        elif view == "following":
            clauses.append("EXISTS(SELECT 1 FROM forum_follows f WHERE f.thread_id=t.id AND f.account_id=?)")
            values.append(uid)
        if search:
            clauses.append("(instr(lower(t.title), lower(?))>0 OR EXISTS(SELECT 1 FROM forum_posts p WHERE p.thread_id=t.id AND p.hidden=0 AND p.deleted=0 AND instr(lower(p.body),lower(?))>0))")
            values.extend([search, search])
        if sort == "unanswered":
            clauses.append("NOT EXISTS(SELECT 1 FROM forum_posts p WHERE p.thread_id=t.id AND p.is_topic=0 AND p.hidden=0 AND p.deleted=0)")
        where = " WHERE " + " AND ".join(clauses)
        order = "t.created DESC,t.id DESC" if sort == "newest" else "t.updated DESC,t.id DESC"
        with self.store.connection() as db:
            total = db.execute("SELECT count(*) FROM forum_threads t" + where, values).fetchone()[0]
            rows = db.execute(self.topic_select() + where + " ORDER BY " + order + " LIMIT ? OFFSET ?",
                              [uid, uid, *values, PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
            items = [self.topic(db, row, account) for row in rows]
        return {"threads": items, "total": total, "page": page, "page_size": PAGE_SIZE}

    def detail(self, tid, query, account):
        uid = account["id"] if account else ""
        page = page_number(query)
        with self.store.connection() as db:
            self.thread_row(db, tid)
            row = db.execute(self.topic_select() + " WHERE t.id=?", (uid, uid, tid)).fetchone()
            total = db.execute("SELECT count(*) FROM forum_posts WHERE thread_id=? AND hidden=0", (tid,)).fetchone()[0]
            rows = db.execute("""SELECT p.*,a.name,a.username,
                (SELECT count(*) FROM forum_likes l WHERE l.post_id=p.id) likes,
                EXISTS(SELECT 1 FROM forum_likes l WHERE l.post_id=p.id AND l.account_id=?) liked
                FROM forum_posts p JOIN accounts a ON a.id=p.author_id
                WHERE p.thread_id=? AND p.hidden=0 ORDER BY p.is_topic DESC,p.created,p.id LIMIT ? OFFSET ?""",
                (uid, tid, POST_PAGE_SIZE, (page - 1) * POST_PAGE_SIZE)).fetchall()
            posts = []
            for p in rows:
                posts.append({"id": p["id"], "body": "" if p["deleted"] else p["body"],
                              "author": self.author(p), "created": p["created"], "edited": p["edited"],
                              "deleted": bool(p["deleted"]), "is_topic": bool(p["is_topic"]),
                              "likes": p["likes"], "liked": bool(p["liked"]), "own": uid == p["author_id"]})
            if uid and page * POST_PAGE_SIZE >= total:
                db.execute("INSERT INTO forum_reads VALUES (?,?,?) ON CONFLICT(thread_id,account_id) DO UPDATE SET seen=excluded.seen", (tid, uid, row["updated"]))
            return {"thread": self.topic(db, row, account), "posts": posts, "total": total,
                    "page": page, "page_size": POST_PAGE_SIZE, "account": account}

    def create(self, data, account, tid=None):
        uid = self.signed_in(account)
        body = text_field(data.get("body"), "Post", 12_000)
        request_id = data.get("request_id", "")
        require(isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{16,80}", request_id), "A valid request ID is required.")
        title = text_field(data.get("title"), "Title", 140, 3) if tid is None else None
        category = data.get("category") if tid is None else None
        if tid is None:
            require(category in CATEGORY_IDS, "Choose a valid category.")
        with self.store.connection() as db:
            existing = db.execute("SELECT * FROM forum_posts WHERE author_id=? AND request_id=?", (uid, request_id)).fetchone()
            if existing:
                topic = self.thread_row(db, existing["thread_id"])
                require(not existing["deleted"] and not existing["hidden"] and existing["body"] == body and
                        ((tid is not None and not existing["is_topic"] and existing["thread_id"] == tid) or
                         (tid is None and existing["is_topic"] and topic["title"] == title and topic["category"] == category)),
                        "This request ID was already used. Start a new post.", 409)
                # Return the post's actual page after an uncertain network response.
                before = db.execute("""SELECT count(*) FROM forum_posts WHERE thread_id=? AND hidden=0
                    AND (is_topic>? OR (is_topic=? AND (created<? OR (created=? AND id<=?))))""",
                    (existing["thread_id"], existing["is_topic"], existing["is_topic"], existing["created"], existing["created"], existing["id"])).fetchone()[0]
                return {"thread_id": existing["thread_id"], "post_id": existing["id"], "duplicate": True,
                        "page": (before - 1) // POST_PAGE_SIZE + 1}
            uid, stamp = self.writable(db, account, "thread" if tid is None else "reply")
            first = tid is None
            if first:
                tid = secrets.token_hex(16)
                db.execute("INSERT INTO forum_threads(id,author_id,category,title,created,updated) VALUES (?,?,?,?,?,?)", (tid, uid, category, title, stamp, stamp))
            else:
                topic = self.thread_row(db, tid)
                require(not topic["locked"], "This discussion is closed to new replies.", 409)
                stamp = max(stamp, topic["updated"] + 1)
            pid = secrets.token_hex(16)
            db.execute("INSERT INTO forum_posts(id,thread_id,author_id,body,created,request_id,is_topic) VALUES (?,?,?,?,?,?,?)", (pid, tid, uid, body, stamp, request_id, int(first)))
            db.execute("UPDATE forum_threads SET updated=? WHERE id=?", (stamp, tid))
            db.execute("INSERT OR IGNORE INTO forum_follows VALUES (?,?)", (tid, uid))
            total = db.execute("SELECT count(*) FROM forum_posts WHERE thread_id=? AND hidden=0", (tid,)).fetchone()[0]
        return {"thread_id": tid, "post_id": pid, "page": (total - 1) // POST_PAGE_SIZE + 1}

    def post_action(self, pid, action, data, account):
        with self.store.connection() as db:
            uid, stamp = self.writable(db, account, "report" if action == "report" else action)
            row = db.execute("SELECT * FROM forum_posts WHERE id=? AND hidden=0 AND deleted=0", (pid,)).fetchone()
            require(row, "This post is unavailable.", 404)
            self.thread_row(db, row["thread_id"])
            if action in ("edit", "delete"):
                require(row["author_id"] == uid, "You can only change your own posts.", 403)
                if action == "edit":
                    body = text_field(data.get("body"), "Post", 12_000)
                    db.execute("UPDATE forum_posts SET body=?,edited=? WHERE id=?", (body, stamp, pid))
                    if row["is_topic"] and "title" in data:
                        title = text_field(data["title"], "Title", 140, 3)
                        db.execute("UPDATE forum_threads SET title=? WHERE id=?", (title, row["thread_id"]))
                else:
                    db.execute("UPDATE forum_posts SET body='',deleted=1,edited=? WHERE id=?", (stamp, pid))
                    db.execute("DELETE FROM forum_likes WHERE post_id=?", (pid,))
                    if row["is_topic"]:
                        db.execute("UPDATE forum_threads SET hidden=1 WHERE id=?", (row["thread_id"],))
            elif action == "like":
                require(type(data.get("liked")) is bool, "Choose whether to like this post.")
                if data["liked"]:
                    db.execute("INSERT OR IGNORE INTO forum_likes VALUES (?,?)", (pid, uid))
                else:
                    db.execute("DELETE FROM forum_likes WHERE post_id=? AND account_id=?", (pid, uid))
            elif action == "report":
                reason = text_field(data.get("reason"), "Reason", 1000, 5)
                require(row["author_id"] != uid, "Use edit or delete for your own post.")
                db.execute("INSERT INTO forum_reports(id,post_id,account_id,reason,created) VALUES (?,?,?,?,?) ON CONFLICT(post_id,account_id) DO UPDATE SET reason=excluded.reason,created=excluded.created,resolved=0", (secrets.token_hex(16), pid, uid, reason, stamp))
            else:
                raise MarketError("Unknown post action.", 404)
        return {"ok": True, "thread_id": row["thread_id"]}

    def follow(self, tid, data, account):
        require(type(data.get("following")) is bool, "Choose whether to follow this discussion.")
        with self.store.connection() as db:
            uid, _ = self.writable(db, account, "follow")
            self.thread_row(db, tid)
            if data["following"]:
                db.execute("INSERT OR IGNORE INTO forum_follows VALUES (?,?)", (tid, uid))
            else:
                db.execute("DELETE FROM forum_follows WHERE thread_id=? AND account_id=?", (tid, uid))
        return {"following": data["following"]}

    def moderate(self, action, target, reason):
        """Operator-only entry point, deliberately absent from HTTP routes."""
        reason = text_field(reason, "Moderation reason", 1000, 5)
        with self.store.connection() as db:
            if action in ("lock", "unlock", "hide-thread", "restore-thread"):
                column = "locked" if action in ("lock", "unlock") else "hidden"
                value = int(action in ("lock", "hide-thread"))
                require(db.execute("SELECT 1 FROM forum_threads WHERE id=?", (target,)).fetchone(), "Thread not found.", 404)
                db.execute(f"UPDATE forum_threads SET {column}=? WHERE id=?", (value, target))
            elif action in ("hide-post", "restore-post"):
                post = db.execute("SELECT * FROM forum_posts WHERE id=?", (target,)).fetchone()
                require(post, "Post not found.", 404)
                require(not post["is_topic"], "Moderate the discussion itself for an opening post.")
                db.execute("UPDATE forum_posts SET hidden=? WHERE id=?", (int(action == "hide-post"), target))
            elif action in ("suspend", "unsuspend"):
                require(db.execute("SELECT 1 FROM accounts WHERE id=?", (target,)).fetchone(), "Account not found.", 404)
                if action == "suspend":
                    db.execute("INSERT INTO forum_restrictions VALUES (?,?) ON CONFLICT(account_id) DO UPDATE SET reason=excluded.reason", (target, reason))
                else:
                    db.execute("DELETE FROM forum_restrictions WHERE account_id=?", (target,))
            elif action == "resolve-report":
                require(db.execute("SELECT 1 FROM forum_reports WHERE id=?", (target,)).fetchone(), "Report not found.", 404)
                db.execute("UPDATE forum_reports SET resolved=1 WHERE id=?", (target,))
            else:
                raise MarketError("Unknown moderation action.")
            db.execute("INSERT INTO forum_moderation(action,target,reason,created) VALUES (?,?,?,?)", (action, target, reason, now()))
        return {"ok": True}

    def route(self, parts, method, data, account, query):
        if method == "GET":
            if parts == ["meta"]:
                return self.meta(account)
            if parts == ["session"]:
                return {"account": account, "mode": "local"}
            if parts == ["threads"]:
                return self.threads(query, account)
            if len(parts) == 2 and parts[0] == "threads":
                return self.detail(parts[1], query, account)
        elif method == "POST":
            if parts == ["threads"]:
                return self.create(data, account)
            if len(parts) == 3 and parts[0] == "threads":
                if parts[2] == "reply":
                    return self.create(data, account, parts[1])
                if parts[2] == "follow":
                    return self.follow(parts[1], data, account)
            if len(parts) == 3 and parts[0] == "posts" and parts[2] in ("edit", "delete", "report", "like"):
                return self.post_action(parts[1], parts[2], data, account)
        raise MarketError("Unknown forum endpoint.", 404)


def get_forum(store):
    with store.lock:
        if not hasattr(store, "forum"):
            store.forum = Forum(store)
        return store.forum
