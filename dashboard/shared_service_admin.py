"""Read service storage status or make a consistent SQLite backup."""
import argparse
import json
from pathlib import Path
import sqlite3


def backup(database, target):
    database, target = Path(database).resolve(), Path(target).resolve()
    if not database.is_file():
        raise ValueError("The service database does not exist.")
    if database == target or target.exists():
        raise ValueError("Choose a new backup path; existing files are never overwritten.")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation refuses a competing writer before opening SQLite.
    with target.open("xb"):
        pass
    source = destination = None
    success = False
    try:
        source = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        destination = sqlite3.connect(target)
        source.backup(destination)
        if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Backup integrity check failed.")
        success = True
    finally:
        if destination:
            destination.close()
        if source:
            source.close()
        if not success:
            target.unlink(missing_ok=True)
    return {"backup": str(target), "bytes": target.stat().st_size, "integrity": "ok"}


def status(database):
    database = Path(database).resolve()
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    try:
        result = {"bytes": database.stat().st_size,
                  "integrity": connection.execute("PRAGMA quick_check").fetchone()[0]}
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("accounts", "listings", "packages", "forum_threads", "forum_posts"):
            result[table] = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] if table in names else 0
        if "forum_reports" in names:
            result["open_reports"] = connection.execute("SELECT count(*) FROM forum_reports WHERE resolved=0").fetchone()[0]
        return result
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    copy = commands.add_parser("backup")
    copy.add_argument("target", type=Path)
    args = parser.parse_args()
    database = args.data_dir / "marketplace.sqlite3"
    try:
        result = status(database) if args.command == "status" else backup(database, args.target)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, str(exc) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
