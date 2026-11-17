#!/usr/bin/env python3
"""Compare two relevance dictionaries on the SAME Google News feed.

Queries the crawler's feed once (same sources, same queries, same --days window as
build_feed.py), then classifies every headline with dictionary A and dictionary B
through the production matching code (build_feed.make_relevance) and reports what
differs. Because both filters see identical items, any difference is caused by the
dictionaries alone -- not by the feed moving between two crawls.

READ-ONLY with respect to the project: it never touches the Postgres archive (the
`db` module is stubbed out) and never writes feed/public/archive files. It only
writes three files into --out-dir:
    <tag>_snapshot.csv   the raw fetched items (replayable: --replay)
    <tag>_results.csv    every item with A and B flags and a change status
    <tag>_summary.txt    the report printed to the console

Usage (from scsdi_rss/):
    python compare_filters.py                              # baseline (pre-2026-11-13) vs live CSV
    python compare_filters.py --b path/to/candidate.csv    # preview a candidate dictionary
    python compare_filters.py --replay <tag>_snapshot.csv --b candidate.csv   # offline, same items
    python compare_filters.py --limit 5 --days 7           # quick test
"""
import argparse
import csv
import datetime as dt
import json
import sys
import time
import types
from collections import Counter
from pathlib import Path

sys.modules["db"] = types.ModuleType("db")   # never connect to / modify the production archive
import build_feed as bf  # noqa: E402  (after the db stub on purpose)

HERE = Path(__file__).resolve().parent
DEFAULT_A = HERE / "dictionaries" / "baseline_v1.0_pre-2026-11-13.csv"
DEFAULT_B = HERE / "term_dictionary.csv"
DEFAULT_OUT = HERE.parent / "validation" / "filter_comparison"
SNAP_FIELDS = ["fetched_utc", "domain", "country", "hl", "source_name", "outlet", "title", "link", "pubDate"]


def fetch_feed(args):
    sources = json.loads(bf.SOURCES.read_text(encoding="utf-8"))
    if args.only:
        want = {d.strip() for d in args.only.split(",")}
        sources = [s for s in sources if s["domain"] in want]
    if args.limit:
        sources = sources[: args.limit]
    rows, per_source = [], []
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    for i, s in enumerate(sources, 1):
        url = bf.gnews_url(s["domain"], args.days, s["hl"], s["gl"], s["ceid"], s["country"])
        try:
            items = list(bf.parse_items(bf.fetch(url), s))
            status = "ok"
        except Exception as e:  # noqa: BLE001
            items, status = [], f"ERROR: {e}"
        for it in items:
            rows.append({"fetched_utc": now, "domain": it["domain"], "country": it["country"], "hl": s["hl"],
                         "source_name": it["source_name"], "outlet": it["outlet"], "title": it["title"],
                         "link": it["link"], "pubDate": it["pubDate"]})
        per_source.append({"domain": s["domain"], "country": s["country"], "fetched": len(items), "status": status})
        print(f"[{i:>2}/{len(sources)}] {s['domain']:<28} {s['hl']:<6} fetched={len(items):<3} {status}", flush=True)
        if i < len(sources):
            time.sleep(args.delay)
    return rows, per_source


def read_snapshot(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f)), []


def write_csv(path, rows, fields):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:   # BOM: opens correctly in Excel
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a", default=str(DEFAULT_A), help="dictionary A (the 'before'); default = frozen pre-update baseline")
    ap.add_argument("--b", default=str(DEFAULT_B), help="dictionary B (the 'after'); default = live term_dictionary.csv")
    ap.add_argument("--days", type=int, default=7, help="Google News window, as build_feed.py (when:Nd)")
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between requests")
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--replay", default="", help="re-use a saved *_snapshot.csv instead of querying Google")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    rel_a = bf.make_relevance(bf.load_dictionary(Path(args.a)))
    rel_b = bf.make_relevance(bf.load_dictionary(Path(args.b)))

    if args.replay:
        items, per_source = read_snapshot(args.replay)
        origin = f"replayed from {Path(args.replay).name}"
    else:
        items, per_source = fetch_feed(args)
        origin = f"live Google News feed, last {args.days} days"
    tag = dt.datetime.now().strftime("%Y%m%d-%H%M")
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    if not args.replay:
        write_csv(out / f"{tag}_snapshot.csv", items, SNAP_FIELDS)

    link_count = Counter(r["link"] for r in items)
    results = []
    for r in items:
        a_rel, a_tier = rel_a(r["title"]); b_rel, b_tier = rel_b(r["title"])
        if a_rel == b_rel:
            status = "same" if a_tier == b_tier else "tier_changed"
        else:
            status = "new_in_B" if b_rel else "dropped_in_B"
        results.append({**r, "a_relevant": int(a_rel), "a_tier": a_tier, "b_relevant": int(b_rel), "b_tier": b_tier,
                        "status": status, "duplicate_link_in_feed": int(link_count[r["link"]] > 1)})
    write_csv(out / f"{tag}_results.csv", results, SNAP_FIELDS + ["a_relevant", "a_tier", "b_relevant", "b_tier", "status", "duplicate_link_in_feed"])

    # ---- summary ----
    uniq = {}
    for r in results:
        uniq.setdefault(r["link"], r)
    L = []
    L.append(f"Filter comparison  ({origin}; run {dt.datetime.now():%Y-%m-%d %H:%M})")
    L.append(f"  A (before): {args.a}")
    L.append(f"  B (after) : {args.b}")
    bad = [p for p in per_source if p["status"] != "ok"]
    L.append(f"  sources queried: {len(per_source)}  (failed: {len(bad)})" if per_source else "  sources: (replay)")
    for p in bad:
        L.append(f"     FAILED {p['domain']}: {p['status'][:80]}")
    L.append(f"  items fetched: {len(results)}  | unique links: {len(uniq)}")
    for label, rows in (("all rows", results), ("unique links", list(uniq.values()))):
        tbl = Counter((r["a_relevant"], r["b_relevant"]) for r in rows)
        L.append(f"\n  2x2, {label}   (rows = A, cols = B)")
        L.append("              B=0     B=1    total")
        for a in (0, 1):
            L.append(f"     A={a}   {tbl[(a,0)]:>7} {tbl[(a,1)]:>7} {tbl[(a,0)]+tbl[(a,1)]:>8}")
        L.append(f"     total {tbl[(0,0)]+tbl[(1,0)]:>7} {tbl[(0,1)]+tbl[(1,1)]:>7} {len(rows):>8}")
    st = Counter(r["status"] for r in uniq.values())
    L.append(f"\n  status (unique links): {dict(st)}")
    changed = [r for r in uniq.values() if r["status"] != "same"]
    if not changed:
        L.append("\n  NO DIFFERENCES: dictionaries A and B classify every fetched headline identically.")
    else:
        L.append("\n  Changed headlines:")
        for r in sorted(changed, key=lambda r: (r["status"], r["country"], r["source_name"])):
            L.append(f"    [{r['status']:<12}] {r['a_tier']:>7} -> {r['b_tier']:<7} {r['country']:<4} {r['source_name'][:16]:<16} {r['title'][:100]}")
    text = "\n".join(L)
    (out / f"{tag}_summary.txt").write_text(text + "\n", encoding="utf-8-sig")
    print("\n" + text)
    print(f"\nwrote {out / (tag + '_results.csv')}")


if __name__ == "__main__":
    main()
