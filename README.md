# SCSDI News Watch — pilot RSS

A pilot news-monitoring feed for the **South China Sea Documentation Initiative**.
It reproduces, automatically, what the coding team did by hand: search a fixed
list of ~47 outlets for South China Sea coverage, in each outlet's own language,
and surface new articles for coders to review.

It queries **Google News RSS** per outlet (the same `site:` + keyword fallback the
team already used when an outlet's own search was broken), merges everything into
one deduplicated, reverse-chronological **RSS 2.0** feed plus a JSON mirror, and
ships a small dashboard to browse it.

## Output

| File | What |
|------|------|
| `public/feed.xml`  | Combined RSS 2.0 feed — subscribe to this in any reader |
| `public/feed.json` | Same items as JSON (for a future triage/coding UI) |
| `public/index.html`| Browsable dashboard (filter by country, search, sort) |

## How it works

- **`sources.json`** — the 47 outlets with domain + Google News locale
  (`hl`/`gl`/`ceid`). Generated from the `Progress` sheet of
  `News coding 2024-2025.xlsx` by `extract_sources.py` (a local step; needs
  `openpyxl` and the workbook, which are **not** in this repo).
- **`build_feed.py`** — for each outlet, queries
  `"<terms>" site:<domain> when:<N>d` in that outlet's language, merges + dedupes
  by normalized title, writes the feed. **Standard library only.**

### Why a per-language term table, not one universal string

Each outlet is queried with the sea's name **in its own language**:

| Locale | Terms |
|--------|-------|
| English        | South China Sea · West Philippine Sea |
| English (PH)   | + Dagat Kanlurang Pilipinas · Dagat Timog Tsina |
| Bahasa (ID/MY) | Laut China Selatan · Laut Cina Selatan · Laut Natuna Utara |
| Chinese        | 南海 · 南中國海 · 南中国海 (trad + simplified) |
| Japanese       | 南シナ海 |
| Vietnamese     | Biển Đông |

Bundling every language into one OR string was tested and **fails**: Google News
(a) silently drops the `site:` filter once the query is long, flooding the feed
with untracked outlets, and (b) in Japanese, bare `南海` means *Nankai* (ferry,
railway, the Nankai Trough earthquake), not the sea. Locale gates which language
Google returns, so only the matching language's terms ever fire — short,
per-language queries keep `site:` honored and precision high.

## Run locally

```bash
python build_feed.py                 # all sources, 14-day window
python build_feed.py --days 7        # narrower window
python build_feed.py --only scmp.com,rappler.com   # quick test
python -m http.server -d public 8777 # then open http://127.0.0.1:8777/
```

Regenerate the source list (needs the workbook + `pip install openpyxl`):

```bash
python extract_sources.py
```

## Deployment

- **Serving:** Vercel, static, from `public/` (see `vercel.json`). Connect this
  repo in the Vercel dashboard once; it redeploys on every push.
- **Refresh:** `.github/workflows/build-feed.yml` runs `build_feed.py` daily
  (22:00 UTC) and commits the new `feed.xml`/`feed.json`, which triggers the
  Vercel redeploy. No Vercel token needed.

## Known limits (pilot)

- **Relevance:** Google News does not strictly enforce exact phrases, so broad
  outlets (e.g. Military Times) bring in off-topic items. A title/body relevance
  filter is the next improvement.
- **Links** are Google News redirect URLs; resolving them to the real article
  URL is a future enhancement.
- **No results (10 outlets):** takungpao, kompas (search subdomain), gatra,
  bbc.com, mongabay.co.id, amti.csis.org, thedefensepost, nhk.or.jp,
  thesundaily, vtc.vn — poorly indexed by Google News or a domain mismatch.
  These need a native RSS feed or a small per-site scraper.
