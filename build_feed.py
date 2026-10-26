#!/usr/bin/env python3
"""Build SCSDI pilot RSS feeds from the source list.

For each outlet in sources.json we query Google News RSS with
    "<keyword>" site:<domain> when:<window>
in the outlet's own language locale, then merge everything into one
deduplicated, reverse-chronological feed the coding team can subscribe to.

Why Google News RSS: the coding notes show the team already fell back to
Google `site:` searches whenever an outlet's own search was broken, infinite-
scrolling, or undated. This reproduces that, in every language, as a feed.

Stdlib only -- runs the same locally, in GitHub Actions, or in a serverless fn.

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

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "sources.json"
OUT_DIR = HERE / "public"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
GNEWS = "https://news.google.com/rss/search"

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
        desc_txt = f'[{it["country"]} · {esc(it["source_name"])}] South China Sea watch'
        parts += [
            "<item>",
            f"<title>{esc(it['title'])}</title>",
            f"<link>{esc(it['link'])}</link>",
            f"<guid isPermaLink=\"false\">{esc(norm_key(it))}</guid>",
            f"<category>{cat}</category>",
            f"<description>{desc_txt}</description>",
        ]
        if it["pubDate"]:
            parts.append(f"<pubDate>{esc(it['pubDate'])}</pubDate>")
        parts.append("</item>")
    parts += ["</channel>", "</rss>"]
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    args = ap.parse_args()

    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    if args.only:
        want = {d.strip() for d in args.only.split(",")}
        sources = [s for s in sources if s["domain"] in want]
    if args.limit:
        sources = sources[: args.limit]

    OUT_DIR.mkdir(exist_ok=True)
    all_items, seen, report = [], set(), []

    for i, s in enumerate(sources, 1):
        url = gnews_url(s["domain"], args.days, s["hl"], s["gl"], s["ceid"], s["country"])
        try:
            raw = fetch(url)
            items = list(parse_items(raw, s))
            status = "ok"
        except Exception as e:  # noqa: BLE001
            items, status = [], f"ERROR: {e}"
        kept = 0
        for it in items:
            k = norm_key(it)
            if k in seen:
                continue
            seen.add(k)
            all_items.append(it)
            kept += 1
        report.append((s["domain"], s["hl"], len(items), kept, status))
        print(f"[{i:>2}/{len(sources)}] {s['domain']:<28} {s['hl']:<6} "
              f"items={len(items):<3} new={kept:<3} {status}", flush=True)
        if i < len(sources):
            time.sleep(args.delay)

    all_items.sort(key=lambda x: x["pub_dt"] or dt.datetime.min, reverse=True)

    feed = build_rss(
        all_items,
        title="SCSDI — South China Sea news watch (pilot)",
        desc=f"Keyword-matched articles from {len(sources)} tracked outlets, last {args.days} days. Auto-generated.",
    )
    (OUT_DIR / "feed.xml").write_text(feed, encoding="utf-8")

    # Machine-readable mirror for a future coding UI / dashboard.
    (OUT_DIR / "feed.json").write_text(json.dumps({
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(),
        "window_days": args.days,
        "n_sources": len(sources),
        "n_items": len(all_items),
        "items": [{k: v for k, v in it.items() if k != "pub_dt"} for it in all_items],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = sum(1 for r in report if r[4] == "ok")
    print(f"\nSources queried: {len(sources)}  ok: {ok}  "
          f"total unique items: {len(all_items)}")
    print(f"Wrote {OUT_DIR/'feed.xml'} and feed.json")
    empties = [r[0] for r in report if r[4] == "ok" and r[2] == 0]
    if empties:
        print(f"No results ({len(empties)}): {', '.join(empties)}")


if __name__ == "__main__":
    main()
