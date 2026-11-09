#!/usr/bin/env python3
"""Export the SCSDI news archive to a CSV for manual review.

One row per archived article: the relevance tag (`relevant` / `not_relevant`)
and matched tier, provenance, and links. Links are, in order of directness:
  - resolved_url : the real publisher URL (direct) -- only where text-archiving ran
  - link         : the Google News link (a redirect; clickable, opens the article)
  - wayback_url  : Internet Archive snapshot, where captured
Intended for an RA to audit false positives / false negatives.

Usage:
    python export_results.py                    # all records -> exports/scsdi_articles_<date>.csv
    python export_results.py --relevant-only
    python export_results.py --window-days 7    # only items within N days (match dashboard)
    python export_results.py --out path.csv
"""
import argparse
import csv
import datetime as dt
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARCHIVE = HERE / "archive" / "articles.jsonl"


def parse_date(s):
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def rec_dt(r):
    d = parse_date(r.get("pubDate") or "")
    if d is None:
        try:
            d = dt.datetime.fromisoformat(r["first_seen"])
        except (ValueError, KeyError):
            return None
    return d.replace(tzinfo=None) if d and d.tzinfo else d


FIELDS = ["relevant", "tier", "text_relevant", "country", "outlet", "source_name",
          "domain", "pubDate", "first_seen", "title",
          "resolved_url", "link", "wayback_url"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--relevant-only", action="store_true")
    ap.add_argument("--window-days", type=int, default=0, help="0 = all records")
    args = ap.parse_args()

    rows = [json.loads(l) for l in ARCHIVE.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    if args.window_days:
        cut = dt.datetime.utcnow() - dt.timedelta(days=args.window_days)
        rows = [r for r in rows if (rec_dt(r) or dt.datetime.min) >= cut]
    if args.relevant_only:
        rows = [r for r in rows if r.get("relevant")]
    rows.sort(key=lambda r: rec_dt(r) or dt.datetime.min, reverse=True)  # newest first

    out = Path(args.out) if args.out else (
        HERE / "exports" / f"scsdi_articles_{dt.datetime.now():%Y%m%d}.csv")
    out.parent.mkdir(exist_ok=True)
    # utf-8-sig so Excel renders Vietnamese/Chinese correctly on open.
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            row = {k: r.get(k, "") for k in FIELDS}
            row["relevant"] = "relevant" if r.get("relevant") else "not_relevant"
            if r.get("text_relevant") is True:
                row["text_relevant"] = "yes"
            elif r.get("text_archived") and not r.get("text_relevant"):
                row["text_relevant"] = "no"
            else:
                row["text_relevant"] = ""      # body not archived -> unknown
            w.writerow(row)

    n_rel = sum(1 for r in rows if r.get("relevant"))
    res = sum(1 for r in rows if r.get("resolved_url"))
    print(f"wrote {len(rows)} rows ({n_rel} relevant, {len(rows)-n_rel} not) -> {out}")
    print(f"direct (resolved) URLs: {res}/{len(rows)} present; the rest carry the "
          f"Google News link only (clickable -- redirects to the article).")


if __name__ == "__main__":
    main()
