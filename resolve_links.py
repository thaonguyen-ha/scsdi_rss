#!/usr/bin/env python3
"""Resolve Google News redirect links in the archive to direct publisher URLs.

A SEPARATE, RETRYABLE step -- deliberately NOT part of ingestion (Google can
change its scheme, so resolution is best-effort and resumable). Reads
archive/articles.jsonl, decodes each record's Google News `link` to the real
publisher URL via `googlenewsdecoder`, and writes `resolved_url` (plus resolve
bookkeeping) back into the archive in place. Safe to re-run: it only attempts
records that have no `resolved_url` yet and are under the retry cap, and it saves
progress periodically so an interrupted run resumes where it stopped.

Setup:
    pip install googlenewsdecoder "selectolax<1.0"
    # (0.1.7 imports selectolax's Modest backend, removed in selectolax 1.0)

Usage:
    python resolve_links.py --limit 20          # resolve 20 unresolved (test)
    python resolve_links.py                       # resolve all unresolved
    python resolve_links.py --relevant-only       # relevant records first
    python resolve_links.py --interval 2 --max-retries 3
"""
import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import db

try:
    from googlenewsdecoder import gnewsdecoder as _decode
except ImportError:
    _decode = None

HERE = Path(__file__).resolve().parent


def load():
    return db.load_ordered()


def save(rows):
    """Atomic full replace of the archive in Postgres (a single transaction, so
    an interrupted write never leaves the archive half-updated)."""
    db.replace_all(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0 = all unresolved")
    ap.add_argument("--interval", type=float, default=1.5,
                    help="seconds between requests (sequential mode only)")
    ap.add_argument("--workers", type=int, default=1,
                    help="parallel resolver threads (I/O-bound; 6 is fast and "
                         "stayed at 0 failures in testing)")
    ap.add_argument("--max-retries", type=int, default=3,
                    help="give up on a link after this many failed attempts")
    ap.add_argument("--relevant-only", action="store_true")
    ap.add_argument("--save-every", type=int, default=25)
    args = ap.parse_args()
    if _decode is None:
        raise SystemExit('googlenewsdecoder not installed. Run: '
                         'pip install googlenewsdecoder "selectolax<1.0"')

    rows = load()

    def needs(r):
        if r.get("resolved_url"):
            return False
        if r.get("resolve_attempts", 0) >= args.max_retries:
            return False
        if args.relevant_only and not r.get("relevant"):
            return False
        return bool(r.get("link"))

    todo = [r for r in rows if needs(r)]
    if args.limit:
        todo = todo[:args.limit]
    already = sum(1 for r in rows if r.get("resolved_url"))
    print(f"{len(todo)} to resolve (of {len(rows)} total; {already} already resolved)...",
          flush=True)

    lock = threading.Lock()
    prog = {"done": 0, "ok": 0, "fail": 0}

    def attempt(r):
        url = err = None
        try:
            res = _decode(r["link"], interval=1)
            url = res.get("decoded_url") if isinstance(res, dict) else None
            if not url:
                err = res.get("message") if isinstance(res, dict) else "no decoded_url"
        except Exception as e:  # noqa: BLE001
            err = str(e)[:200]
        with lock:
            r["resolve_attempts"] = r.get("resolve_attempts", 0) + 1
            if url:
                r["resolved_url"] = url
                r.pop("resolve_error", None)
                prog["ok"] += 1
            else:
                r["resolve_error"] = str(err)[:200]
                prog["fail"] += 1
            prog["done"] += 1
            if prog["done"] % args.save_every == 0:
                save(rows)   # saved under lock so no record is mid-mutation
                print(f"  [{prog['done']}/{len(todo)}] ok={prog['ok']} "
                      f"fail={prog['fail']} (saved)", flush=True)
        if args.workers == 1:
            time.sleep(args.interval)   # pace only in sequential mode

    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(attempt, todo))
    else:
        for r in todo:
            attempt(r)

    save(rows)
    total_res = sum(1 for r in rows if r.get("resolved_url"))
    print(f"done: ok={prog['ok']} fail={prog['fail']}  |  "
          f"resolved_url now on {total_res}/{len(rows)}")


if __name__ == "__main__":
    main()
