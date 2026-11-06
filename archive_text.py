#!/usr/bin/env python3
"""Archive the full text of relevant SCSDI articles.

The Google News feed carries only a headline + a redirect link, so for each
relevant article not yet archived this script:
  1. resolves the Google News redirect to the real article URL (batchexecute);
  2. fetches the page and extracts the body with trafilatura;
  3. runs a rule-based *text* relevance check (sea/feature + actor/incident),
     which can tell an incident apart from a weather report that merely mentions
     the same waters -- something the title-only filter cannot;
  4. snapshots the real URL to the Internet Archive Wayback Machine (citable,
     link-rot-proof provenance);
  5. records the resolved URL, Wayback URL and text stats back into the archive,
     and stores the extracted text under archive/text/.

Durability: the extracted text lives in the repo (your copy); the Wayback URL is
the public citation. Raw HTML is not committed by default (repo bloat) -- pass
--save-html to keep gzipped originals locally.

Requires: trafilatura.  Everything else is standard library.

Usage:
    python archive_text.py                 # up to --limit relevant, unarchived
    python archive_text.py --limit 5       # small batch (default 12)
    python archive_text.py --delay 4       # politeness gap between articles
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request

import trafilatura

import db
from build_feed import (ARCHIVE_DIR, REL_ACTOR, REL_FEATURE,
                        REL_SEA, load_archive)

TEXT_DIR = ARCHIVE_DIR / "text"
HTML_DIR = ARCHIVE_DIR / "html"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MAX_ATTEMPTS = 3

# Incident/activity vocabulary (English-centric v1) added to the actor terms to
# separate real incidents from passing mentions of the sea.
INCIDENT = [
    "ram", "rammed", "collision", "collide", "water cannon", "blockade",
    "harass", "incursion", "intrud", "drove away", "drive away", "expel",
    "seize", "seized", "detain", "confront", "standoff", "stand-off",
    "resupply", "supply mission", "swarm", "laser", "militia", "patrol",
    "intercept", "shadow", "chase", "fired", "freedom of navigation",
    "drill", "exercise", "incident", "protest", "sovereignty", "dispute",
]


def _req(url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers=headers or {"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r, r.read()


def resolve_gnews(link):
    """Decode a Google News redirect to the real article URL (or None)."""
    p = urllib.parse.urlparse(link)
    parts = [x for x in p.path.split("/") if x]
    if "articles" not in parts:
        return link if p.netloc and "news.google" not in p.netloc else None
    art_id = parts[parts.index("articles") + 1]
    html = _req(f"https://news.google.com/rss/articles/{art_id}")[1].decode("utf-8", "replace")
    sg = re.search(r'data-n-a-sg="([^"]+)"', html)
    ts = re.search(r'data-n-a-ts="([^"]+)"', html)
    aid = re.search(r'data-n-a-id="([^"]+)"', html)
    if not (sg and ts):
        return None
    gn_id = aid.group(1) if aid else art_id
    inner = ["garturlreq",
             [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
              "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
             gn_id, int(ts.group(1)), sg.group(1)]
    freq = [[["Fbv4je", json.dumps(inner), None, "generic"]]]
    payload = urllib.parse.urlencode({"f.req": json.dumps(freq)}).encode()
    resp = _req("https://news.google.com/_/DotsSplashUi/data/batchexecute", data=payload,
                headers={"User-Agent": UA,
                         "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})[1]
    for line in resp.decode("utf-8", "replace").split("\n"):
        line = line.strip()
        if not line.startswith("["):
            continue
        try:
            arr = json.loads(line)
        except json.JSONDecodeError:
            continue
        for row in arr:
            if isinstance(row, list) and len(row) > 2 and isinstance(row[2], str) and "garturlres" in row[2]:
                return json.loads(row[2])[1]
    return None


def text_relevance(text):
    """(is_relevant, scs_hits, act_hits): needs a place AND an incident/actor."""
    low = text.lower()
    scs = sum(low.count(t.lower()) for t in (REL_SEA + REL_FEATURE))
    act = sum(low.count(t.lower()) for t in (REL_ACTOR + INCIDENT))
    return (scs > 0 and act > 0), scs, act


def wayback_save(url, timeout=90):
    """Submit to the Wayback Machine; return the snapshot URL or None."""
    try:
        r, _ = _req("https://web.archive.org/save/" + url,
                    headers={"User-Agent": UA}, timeout=timeout)
        cl = r.headers.get("Content-Location")
        if cl:
            return "https://web.archive.org" + cl
        return r.geturl()
    except Exception:
        # Fall back to whatever snapshot already exists.
        try:
            api = "https://archive.org/wayback/available?url=" + urllib.parse.quote(url)
            data = json.loads(_req(api, timeout=30)[1])
            snap = data.get("archived_snapshots", {}).get("closest", {})
            return snap.get("url")
        except Exception:
            return None


def keyhash(key):
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=12, help="max articles this run")
    ap.add_argument("--delay", type=float, default=4.0, help="seconds between articles")
    ap.add_argument("--save-html", action="store_true", help="also keep gzipped raw HTML")
    args = ap.parse_args()

    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    if args.save_html:
        HTML_DIR.mkdir(parents=True, exist_ok=True)

    by_key, ordered = load_archive()
    todo = [r for r in ordered
            if r.get("relevant")
            and not r.get("text_archived")
            and r.get("archive_attempts", 0) < MAX_ATTEMPTS][: args.limit]
    print(f"Archiving {len(todo)} of {sum(1 for r in ordered if r.get('relevant'))} relevant articles")

    done = fail = 0
    for i, rec in enumerate(todo, 1):
        rec["archive_attempts"] = rec.get("archive_attempts", 0) + 1
        rec["archived_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        status = "ok"
        try:
            real = resolve_gnews(rec["link"])
            if not real:
                status = "resolve_failed"
            else:
                rec["resolved_url"] = real
                html = _req(real, timeout=30)[1]
                text = trafilatura.extract(html.decode("utf-8", "replace"),
                                           include_comments=False, include_tables=False) or ""
                if len(text) < 200:
                    status = "no_text"
                else:
                    h = keyhash(rec["key"])
                    (TEXT_DIR / f"{h}.txt").write_text(text, encoding="utf-8")
                    rec["text_file"] = f"archive/text/{h}.txt"
                    rec["text_chars"] = len(text)
                    tr, scs, act = text_relevance(text)
                    rec["text_relevant"], rec["text_scs_hits"], rec["text_actor_hits"] = tr, scs, act
                    if args.save_html:
                        with gzip.open(HTML_DIR / f"{h}.html.gz", "wb") as f:
                            f.write(html)
                    rec["wayback_url"] = wayback_save(real)
                    rec["text_archived"] = True
                    done += 1
        except Exception as e:  # noqa: BLE001
            status = f"error: {type(e).__name__}"
        rec["archive_status"] = status
        if status != "ok":
            fail += 1
        mark = "OK " if status == "ok" else "!! "
        print(f"[{i:>2}/{len(todo)}] {mark}{rec['country']:<3} {rec.get('text_chars','-'):>6} "
              f"ch  tr={rec.get('text_relevant','-')}  wb={'y' if rec.get('wayback_url') else 'n'}  "
              f"{status}  {rec['title'][:40]}", flush=True)
        if i < len(todo):
            time.sleep(args.delay)

    # Rewrite the archive with the updated records (pilot scale -> full rewrite).
    db.replace_all(ordered)

    total_arch = sum(1 for r in ordered if r.get("text_archived"))
    print(f"\nArchived this run: {done}  failed: {fail}  |  total text-archived: {total_arch}")
    print("Re-run `build_feed.py --no-crawl` to refresh outputs with text-relevance.")


if __name__ == "__main__":
    main()
