#!/usr/bin/env python3
"""Build SCSDI pilot RSS feeds from the source list.

For each outlet in sources.json we query Google News RSS with
    "<keyword>" site:<domain> when:<window>
in the outlet's own language locale, then merge everything into one
deduplicated, reverse-chronological feed the coding team can subscribe to.

Why Google News RSS: the coding notes show the team already fell back to
Google `site:` searches whenever an outlet's own search was broken, infinite-
scrolling, or undated. The below procedure aims to replicate this process. 

Stdlib for crawling/feed-building; the article archive lives in Postgres (db.py,
needs DATABASE_URL). Runs the same locally, in GitHub Actions, or serverless.

Usage:
    python build_feed.py                 # all sources, 7-day window
    python build_feed.py --days 14       # widen window
    python build_feed.py --limit 5       # first 5 sources (quick test)
    python build_feed.py --only scmp.com,rappler.com
"""
import argparse
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import db  # Postgres-backed article archive (source of truth)

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "sources.json"
OUT_DIR = HERE / "public"
ARCHIVE_DIR = HERE / "archive"
ARCHIVE_FILE = ARCHIVE_DIR / "articles.jsonl"   # historical snapshot only (archive now in Postgres)
REPORTS_DIR = HERE / "reports"                   # one JSON per crawl run

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
GNEWS = "https://news.google.com/rss/search"

# Need to revise: 
# Standardised multilingual term table. The procedure is uniform -- every site
# is queried the same way -- but each site gets the OR string in ITS language,
# because Google News gates results by locale (hl/gl): under a Japanese locale
# only the Japanese branch can match, so bundling every language into one string
# adds dead weight that (a) makes Google silently drop `site:` and (b) drags in
# noise (bare 南海 means "Nankai" in Japanese: ferry, railway, the Nankai Trough
# quake -- not the sea; Japanese must use 南シナ海). Short per-language queries
# keep `site:` honoured and precision high.

LANG_TERMS = {
    "en":    ["South China Sea", "West Philippine Sea"],
    "en-PH": ["South China Sea", "West Philippine Sea",
              "Dagat Kanlurang Pilipinas", "Dagat Timog Tsina"],
    "id":    ["Laut China Selatan", "Laut Cina Selatan", "Laut Natuna Utara"],
    "ja":    ["南シナ海"],
    "zh":    ["南海", "南中國海", "南中国海"],   # trad + simplified "South China Sea"
    "vi":    ["Biển Đông"],
}


def term_key(hl: str, country: str) -> str:
    if hl.startswith("zh"):
        return "zh"
    if hl in ("ja", "id", "vi"):
        return hl
    return "en-PH" if country == "PH" else "en"


def query_for(hl: str, country: str) -> str:
    terms = LANG_TERMS[term_key(hl, country)]
    return " OR ".join(f'"{t}"' for t in terms)


def gnews_url(domain: str, days: int, hl: str, gl: str, ceid: str, country: str) -> str:
    q = f"{query_for(hl, country)} site:{domain} when:{days}d"
    params = urllib.parse.urlencode({"q": q, "hl": hl, "gl": gl, "ceid": ceid})
    return f"{GNEWS}?{params}"


def fetch(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def parse_items(xml_bytes: bytes, source):
    """Yield normalised items from a Google News RSS payload."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return
    for it in root.iter("item"):
        def t(tag):
            el = it.find(tag)
            return el.text.strip() if el is not None and el.text else ""
        title = t("title")
        link = t("link")
        if not title or not link:
            continue
        pub = t("pubDate")
        # Google appends " - Outlet" to titles; keep as-is, it names the outlet.
        src_el = it.find("source")
        outlet = src_el.text.strip() if src_el is not None and src_el.text else source["name"]
        yield {
            "title": title,
            "link": link,
            "pubDate": pub,
            "pub_dt": parse_date(pub),
            "outlet": outlet,
            "source_name": source["name"],
            "country": source["country"],
            "domain": source["domain"],
            "keyword": source["keyword"],
        }


def parse_date(s: str):
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def norm_key(item):
    """Dedup key: normalised title (strip trailing ' - Outlet')."""
    t = re.sub(r"\s+-\s+[^-]+$", "", item["title"]).lower()
    return re.sub(r"\W+", " ", t).strip()


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


# ---- Relevance (rule-based v1) -------------------------------------------
# Google News does not strictly enforce the exact phrase, so broad outlets
# (e.g. Military Times) return off-topic items. An item is "relevant" if its
# title contains a South China Sea sea-name, a named feature/shoal, or a
# maritime-actor term, in any of the tracked languages. 

REL_SEA = [  # the sea itself (strong signal)
    "south china sea", "west philippine sea",
    "wps",   # WPS = West Philippine Sea; common PH headline abbreviation
    "philippine sea",   # the Philippine Sea (Pacific, E of PH) -- adjacent body of
                        # water, included by team decision (distinct from the SCS)
    "dagat kanlurang pilipinas",
    "dagat timog tsina", "laut china selatan", "laut cina selatan",
    "laut natuna utara", "biển đông", "bien dong",
    "南シナ海", "南海", "南中國海", "南中国海",
]
REL_FEATURE = [  # named features / shoals / islands
    "scarborough", "panatag", "bajo de masinloc", "huangyan", "黄岩", "黃岩",
    "spratly", "nansha", "南沙", "kalayaan", "paracel", "xisha", "西沙",
    "hoàng sa", "hoang sa", "trường sa", "truong sa",
    "second thomas", "ayungin", "ren'ai", "renai", "仁爱", "仁愛",
    "sabina", "escoda", "xianbin", "仙宾", "仙賓",
    "thitu", "pag-asa", "pagasa", "zhongye", "中業", "中业",
    "whitsun", "julian felipe", "niu'e", "牛轭",
    "reed bank", "recto bank", "natuna", "mischief reef", "panganiban",
    "美济", "美濟", "subi reef", "渚碧", "half moon shoal",
    # NOTE: dropped bare "zamora"/"meiji"/"subi" -- ambiguous with a common
    # Filipino surname, the Japanese era, and ordinary words.
]
REL_ACTOR = [  # maritime actors / incident vocabulary
    "coast guard", "china coast guard", "ccg", "maritime militia",
    "water cannon", "海警", "海上民兵", "cảnh sát biển", "canh sat bien",
    "dân quân biển", "penjaga pantai", "bakamla", "grey-zone", "gray-zone",
]


# Google News mixes non-article index pages into results: author profiles
# (titles beginning with "#"), keyword/tag pages and reporter article lists.
# These are never codeable events, so they are excluded before relevance.
NONARTICLE_MARKERS = ["關鍵字新聞", "关键字新闻", "記者文章列表", "记者文章列表"]


def is_nonarticle(title: str) -> bool:
    t = (title or "").strip()
    return t.startswith("#") or any(m in t for m in NONARTICLE_MARKERS)


def relevance(title: str):
    """Return (is_relevant, tier) for an article title."""
    if is_nonarticle(title):
        return False, "none"
    low = (title or "").lower()
    def hit(terms):
        # All term lists are lowercase; Unicode .lower() covers Vietnamese
        # diacritics and is a no-op for CJK, so one case-folded check works.
        return any(term in low for term in terms)
    if hit(REL_SEA):
        return True, "sea"
    if hit(REL_FEATURE):
        return True, "feature"
    if hit(REL_ACTOR):
        return True, "actor"
    return False, "none"


def load_archive():
    """Return (records_by_key, ordered_list) from the Postgres archive."""
    return db.load_archive()


def build_rss(items, title, desc, self_url=""):
    now = dt.datetime.now(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        f"<title>{esc(title)}</title>",
        "<link>https://github.com/</link>",
        f"<description>{esc(desc)}</description>",
        f"<lastBuildDate>{now}</lastBuildDate>",
        "<language>en</language>",
    ]
    if self_url:
        parts.append(f'<atom:link href="{esc(self_url)}" rel="self" type="application/rss+xml"/>')
    for it in items:
        cat = f'{it["country"]} / {esc(it["source_name"])}'
        tier = it.get("tier", "")
        desc_txt = f'[{it["country"]} · {esc(it["source_name"])}] match: {esc(tier)}'
        parts += [
            "<item>",
            f"<title>{esc(it['title'])}</title>",
            f"<link>{esc(it['link'])}</link>",
            f"<guid isPermaLink=\"false\">{esc(it.get('key') or norm_key(it))}</guid>",
            f"<category>{cat}</category>",
            f"<description>{desc_txt}</description>",
        ]
        if it.get("pubDate"):
            parts.append(f"<pubDate>{esc(it['pubDate'])}</pubDate>")
        parts.append("</item>")
    parts += ["</channel>", "</rss>"]
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7,
                    help="Google fetch depth (when:Nd); kept at 7 to respect the "
                         "~100-article/query cap (see codebook)")
    ap.add_argument("--window-days", type=int, default=14,
                    help="dashboard/feed display window in days, built from the "
                         "accumulated archive; may exceed --days")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    ap.add_argument("--no-crawl", action="store_true",
                    help="skip fetching; re-tag relevance on the archive and rebuild outputs")
    ap.add_argument("--dedup", action="store_true",
                    help="re-enable deduplication (default: OFF, per codebook Stage 3 -- "
                         "every fetched item is recorded so duplicates stay visible)")
    args = ap.parse_args()

    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    if args.only:
        want = {d.strip() for d in args.only.split(",")}
        sources = [s for s in sources if s["domain"] in want]
    if args.limit:
        sources = sources[: args.limit]

    for d in (OUT_DIR, ARCHIVE_DIR, REPORTS_DIR):
        d.mkdir(exist_ok=True)
    db.init_schema()

    # Persistent state: `ordered` is the full append-only list (keeps duplicates);
    # `by_key` collapses by normalized title. Dedup is OFF by default (codebook
    # Stage 3), so we record every fetched item and build outputs from the full
    # list; --dedup restores title-keyed skipping.
    by_key, ordered = load_archive()
    seen = set(by_key) if args.dedup else set()
    now = dt.datetime.now(dt.timezone.utc)
    now_iso = now.isoformat()
    now_naive = now.replace(tzinfo=None)

    new_records, per_source = [], []

    if args.no_crawl:
        # Maintenance mode: recompute relevance for the whole archive (e.g. after
        # the filter changed) and rewrite it; don't fetch or touch status/reports.
        sources = []
        for rec in ordered:
            rec["relevant"], rec["tier"] = relevance(rec["title"])
        db.replace_all(ordered)
        print(f"Re-tagged {len(ordered)} archived records.")

    for i, s in enumerate(sources, 1):
        url = gnews_url(s["domain"], args.days, s["hl"], s["gl"], s["ceid"], s["country"])
        try:
            raw = fetch(url)
            items = list(parse_items(raw, s))
            status = "ok"
        except Exception as e:  # noqa: BLE001
            items, status = [], f"ERROR: {e}"
        n_new = n_new_rel = 0
        for it in items:
            k = norm_key(it)
            if args.dedup and k in seen:   # seen in a prior run/source -> skip
                continue
            if args.dedup:
                seen.add(k)
            rel, tier = relevance(it["title"])
            rec = {
                "key": k, "title": it["title"], "link": it["link"],
                "pubDate": it["pubDate"], "outlet": it["outlet"],
                "source_name": it["source_name"], "country": it["country"],
                "domain": it["domain"], "relevant": rel, "tier": tier,
                "first_seen": now_iso, "text_archived": False,
            }
            new_records.append(rec)
            if args.dedup:
                by_key[k] = rec
            n_new += 1
            n_new_rel += int(rel)
        per_source.append({
            "domain": s["domain"], "country": s["country"], "hl": s["hl"],
            "fetched": len(items), "new": n_new, "new_relevant": n_new_rel,
            "status": status,
        })
        print(f"[{i:>2}/{len(sources)}] {s['domain']:<28} {s['hl']:<6} "
              f"fetched={len(items):<3} new={n_new:<3} new_rel={n_new_rel:<3} {status}",
              flush=True)
        if i < len(sources):
            time.sleep(args.delay)

    # Append new records to the permanent archive (Postgres).
    if new_records:
        db.insert_many(new_records)

    # Rolling outputs are built from the FULL archive within the time window.
    cutoff = now_naive - dt.timedelta(days=args.window_days)

    def rec_dt(rec):
        d = parse_date(rec.get("pubDate") or "")
        if d is None:
            try:
                d = dt.datetime.fromisoformat(rec["first_seen"])
            except (ValueError, KeyError):
                return dt.datetime.min
        return d.astimezone(dt.timezone.utc).replace(tzinfo=None) if d.tzinfo else d

    # Build outputs from the full record list so duplicates stay visible when dedup
    # is off; with --dedup, by_key holds one record per normalized title.
    all_records = list(by_key.values()) if args.dedup else (ordered + new_records)
    archive_total = len(all_records)
    window = sorted((r for r in all_records if rec_dt(r) >= cutoff),
                    key=rec_dt, reverse=True)
    relevant = [r for r in window if r["relevant"]]

    # feed.xml: the high-signal coder feed -> relevant items only.
    (OUT_DIR / "feed.xml").write_text(build_rss(
        relevant,
        title="SCSDI — South China Sea news watch (pilot)",
        desc=f"Relevance-filtered SCS articles from {len(sources)} tracked outlets, "
             f"last {args.window_days} days. Auto-generated.",
    ), encoding="utf-8")

    # feed.json / feed-data.js: ALL windowed items with a relevance flag, so the
    # dashboard can show everything and let a coder audit the filter.
    payload = {
        "generated": now_iso,
        "window_days": args.window_days,
        "n_sources": len(sources),
        "n_items": len(window),
        "n_relevant": len(relevant),
        "new_this_run": len(new_records),
        "archive_total": archive_total,
        "items": window,
    }
    (OUT_DIR / "feed.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "feed-data.js").write_text(
        "window.FEED_DATA=" + json.dumps(payload, ensure_ascii=False) + ";",
        encoding="utf-8")

    # Per-run report: how many NEW, non-duplicate articles each source produced.
    report = {
        "generated": now_iso,
        "window_days": args.window_days,
        "new_this_run": len(new_records),
        "new_relevant_this_run": sum(1 for r in new_records if r["relevant"]),
        "archive_total": archive_total,
        "per_source": sorted(per_source, key=lambda r: r["new"], reverse=True),
    }
    # In --no-crawl rebuilds, keep the last real run's status/report intact.
    if not args.no_crawl:
        report_json = json.dumps(report, ensure_ascii=False, indent=2)
        (OUT_DIR / "status.json").write_text(report_json, encoding="utf-8")
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        (REPORTS_DIR / f"run-{stamp}.json").write_text(report_json, encoding="utf-8")

    ok = sum(1 for r in per_source if r["status"] == "ok")
    print(f"\nSources ok: {ok}/{len(sources)}  |  new this run: {len(new_records)} "
          f"({report['new_relevant_this_run']} relevant)  |  archive total: {archive_total}")
    print(f"Window: {len(window)} items ({len(relevant)} relevant) -> feed.xml")
    print("Wrote feed.xml, feed.json, feed-data.js"
          + ("" if args.no_crawl else ", status.json, reports/run-*.json"))


if __name__ == "__main__":
    main()
