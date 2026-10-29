"""Trusted local review queue. This is deliberately not exposed over HTTP."""
import argparse
import json
from pathlib import Path

from marketplace import DEFAULT_DATA, Marketplace, MarketError, parse_package


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    commands = parser.add_subparsers(dest="command", required=True)
    queue = commands.add_parser("queue", help="List unreviewed package versions")
    inspect = commands.add_parser("inspect", help="Print package source without executing it")
    inspect.add_argument("listing")
    inspect.add_argument("--version", required=True)
    review = commands.add_parser("review", help="Record an independent source review for an exact digest")
    review.add_argument("listing")
    review.add_argument("--version", required=True)
    review.add_argument("--sha256", required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--risk", choices=("low", "moderate", "high"), required=True)
    review.add_argument("--verdict", choices=("approved", "rejected"), required=True)
    review.add_argument("--findings-file", type=Path, required=True)
    args = parser.parse_args()
    store = Marketplace(args.data_dir, seed=False)
    try:
        if args.command == "queue":
            print(json.dumps(store.review_queue(), indent=2))
        elif args.command == "inspect":
            with store.connection() as db:
                row = db.execute("SELECT archive FROM packages WHERE listing_id=? AND version=?", (args.listing, args.version)).fetchone()
            if not row:
                raise MarketError("Package not found.")
            package = parse_package(bytes(row[0]))
            print(json.dumps(package, indent=2))
        else:
            review = store.review_package(args.listing, args.version, args.sha256, args.reviewer,
                                          args.findings_file.read_text(encoding="utf-8"), args.risk, args.verdict)
            print(json.dumps(review, indent=2))
    except MarketError as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
