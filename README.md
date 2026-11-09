# SCSDI News Watch — pilot RSS

This is pilot news-monitoring feed for the **South China Sea Documentation Initiative**. It uses **Google News RSS** to query for relevant articles from a pre-determined set of news outlets, reorganized in a dashboard for revision. This document contains details about design decisions related to the dashboard. 

Current status: 2026-10-28.


## Output

| File | What |
|------|------|
| `public/feed.xml`   | RSS 2.0 feed — **relevance-filtered** items only; subscribe in any reader |
| `public/feed.json`  | All windowed items (with a `relevant` flag) for the dashboard |
| `public/feed-data.js`| Same JSON as `window.FEED_DATA` so the dashboard works on `file://` too |
| `public/index.html` | Browsable dashboard (relevant-only toggle, country/search/sort) |
| `public/status.json`| Latest crawl report: per-source new counts, totals |
| `archive/articles.jsonl` | **Permanent** append-only record of every article ever seen (this is the dedup state — committed so it persists across runs) |
| `archive/text/*.txt`| Extracted body text of archived relevant articles |
| `reports/run-*.json`| One per-run report kept for history |

## How it works

- **`sources.json`** — this file collects all 47 outlets with domain + Google News locale
  (`hl`/`gl`/`ceid`). This data is extracted from the `Progress` sheet of
  `News coding 2024-2025.xlsx`.
- **`build_feed.py`** — The main workhorse of Google News. For each outlet, queries
  `"<terms>" site:<domain> when:<N>d` in that outlet's language. **Standard
  library only.** Each run:
  1. loads the archive (`archive/articles.jsonl`) to know what's already been seen;
  2. crawls every source and keeps only articles **not seen in any prior run**
     (dedup by normalized title), appending them to the archive; (Note: Pending. I'm disabling deduplication for now.)
  3. tags each new article's **relevance** (see below);
  4. rebuilds the rolling `feed.xml` (relevant only) and `feed.json`/dashboard
     (everything, flagged);
  5. writes a per-run report: how many new, non-duplicate articles each source
     produced (`public/status.json` + `reports/run-*.json`).

  `python build_feed.py --no-crawl` re-tags the whole archive and rebuilds
  outputs **without** fetching — use it after changing the relevance rules.

- **`archive_text.py`** — for each relevant, not-yet-archived article it (1)
  resolves the Google News redirect to the real URL (via Google's `batchexecute`
  endpoint), (2) fetches and extracts the body with **trafilatura**, (3) runs a
  rule-based **text** relevance check (a place term *and* an incident/actor term,
  which separates an incident from a weather report that just names the sea), and
  (4) snapshots the real URL to the **Wayback Machine**. It stores the extracted
  text under `archive/text/` and writes `resolved_url`, `wayback_url`,
  `text_relevant` and hit counts back into the archive. Runs incrementally
  (`--limit`, default 12) to respect Wayback's rate limits. Needs
  `pip install -r requirements.txt`.

  **Durability:** the extracted text lives in the repo (your copy) and the
  Wayback URL is the public citation. Raw HTML isn't committed by default to
  avoid repo bloat; `--save-html` keeps gzipped originals locally. Because full
  text is stored, **keep the repo private.**

### Relevance filter (v1, rule-based)

Google News doesn't strictly enforce the exact phrase, so broad outlets return
off-topic items (e.g. Military Times car-crash stories). The dashboard tags an article is
**relevant** if its title contains, in any tracked language, a sea-name
(`sea`), a named feature/shoal (`feature`), or a maritime-actor term (`actor`).
`feed.xml` carries relevant items only; the dashboard shows everything so the
filter can be audited. See the codebook for more details. 

Each outlet is queried with the sea's name **in its own language**:

| Locale | Terms |
|--------|-------|
| English        | South China Sea · West Philippine Sea |
| English (PH)   | + Dagat Kanlurang Pilipinas · Dagat Timog Tsina |
| Bahasa (ID/MY) | Laut China Selatan · Laut Cina Selatan · Laut Natuna Utara |
| Chinese        | 南海 · 南中國海 · 南中国海 (trad + simplified) |
| Japanese       | 南シナ海 |
| Vietnamese     | Biển Đông |

Typically in Factiva, one might try to bundle every language into one boolean OR string, but this approach is not the best for Google News.
*Google News silently drops the `site:` filter once the query is long, flooding the feed
with untracked outlets. 
* In Japanese, bare `南海` means *Nankai* (ferry, railway, the Nankai Trough earthquake), not the sea. 

## Deduplication

We do not deduplicate for now. We want to make sure how duplicated news are generated before moving forward with the best approach. 

## Direct links

## Run locally

```bash
python build_feed.py                 # all sources, 7-day fetch + 7-day feed window
python build_feed.py --window-days 14  # show a longer window from the archive
python build_feed.py --only scmp.com,rappler.com   # quick test
python -m http.server -d public 8777 # then open http://127.0.0.1:8777/
```

Regenerate the source list (needs the workbook + `pip install openpyxl`):

```bash
python extract_sources.py
```

## Deployment

TBD - in progress: Vercel + GitHub CI are the main pathway right now but we should set up things with future AWS host in mind. 

- **Serving:** Vercel, static, from `public/` (see `vercel.json`). Connect this
  repo in the Vercel dashboard once; it redeploys on every push.
- **Refresh:** `.github/workflows/build-feed.yml` runs `build_feed.py` **every
  30 minutes**, then commits `public/`, `archive/`, and `reports/` — which
  triggers the Vercel redeploy. 

## Current limitations

- **Text archiving is best-effort.** Redirect resolution or extraction can fail
  (paywalls, bot-blocking, or Google changing its endpoint). 
- **Paywalled sites exist.** (To-do: make a list).
- **No results (10 outlets):** takungpao, kompas (search subdomain), gatra,
  bbc.com, mongabay.co.id, amti.csis.org, thedefensepost, nhk.or.jp,
  thesundaily, vtc.vn — poorly indexed by Google News or a domain mismatch.
  These need a native RSS feed or a small per-site scraper.
