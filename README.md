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
| `public/feed.xml`   | RSS 2.0 feed — **relevance-filtered** items only; subscribe in any reader |
| `public/feed.json`  | All windowed items (with a `relevant` flag) for the dashboard |
| `public/feed-data.js`| Same JSON as `window.FEED_DATA` so the dashboard works on `file://` too |
| `public/index.html` | Browsable dashboard (relevant-only toggle, country/search/sort) |
| `public/status.json`| Latest crawl report: per-source new counts, totals |
| `archive/articles.jsonl` | **Permanent** append-only record of every article ever seen (this is the dedup state — committed so it persists across runs) |
| `reports/run-*.json`| One per-run report kept for history |

## How it works

- **`sources.json`** — the 47 outlets with domain + Google News locale
  (`hl`/`gl`/`ceid`). Generated from the `Progress` sheet of
  `News coding 2024-2025.xlsx` by `extract_sources.py` (a local step; needs
  `openpyxl` and the workbook, which are **not** in this repo).
- **`build_feed.py`** — for each outlet, queries
  `"<terms>" site:<domain> when:<N>d` in that outlet's language. **Standard
  library only.** Each run:
  1. loads the archive (`archive/articles.jsonl`) to know what's already been seen;
  2. crawls every source and keeps only articles **not seen in any prior run**
     (dedup by normalized title), appending them to the archive;
  3. tags each new article's **relevance** (see below);
  4. rebuilds the rolling `feed.xml` (relevant only) and `feed.json`/dashboard
     (everything, flagged);
  5. writes a per-run report: how many new, non-duplicate articles each source
     produced (`public/status.json` + `reports/run-*.json`).

  `python build_feed.py --no-crawl` re-tags the whole archive and rebuilds
  outputs **without** fetching — use it after changing the relevance rules.

### Relevance filter (v1, rule-based)

Google News doesn't strictly enforce the exact phrase, so broad outlets return
off-topic items (e.g. Military Times car-crash stories). An article is tagged
**relevant** if its title contains, in any tracked language, a sea-name
(`sea`), a named feature/shoal (`feature`), or a maritime-actor term (`actor`).
`feed.xml` carries relevant items only; the dashboard shows everything so the
filter can be audited. This is a deliberately simple, **high-precision /
lower-recall** first pass — see *Known limits*.

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
- **Refresh:** `.github/workflows/build-feed.yml` runs `build_feed.py` **every
  30 minutes**, then commits `public/`, `archive/`, and `reports/` — which
  triggers the Vercel redeploy. No Vercel token needed. The archive **must** be
  committed, or each CI run would start empty and re-flag everything as new.
  (GitHub's scheduled runs are best-effort and may be delayed or skipped under
  load, so "every 30 min" is approximate.)

## Known limits (pilot)

- **Relevance is title-only (v1).** It can't tell "typhoon in the East Sea /
  Biển Đông" from "China Coast Guard in the East Sea," and ambiguous proper
  nouns can slip through. Real relevance needs the **article text** + an
  incident classifier (rule-based on text, or an LLM). That depends on
  text archiving (below) and is the main open item.
- **No text archiving yet.** The archive stores metadata (title, link, outlet,
  date, first-seen), not the article body. Fetching full text requires resolving
  the Google News **redirect** links to the real article URL first — the next
  build step.
- **No results (10 outlets):** takungpao, kompas (search subdomain), gatra,
  bbc.com, mongabay.co.id, amti.csis.org, thedefensepost, nhk.or.jp,
  thesundaily, vtc.vn — poorly indexed by Google News or a domain mismatch.
  These need a native RSS feed or a small per-site scraper.
