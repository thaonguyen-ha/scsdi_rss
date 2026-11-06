#!/usr/bin/env python3
"""One-time migration: load archive/articles.jsonl into Neon Postgres.

Run this ONCE, after you have created the Neon database and set DATABASE_URL
(locally: a .env file or an exported env var; the pooled '-pooler' host):

    pip install -r requirements.txt
    python migrate_jsonl_to_db.py            # loads archive/articles.jsonl
    python migrate_jsonl_to_db.py --dry-run  # just report what would load

It creates the schema if needed, then replaces the table contents with every
record in the JSONL (insertion order preserved, duplicate keys kept). Safe to
re-run: it's a full replace, not an append, so running it twice is idempotent.

After this, Postgres is the source of truth; build_feed.py, archive_text.py and
resolve_links.py read/write the DB. The JSONL file is kept only as a historical
snapshot -- it is no longer updated.
"""

import argparse
import json
from pathlib import Path

import db

HERE = Path(__file__).resolve().parent
ARCHIVE_FILE = HERE / "archive" / "articles.jsonl"


def read_jsonl(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as e:
            print(f"  skipping unparseable line: {e}")
    return records


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", default=str(ARCHIVE_FILE),
                    help="source JSONL (default: archive/articles.jsonl)")
    ap.add_argument("--dry-run", action="store_true",
                    help="parse and report, but don't touch the database")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"No such file: {path}")

    records = read_jsonl(path)
    keys = {r.get("key") for r in records}
    print(f"Parsed {len(records)} records ({len(keys)} unique keys) from {path.name}")

    if args.dry_run:
        print("Dry run -- nothing written.")
        return

    db.init_schema()
    n = db.replace_all(records)
    print(f"Loaded {n} records into Postgres. Row count now: {db.count()}")


if __name__ == "__main__":
    main()
