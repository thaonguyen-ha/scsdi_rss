#!/usr/bin/env python3
"""Archive the full text of relevant SCSDI articles.

The Google News feed carries only a headline + a redirect link, so for each
relevant article not yet archived this script:
  1. resolves the Google News redirect to the real article URL (batchexecute);
  2. fetches the page and extracts the body with trafilatura; if the publisher
     refuses us (many block scripted requests), it falls back to the body of an
     existing Wayback Machine copy;
  3. runs a rule-based *text* relevance check (sea/feature + actor/incident),
     which can tell an incident apart from a weather report that merely mentions
     the same waters -- something the title-only filter cannot;
  4. records the resolved URL and text stats back into the archive, and stores
     the extracted text under archive/text/.

Separately, EVERY relevant article gets a Wayback Machine snapshot request
(citable, link-rot-proof provenance), whether or not we could fetch it
ourselves -- the Wayback Machine fetches from its own servers.

Retries: a failed fetch is retried up to MAX_ATTEMPTS (3) times, at most once
per RETRY_GAP_HOURS, so a temporary outage gets a few days rather than hours.
Never-tried articles go first, so repeat failures don't hold up new ones.
Failures are recorded with the HTTP status (``http_403``, ``timeout``, ...).
An article that still lacks text or a Wayback snapshot after its tries is
written to archive/recovery_queue.json (RECOVERY_QUEUE) and left to the weekly
recover_archive.py pass, which retries it by failure type.

Durability: the extracted text lives in the repo (your copy); the Wayback URL is
the public citation. Raw HTML is not committed by default (repo bloat) -- pass
--save-html to keep gzipped originals locally.

Requires: trafilatura.  Everything else is standard library.

Usage:
    python archive_text.py                 # up to --limit relevant, unarchived
    python archive_text.py --limit 5       # small batch (default 12)
    python archive_text.py --delay 4       # politeness gap between articles
    python archive_text.py --wayback-limit 6   # Wayback snapshot requests per run
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import trafilatura

import db
from build_feed import (ARCHIVE_DIR, REL_ACTOR, REL_FEATURE,
                        REL_SEA, classify, is_index_url, load_archive)

TEXT_DIR = ARCHIVE_DIR / "text"
HTML_DIR = ARCHIVE_DIR / "html"
RECOVERY_QUEUE = ARCHIVE_DIR / "recovery_queue.json"   # handed to recover_archive.py
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MAX_ATTEMPTS = 3            # text-fetch tries per article before the weekly queue
MAX_WAYBACK_ATTEMPTS = 3    # Wayback snapshot requests per article
RETRY_GAP_HOURS = 20        # min gap between tries; with the 6-hourly cron
                            # that is about one try per day
MIN_TEXT_CHARS = 200        # shorter extractions are treated as "no text"
MIN_WAYBACK_TEXT_CHARS = 600  # stricter for Wayback copies: of pages that load
                              # their article with JavaScript, the capture holds
                              # only the site menu (a few hundred characters)

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


def _ascii_url(url):
    """Percent-encode non-ASCII characters (accented paths, Vietnamese slugs);
    urllib refuses them raw. Already-encoded %XX sequences are left alone."""
    return urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%~")


def _req(url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(_ascii_url(url), data=data, headers=headers or {"User-Agent": UA})
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


def status_of(exc):
    """Short, specific failure label: http_403, timeout, url_error, ..."""
    if isinstance(exc, urllib.error.HTTPError):  # before URLError: it's a subclass
        return f"http_{exc.code}"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        return "timeout" if isinstance(reason, TimeoutError) or "timed out" in str(reason) else "url_error"
    return f"error_{type(exc).__name__}"


WAYBACK_PAUSE = 5  # seconds before each Wayback request; it rate-limits bursts
# The Wayback Machine blocks browser-imitating user agents from scripts as
# "suspected abusive bot traffic" (HTTP 429), so identify ourselves honestly.
WAYBACK_UA = ("scsdi-rss-archiver/1.0 (research news archive; "
              "+https://github.com/thaonguyen-ha/scsdi_rss)")


def _wb_req(url, timeout=60):
    """A Wayback Machine request: honest user agent, paced, one retry on 429."""
    for attempt in (1, 2):
        time.sleep(WAYBACK_PAUSE)
        try:
            r, body = _req(url, timeout=timeout, headers={"User-Agent": WAYBACK_UA})
            if body[:2] == b"\x1f\x8b":  # raw captures keep the site's gzip encoding
                body = gzip.decompress(body)
            return r, body
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 2:
                raise
            wait = e.headers.get("Retry-After", "")
            time.sleep(min(int(wait), 120) if wait.isdigit() else 30)


def _norm_url(u):
    u = urllib.parse.unquote(u).lower()
    return re.sub(r"^https?://(www\.)?", "", u).rstrip("/")


def snapshot_matches(snap, url):
    """True if a snapshot URL is a capture of ``url`` itself. The availability
    API matches loosely and can hand back a different page on the same site
    (e.g. a tag page), which must not be used as the article."""
    m = re.match(r"https?://web\.archive\.org/web/\d+[a-z_]*/(.+)", snap or "")
    return bool(m) and _norm_url(m.group(1)) == _norm_url(url)


def wayback_lookup(url, timeout=30):
    """URL of the closest existing Wayback snapshot of exactly this page that
    captured real content (HTTP 200, not a block or error page), or None."""
    try:
        api = "https://archive.org/wayback/available?url=" + urllib.parse.quote(url, safe="")
        snap = json.loads(_wb_req(api, timeout=timeout)[1]).get("archived_snapshots", {}).get("closest", {})
        if (snap.get("available") and str(snap.get("status")) == "200"
                and snapshot_matches(snap.get("url"), url)):
            return snap.get("url")
    except Exception:  # noqa: BLE001
        pass
    return None


def wayback_save(url, timeout=60):
    """Ask the Wayback Machine to snapshot ``url``. It fetches from its own
    servers, so this often works even when the publisher blocks us. Returns the
    new snapshot URL, else the closest existing good snapshot, else None."""
    try:
        r, _ = _wb_req("https://web.archive.org/save/" + url, timeout=timeout)
        cl = r.headers.get("Content-Location")
        snap = ("https://web.archive.org" + cl) if cl else r.geturl()
        if snapshot_matches(snap, url):
            return snap
    except Exception:  # noqa: BLE001
        pass
    return wayback_lookup(url)


def raw_snapshot_url(snap):
    """.../web/<ts>/<url> -> .../web/<ts>id_/<url>: the page as captured,
    without the Wayback toolbar wrapped around it."""
    m = re.match(r"(https?://web\.archive\.org/web/)(\d+)[a-z_]*/(.+)", snap)
    return f"{m.group(1)}{m.group(2)}id_/{m.group(3)}" if m else snap


def extract_text(html, favor_recall=False):
    return trafilatura.extract(html.decode("utf-8", "replace"), favor_recall=favor_recall,
                               include_comments=False, include_tables=False) or ""


def keyhash(key):
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def is_due(rec, field, now, fallback=None):
    """True if the last try (``field``, else ``fallback``) is older than the gap."""
    last = rec.get(field) or (rec.get(fallback) if fallback else None)
    try:
        last = dt.datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return True  # never tried, or no usable timestamp
    if last.tzinfo is None:
        last = last.replace(tzinfo=dt.timezone.utc)
    return now - last >= dt.timedelta(hours=RETRY_GAP_HOURS)


def resolve(rec):
    """The article's real URL (cached in ``resolved_url``), or None."""
    if not rec.get("resolved_url"):
        real = resolve_gnews(rec["link"])
        if real:
            rec["resolved_url"] = real
    return rec.get("resolved_url")


def requeue_legacy_failures(ordered):
    """One-time fix-up: articles that failed under the old rule (3 tries on
    back-to-back runs, status 'error: HTTPError' with no code) get a fresh set
    of spaced-out retries. Their new status carries the code, so this runs once."""
    n = 0
    for r in ordered:
        if (r.get("relevant") and not r.get("text_archived")
                and (r.get("archive_status") or "").startswith("error: ")):
            r["archive_attempts"] = 0
            r["archive_status"] = "requeued"
            n += 1
    return n


def load_queue(path=None):
    """The recovery queue as {key: entry}. Missing file -> empty queue."""
    path = path or RECOVERY_QUEUE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return {e["key"]: e for e in data.get("items", [])}


def save_queue(queue, path=None):
    path = path or RECOVERY_QUEUE
    items = sorted(queue.values(), key=lambda e: (e.get("status") != "queued", e.get("queued_at") or ""))
    counts = {}
    for e in items:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    path.write_text(json.dumps({"updated_at": now_iso(), "counts": counts, "items": items},
                               ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def needs_of(rec):
    """What an article still lacks after its incremental tries are used up."""
    needs = []
    if not rec.get("text_archived") and rec.get("archive_attempts", 0) >= MAX_ATTEMPTS:
        needs.append("text")
    if not rec.get("wayback_url") and rec.get("wayback_attempts", 0) >= MAX_WAYBACK_ATTEMPTS:
        needs.append("wayback")
    return needs


def enqueue_exhausted(relevant, queue):
    """Add articles whose incremental tries are used up to the recovery queue
    (or widen an existing queued entry's needs). Returns the number added."""
    added = 0
    for rec in relevant:
        if not rec.get("relevant"):   # turned out to be an index page this run
            continue
        needs = needs_of(rec)
        if not needs:
            continue
        e = queue.get(rec["key"])
        if e is None:
            if rec.get("recovery_status") in ("recovered", "given_up"):
                continue   # the weekly pass already settled it
            e = queue[rec["key"]] = {
                "key": rec["key"], "title": rec.get("title"), "link": rec.get("link"),
                "country": rec.get("country"), "domain": rec.get("domain"),
                "status": "queued", "needs": [], "queued_at": now_iso(),
                "recovery_attempts": 0, "history": [],
            }
            added += 1
        elif e["status"] != "queued":
            continue
        e["needs"] = sorted(set(e["needs"]) | set(needs))
        # Failure context the weekly pass uses to choose its strategy.
        e["resolved_url"] = rec.get("resolved_url")
        e["text_status"] = rec.get("archive_status")
        e["publisher_status"] = rec.get("publisher_status")
        e["text_attempts"] = rec.get("archive_attempts", 0)
        e["wayback_attempts"] = rec.get("wayback_attempts", 0)
        rec["recovery_status"] = "queued"
    return added


def drop_index_pages(records):
    """Re-check relevant records with the index-page rules (headline markers,
    and the resolved URL once known); index pages become relevant=False,
    tier='index', and lose any 'text' archived from them (a list of other
    headlines, which would skew text_relevant). Returns how many were dropped."""
    n = 0
    for rec in records:
        if not rec.get("relevant"):
            continue
        rel, tier = classify(rec)
        if tier == "index":
            rec["relevant"], rec["tier"] = False, "index"
            rec["archive_status"] = "index_page"
            for f in ("text_archived", "text_relevant"):
                if rec.get(f):
                    rec[f] = False
            n += 1
    return n


def fetch_text(rec, save_html):
    """Fetch and store one article's body text. Tries the publisher first and
    falls back to an existing Wayback copy. Returns the status label."""
    try:
        real = resolve(rec)
    except Exception as e:  # noqa: BLE001
        return "resolve_" + status_of(e)
    if not real:
        return "resolve_failed"
    if is_index_url(real):
        return "index_page"   # tag/topic/author page: nothing to archive

    source = "publisher"
    try:
        html = _req(real, timeout=30)[1]
        rec.pop("publisher_status", None)
    except Exception as e:  # noqa: BLE001
        err = status_of(e)
        rec["publisher_status"] = err
        snap = wayback_lookup(real)
        if not snap:
            return err
        try:
            html = _wb_req(raw_snapshot_url(snap), timeout=60)[1]
        except Exception:  # noqa: BLE001
            return err
        source = "wayback"
        if not rec.get("wayback_url"):  # older records may hold None here
            rec["wayback_url"] = snap

    return store_text(rec, html, source, save_html)


def store_text(rec, html, source, save_html=False, favor_recall=False):
    """Extract the body from ``html`` and record it on ``rec``. ``source`` is
    'publisher' or 'wayback'. Returns the status label (ok / ok_wayback /
    no_text / wayback_no_text)."""
    text = extract_text(html, favor_recall=favor_recall)
    if source == "publisher" and len(text) < MIN_TEXT_CHARS:
        return "no_text"
    if source == "wayback" and len(text) < MIN_WAYBACK_TEXT_CHARS:
        return "wayback_no_text"

    h = keyhash(rec["key"])
    (TEXT_DIR / f"{h}.txt").write_text(text, encoding="utf-8")
    if save_html:
        with gzip.open(HTML_DIR / f"{h}.html.gz", "wb") as f:
            f.write(html)
    rec["text_file"] = f"archive/text/{h}.txt"
    rec["text_chars"] = len(text)
    rec["text_source"] = source
    tr, scs, act = text_relevance(text)
    rec["text_relevant"], rec["text_scs_hits"], rec["text_actor_hits"] = tr, scs, act
    rec["text_archived"] = True
    return "ok" if source == "publisher" else "ok_wayback"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=12, help="max text fetches this run")
    ap.add_argument("--wayback-limit", type=int, default=6,
                    help="max Wayback snapshot requests this run")
    ap.add_argument("--delay", type=float, default=4.0, help="seconds between requests")
    ap.add_argument("--save-html", action="store_true", help="also keep gzipped raw HTML")
    args = ap.parse_args()

    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    if args.save_html:
        HTML_DIR.mkdir(parents=True, exist_ok=True)

    by_key, ordered = load_archive()
    now = dt.datetime.now(dt.timezone.utc)
    requeued = requeue_legacy_failures(ordered)
    if requeued:
        print(f"Requeued {requeued} articles that failed under the old retry rule")
    dropped = drop_index_pages(ordered)
    if dropped:
        print(f"Dropped {dropped} index pages (tag/topic/author/archive) from the relevant set")
    relevant = [r for r in ordered if r.get("relevant")]

    # --- 1. Body text --------------------------------------------------------
    # Fewest attempts first (sorted() is stable, so archive order breaks ties):
    # new articles are never stuck behind ones that keep failing.
    todo = sorted((r for r in relevant
                   if not r.get("text_archived")
                   and r.get("archive_attempts", 0) < MAX_ATTEMPTS
                   and is_due(r, "last_attempt_at", now, fallback="archived_at")),
                  key=lambda r: r.get("archive_attempts", 0))[: args.limit]
    waiting = sum(1 for r in relevant if not r.get("text_archived")
                  and r.get("archive_attempts", 0) < MAX_ATTEMPTS)
    print(f"Text: fetching {len(todo)} now ({waiting} relevant articles still need text)")

    done = fail = 0
    for i, rec in enumerate(todo, 1):
        rec["archive_attempts"] = rec.get("archive_attempts", 0) + 1
        rec["last_attempt_at"] = now_iso()
        try:
            status = fetch_text(rec, args.save_html)
        except Exception as e:  # noqa: BLE001
            status = status_of(e)
        rec["archive_status"] = status
        if status == "index_page":   # found out only now, from the resolved URL
            rec["relevant"], rec["tier"] = False, "index"
            rec["archive_attempts"] -= 1
        if status.startswith("ok"):
            done += 1
        else:
            fail += 1
        mark = "OK " if status.startswith("ok") else "!! "
        print(f"[{i:>2}/{len(todo)}] {mark}{rec['country']:<3} {rec.get('text_chars','-'):>6} "
              f"ch  tr={rec.get('text_relevant','-')}  try {rec['archive_attempts']}/{MAX_ATTEMPTS}  "
              f"{status}  {rec['title'][:40]}", flush=True)
        if i < len(todo):
            time.sleep(args.delay)

    # --- 2. Wayback snapshots, for EVERY relevant article ---------------------
    # Independent of step 1: an article we could not fetch still gets a citable
    # snapshot, because the Wayback Machine fetches it from its own servers.
    wb_todo = sorted((r for r in relevant
                      if r.get("relevant")   # step 1 may have found index pages
                      and not r.get("wayback_url")
                      and r.get("wayback_attempts", 0) < MAX_WAYBACK_ATTEMPTS
                      and is_due(r, "wayback_last_attempt_at", now)),
                     key=lambda r: r.get("wayback_attempts", 0))[: args.wayback_limit]
    print(f"\nWayback: requesting {len(wb_todo)} snapshots "
          f"({sum(1 for r in relevant if not r.get('wayback_url'))} relevant articles lack one)")

    saved = 0
    for i, rec in enumerate(wb_todo, 1):
        rec["wayback_attempts"] = rec.get("wayback_attempts", 0) + 1
        rec["wayback_last_attempt_at"] = now_iso()
        try:
            real = resolve(rec)
        except Exception:  # noqa: BLE001
            real = None
        if is_index_url(real):   # don't spend a Wayback request on an index page
            rec["relevant"], rec["tier"], rec["archive_status"] = False, "index", "index_page"
            rec["wayback_attempts"] -= 1
            print(f"[{i:>2}/{len(wb_todo)}] -- {rec['country']:<3} index page, skipped  {rec['title'][:40]}")
            continue
        snap = wayback_save(real) if real else None
        if snap:
            rec["wayback_url"] = snap
            saved += 1
        print(f"[{i:>2}/{len(wb_todo)}] {'OK ' if snap else '!! '}{rec['country']:<3} "
              f"try {rec['wayback_attempts']}/{MAX_WAYBACK_ATTEMPTS}  "
              f"{'saved' if snap else ('no URL' if not real else 'failed')}  {rec['title'][:40]}",
              flush=True)
        if i < len(wb_todo):
            time.sleep(args.delay)

    # --- 3. Hand exhausted articles to the weekly recovery pass ---------------
    queue = load_queue()
    added = enqueue_exhausted(relevant, queue)
    save_queue(queue)
    pending = sum(1 for e in queue.values() if e["status"] == "queued")
    print(f"\nRecovery queue: {added} added this run, {pending} waiting for the weekly pass "
          f"({RECOVERY_QUEUE.name})")

    # Rewrite the archive with the updated records (pilot scale -> full rewrite).
    db.replace_all(ordered)

    total_arch = sum(1 for r in ordered if r.get("text_archived"))
    total_wb = sum(1 for r in relevant if r.get("wayback_url"))
    print(f"\nText this run: {done} ok, {fail} failed  |  total text-archived: {total_arch}")
    print(f"Wayback this run: {saved} of {len(wb_todo)}  |  relevant with a snapshot: "
          f"{total_wb} of {len(relevant)}")
    print("Re-run `build_feed.py --no-crawl` to refresh outputs with text-relevance.")


if __name__ == "__main__":
    main()
