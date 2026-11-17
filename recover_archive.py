#!/usr/bin/env python3
"""Weekly recovery pass for articles the incremental archiver gave up on.

archive_text.py (every 6 h) tries each relevant article 3 times for body text
and 3 times for a Wayback snapshot. Anything still missing is written to
archive/recovery_queue.json. Once a week this script works through that queue,
choosing a strategy from HOW the article failed, so each failure mode gets the
treatment that can actually fix it:

  failure (text_status)          class      what this pass does
  -----------------------------  ---------  ----------------------------------------------
  resolve_failed / resolve_*     resolve    retry decoding the Google News redirect first
  http_401/402/403/406/451       blocked    skip the publisher (it refuses scripts); use the
                                            Wayback Machine, which fetches from its own servers:
                                            existing capture, else request a fresh one
  http_408/429/5xx, timeout,     transient  one patient publisher retry (60 s timeout), then
  url_error, error_*                        the Wayback route as for `blocked`
  no_text                        no_text    page loaded but extraction was too short (JS /
                                            paywall): re-extract in recall mode, then Wayback
  wayback_no_text                wb_shell   the existing capture is a JS shell: request a
                                            FRESH capture and extract from that
  http_404/410                   gone       page is gone: look for an existing capture only
                                            (a new one would just archive the 404)

Missing Wayback snapshots (needs "wayback") are requested the same way, except
for `gone` pages, where only an existing capture is looked up.

Each queued article gets up to MAX_RECOVERY_PASSES weekly passes. It is then
marked `recovered` (its needs are met) or `given_up` (left in the queue file
with its full history, as an audit trail). Wayback traffic is deliberately slow
(longer pauses than the 6-hourly job), which is the point of batching it weekly.

Usage:
    python recover_archive.py                 # up to --limit queued articles
    python recover_archive.py --limit 10 --delay 15
    python recover_archive.py --dry-run       # show the plan, change nothing
"""
import argparse
import datetime as dt
import json
import sys
import time

import archive_text as at
import db
from build_feed import REPORTS_DIR, load_archive

MAX_RECOVERY_PASSES = 3
at.WAYBACK_PAUSE = 10   # gentler than the 6-hourly job (5 s)

COPY_FIELDS = ("resolved_url", "wayback_url", "text_file", "text_chars", "text_source",
               "text_relevant", "text_scs_hits", "text_actor_hits", "text_archived",
               "archive_status", "publisher_status", "recovery_status",
               "recovery_attempts", "last_recovery_at")


def classify(status):
    s = status or ""
    if s.startswith("resolve"):
        return "resolve"
    if s in ("no_text",):
        return "no_text"
    if s == "wayback_no_text":
        return "wb_shell"
    if s in ("http_404", "http_410"):
        return "gone"
    if s in ("http_401", "http_402", "http_403", "http_406", "http_451"):
        return "blocked"
    return "transient"   # http_408/429/5xx, timeout, url_error, error_*, requeued, unknown


def wayback_for(real, klass, fresh=False):
    """A good snapshot URL for ``real``. ``fresh`` asks for a new capture even
    if one exists (the old one is a JS shell). Gone pages: lookup only."""
    if klass == "gone":
        return at.wayback_lookup(real)
    if not fresh:
        snap = at.wayback_lookup(real)
        if snap:
            return snap
    return at.wayback_save(real)


def text_from_wayback(rec, snap):
    try:
        html = at._wb_req(at.raw_snapshot_url(snap), timeout=90)[1]
    except Exception as e:  # noqa: BLE001
        return "wayback_" + at.status_of(e)
    return at.store_text(rec, html, "wayback", favor_recall=True)


def recover_text(rec, real, klass):
    """Try to get body text by the route that fits the failure class."""
    if klass in ("transient", "no_text"):
        try:
            html = at._req(real, timeout=60)[1]
            status = at.store_text(rec, html, "publisher", favor_recall=True)
            rec.pop("publisher_status", None)
            if status.startswith("ok"):
                return status
        except Exception as e:  # noqa: BLE001
            rec["publisher_status"] = at.status_of(e)
    snap = rec.get("wayback_url") if klass != "wb_shell" else None
    if not snap or not at.snapshot_matches(snap, real):
        snap = wayback_for(real, klass, fresh=(klass == "wb_shell"))
    if not snap:
        return "no_snapshot"
    if not rec.get("wayback_url") or klass == "wb_shell":
        rec["wayback_url"] = snap
    return text_from_wayback(rec, snap)


def recover_one(rec, entry):
    """One weekly pass for one article. Returns (strategy, outcome dict)."""
    klass = classify(rec.get("archive_status") if "text" in entry["needs"] else "")
    outcome = {}
    try:
        real = at.resolve(rec)
    except Exception as e:  # noqa: BLE001
        real, outcome["resolve"] = None, "resolve_" + at.status_of(e)
    if not real:
        outcome.setdefault("resolve", "resolve_failed")
        return klass, outcome
    if at.is_index_url(real):
        rec["relevant"], rec["tier"], rec["archive_status"] = False, "index", "index_page"
        outcome["index_page"] = True
        return klass, outcome

    if "wayback" in entry["needs"] and not rec.get("wayback_url"):
        snap = wayback_for(real, klass)
        if snap:
            rec["wayback_url"] = snap
        outcome["wayback"] = "ok" if snap else "failed"
    if "text" in entry["needs"] and not rec.get("text_archived"):
        status = recover_text(rec, real, klass)
        rec["archive_status"] = status
        outcome["text"] = status
    return klass, outcome


def remaining_needs(rec, needs):
    left = []
    if "text" in needs and not rec.get("text_archived"):
        left.append("text")
    if "wayback" in needs and not rec.get("wayback_url"):
        left.append("wayback")
    return left


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=60, help="max queued articles this pass")
    ap.add_argument("--delay", type=float, default=10.0, help="seconds between articles")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; no requests, no writes")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    queue = at.load_queue()
    by_key, ordered = load_archive()
    # Index pages (tag/topic/author/archive) are not articles: drop them from the
    # relevant set and from the queue rather than retrying them every week.
    dropped = at.drop_index_pages(ordered)
    for e in queue.values():
        rec = by_key.get(e["key"])
        if e["status"] == "queued" and rec is not None and not rec.get("relevant"):
            e["status"] = "index_page" if rec.get("tier") == "index" else "not_relevant"
            rec["recovery_status"] = e["status"]
    if dropped and not args.dry_run:
        print(f"Dropped {dropped} index pages from the relevant set")
    # Articles may have been queued before this script existed (old 5-try rule):
    # sweep the archive so they are picked up too.
    relevant = [r for r in ordered if r.get("relevant")]
    added = at.enqueue_exhausted(relevant, queue)
    todo = sorted((e for e in queue.values() if e["status"] == "queued" and e["key"] in by_key),
                  key=lambda e: (e.get("recovery_attempts", 0), e.get("queued_at") or ""))[: args.limit]
    waiting = sum(1 for e in queue.values() if e["status"] == "queued")
    print(f"Recovery queue: {waiting} queued ({added} newly swept in); working on {len(todo)} this pass")

    if args.dry_run:
        for e in todo:
            rec = by_key[e["key"]]
            klass = classify(rec.get("archive_status") if "text" in e["needs"] else "")
            print(f"  {klass:<9} needs={','.join(e['needs']):<12} pass {e.get('recovery_attempts', 0) + 1}"
                  f"/{MAX_RECOVERY_PASSES}  {rec.get('archive_status')}  {e['title'][:60]}")
        return

    at.TEXT_DIR.mkdir(parents=True, exist_ok=True)
    tally = {}
    for i, e in enumerate(todo, 1):
        rec = by_key[e["key"]]
        before = list(e["needs"])
        try:
            klass, outcome = recover_one(rec, e)
        except Exception as ex:  # noqa: BLE001
            klass, outcome = "error", {"error": at.status_of(ex)}
        now = at.now_iso()
        e["recovery_attempts"] = e.get("recovery_attempts", 0) + 1
        e["last_recovery_at"] = now
        e["needs"] = remaining_needs(rec, before)
        e["history"].append({"at": now, "class": klass, "needs": before, **outcome})
        if outcome.get("index_page"):
            e["status"] = "index_page"
        elif not e["needs"]:
            e["status"] = "recovered"
        elif e["recovery_attempts"] >= MAX_RECOVERY_PASSES:
            e["status"] = "given_up"
        rec["recovery_status"] = e["status"]
        rec["recovery_attempts"] = e["recovery_attempts"]
        rec["last_recovery_at"] = now
        e["resolved_url"] = rec.get("resolved_url")
        e["text_status"] = rec.get("archive_status")
        e["publisher_status"] = rec.get("publisher_status")
        # Duplicate rows of the same article (dedup off) get the same result.
        for other in ordered:
            if other is not rec and other.get("key") == rec["key"]:
                for f in COPY_FIELDS:
                    if f in rec:
                        other[f] = rec[f]
        k = (klass, e["status"])
        tally[k] = tally.get(k, 0) + 1
        print(f"[{i:>2}/{len(todo)}] {e['status']:<9} {klass:<9} {json.dumps(outcome)}  {e['title'][:50]}",
              flush=True)
        if i < len(todo):
            time.sleep(args.delay)

    at.save_queue(queue)
    db.replace_all(ordered)

    REPORTS_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {"run_utc": stamp, "worked": len(todo), "swept_in": added,
              "by_class_and_status": {f"{c}|{s}": n for (c, s), n in sorted(tally.items())},
              "queue_counts": json.loads(at.RECOVERY_QUEUE.read_text(encoding="utf-8"))["counts"]}
    (REPORTS_DIR / f"recovery-{stamp}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"\nDone: {report['by_class_and_status']}  queue now {report['queue_counts']}")


if __name__ == "__main__":
    main()
