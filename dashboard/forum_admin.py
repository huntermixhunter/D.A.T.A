"""Local operator moderation. No moderator powers are exposed to forum clients."""
import argparse
import json
from pathlib import Path

from marketplace import DEFAULT_DATA, Marketplace
from forum import get_forum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("reports")
    action = commands.add_parser("moderate")
    action.add_argument("action", choices=("lock", "unlock", "hide-thread", "restore-thread", "hide-post", "restore-post", "suspend", "unsuspend", "resolve-report"))
    action.add_argument("target")
    action.add_argument("--reason", required=True)
    args = parser.parse_args()
    store = Marketplace(args.data_dir, seed=False)
    forum = get_forum(store)
    if args.command == "reports":
        with store.connection() as db:
            result = [dict(row) for row in db.execute("""SELECT r.*,p.thread_id,p.author_id,p.body,t.title
                FROM forum_reports r JOIN forum_posts p ON p.id=r.post_id
                JOIN forum_threads t ON t.id=p.thread_id WHERE r.resolved=0 ORDER BY r.created LIMIT 200""")]
    else:
        result = forum.moderate(args.action, args.target, args.reason)
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
