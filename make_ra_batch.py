#!/usr/bin/env python3
"""Build an RA review batch: ALL relevant articles + a reproducible random sample
of N non-relevant ones, with direct publisher links resolved, exported to CSV.

It resolves any unresolved links *in the batch* (in parallel) via
googlenewsdecoder and writes `resolved_url` back into the archive, so the work
also counts toward the eventual full-archive resolution. The non-relevant sample
is seeded (--seed) so the batch is reproducible and the RA's false-negative check
is an unbiased draw.

Usage:
    python make_ra_batch.py                                  # all relevant + 150 non-relevant
    python make_ra_batch.py --n-nonrelevant 150 --seed 42 --workers 6
"""
import argparse
import csv
import datetime as dt
import json
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from googlenewsdecoder import gnewsdecoder

HERE = Path(__file__).resolve().parent
ARCHIVE = HERE / "archive" / "articles.jsonl"
SOURCES = HERE / "sources.json"
FIELDS = ["relevant", "tier", "text_relevant", "country", "language", "outlet",
          "source_name", "domain", "pubDate", "first_seen", "title",
          "resolved_url", "link", "wayback_url"]

# Google News `hl` code -> readable publishing/search language for the RA.
HL_LANG = {"en": "English", "id": "Indonesian", "ja": "Japanese",
           "vi": "Vietnamese", "zh-TW": "Chinese (Traditional)"}


def language_map():
    """Map (country, domain) -> language label from sources.json. Keyed on both
    because one domain (bbc.com) is searched in two languages across countries."""
    try:
        srcs = json.load(SOURCES.open(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {(s.get("country"), s.get("domain")): HL_LANG.get(s.get("hl", ""),
                                                             s.get("hl", ""))
            for s in srcs}


def load():
    return [json.loads(l) for l in ARCHIVE.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def save(rows):
    tmp = ARCHIVE.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(ARCHIVE)


def rec_dt(r):
    s = r.get("pubDate") or ""
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=None)
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(r["first_seen"]).replace(tzinfo=None)
    except (ValueError, KeyError):
        return dt.datetime.min


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-nonrelevant", type=int, default=150)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    rows = load()
    rel = [r for r in rows if r.get("relevant")]
    non = [r for r in rows if not r.get("relevant")]
    random.seed(args.seed)
    sample_non = random.sample(non, min(args.n_nonrelevant, len(non)))
    batch = rel + sample_non
    print(f"batch: {len(rel)} relevant + {len(sample_non)} non-relevant = {len(batch)}")

    todo = [r for r in batch if not r.get("resolved_url") and r.get("link")]
    print(f"resolving {len(todo)} unresolved links with {args.workers} workers...", flush=True)
    lock = threading.Lock()
    prog = {"done": 0, "ok": 0, "fail": 0}

    def work(r):
        url = None
        err = None
        try:
            res = gnewsdecoder(r["link"], interval=1)
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
            if prog["done"] % 25 == 0:
                print(f"  {prog['done']}/{len(todo)} ok={prog['ok']} fail={prog['fail']}",
                      flush=True)

    if todo:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(work, todo))
        save(rows)
    print(f"resolved ok={prog['ok']} fail={prog['fail']}; archive saved", flush=True)

    # Export: relevant first, then non-relevant; newest within each group.
    batch.sort(key=lambda r: (not r.get("relevant"), rec_dt(r)), reverse=False)
    batch.sort(key=lambda r: rec_dt(r), reverse=True)
    batch.sort(key=lambda r: not r.get("relevant"))  # stable: relevant block first
    out = Path(args.out) if args.out else (
        HERE / "exports" / f"ra_batch_{dt.datetime.now():%Y%m%d}.csv")
    out.parent.mkdir(exist_ok=True)
    langs = language_map()
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in batch:
            row = {k: r.get(k, "") for k in FIELDS}
            row["language"] = langs.get((r.get("country"), r.get("domain")), "")
            row["relevant"] = "relevant" if r.get("relevant") else "not_relevant"
            row["text_relevant"] = ("yes" if r.get("text_relevant")
                                    else ("no" if r.get("text_archived") else ""))
            w.writerow(row)
    res = sum(1 for r in batch if r.get("resolved_url"))
    print(f"wrote {len(batch)} rows ({len(rel)} relevant) -> {out}")
    print(f"direct URLs in batch: {res}/{len(batch)}")


if __name__ == "__main__":
    main()
