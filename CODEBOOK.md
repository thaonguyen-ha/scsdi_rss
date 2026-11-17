# SCSDI — South China Sea News Search & Relevance Codebook

**Purpose.** This document defines the search and inclusion strategy for the
SCSDI news watch so that it is **reproducible by a human**. A coder who follows
these steps by hand — same sources, same search terms, same inclusion rules —
should arrive at the **same set of articles** that the automated crawler
(`build_feed.py`) produces. The automated pipeline is simply a mechanization of
this codebook.

**Reproducibility contract.** The code and this codebook must stay in lockstep.
The authoritative lists live in the repository:
- Source frame → [`sources.json`](sources.json)
- Search terms per language → `LANG_TERMS` in [`build_feed.py`](build_feed.py)
- Relevance terms → [`term_dictionary.csv`](term_dictionary.csv) (tiers `sea` / `feature` / `actor`, plus `exclude` markers), loaded by `build_feed.py`

If you change one, change the other in the same commit, and bump the version at
the bottom of this file.

---

## Stage 1 — Source frame

We search a **fixed, vetted list of outlets**. This is the
same list the team used for 2024–2025 coding. Each outlet is searched in **one
language** (its primary publishing language). Canonical list: `sources.json`.

**China (CN)** — CGTN `cgtn.com`, China Daily `chinadaily.com.cn`, People's Daily `en.people.cn`, SCMP `scmp.com` — *English*
**Hong Kong (HK)** — Ta Kung Pao `takungpao.com` — *English*
**Indonesia (ID)** — Antara `antaranews.com`, BBC Indonesia `bbc.com`, CNN Indonesia `cnnindonesia.com`, Detik `detik.com`, Gatra `gatra.com`, Kumparan `kumparan.com`, Mongabay `mongabay.co.id`, Kompas `search.kompas.com`, Tempo `tempo.co` — *Bahasa*; The Jakarta Post `thejakartapost.com` — *English*
**International (INT)** — ABC News `abc.net.au`, AMTI `amti.csis.org`, Military Times `militarytimes.com`, Reuters `reuters.com`, The Defense Post `thedefensepost.com` — *English*
**Japan (JP)** — Asahi `asahi.com`, NHK `nhk.or.jp`, Nikkei `nikkei.com` — *Japanese*
**Malaysia (MY)** — Malay Mail `malaymail.com`, New Straits Times `nst.com.my`, The Star `thestar.com.my`, The Sun `thesundaily.my` — *English*
**Philippines (PH)** — Inquirer `inquirer.net`, Manila Bulletin `mb.com.ph`, Philstar `philstar.com`, PNA `pna.gov.ph`, Rappler `rappler.com` — *English*
**Singapore (SG)** — Channel News Asia `channelnewsasia.com`, Straits Times `straitstimes.com` — *English*
**Taiwan (TW)** — CNA `cna.com.tw` — *Traditional Chinese*
**Vietnam (VN)** — An Ninh Thu Do `anninhthudo.vn`, Bao Phap Luat `baophapluat.vn`, BBC Vietnam `bbc.com`, RFI VN `rfi.fr`, Soha `soha.vn`, Thanh Nien `thanhnien.vn`, Tuoi Tre `tuoitre.vn`, Vietnamnet `vietnamnet.vn`, Viettimes `viettimes.vn`, VOA Vietnam `voatiengviet.com`, VTC News `vtc.vn`, Zing News `zingnews.vn` — *Vietnamese*

---

## Stage 2 — Search procedure

We use **Google News** to generate a feed of news articles from a specific news outlet using the `site:` operator. 

### Search terms (sea-name, per language)

Query each source with the sea-name term(s) for its search language, combined
with **OR**:

| Search language | Terms (OR) |
|-----------------|------------|
| English | `"South China Sea"`, `"West Philippine Sea"` |
| English (Philippines) | the two English terms **plus** `"Dagat Kanlurang Pilipinas"`, `"Dagat Timog Tsina"` |
| Bahasa (ID/MY) | `"Laut China Selatan"`, `"Laut Cina Selatan"`, `"Laut Natuna Utara"` |
| Traditional/Simplified Chinese | `"南海"`, `"南中國海"`, `"南中国海"` |
| Japanese | `"南シナ海"` |
| Vietnamese | `"Biển Đông"` |

### Manual steps (human coder)

For each source in the frame:
1. Open **Google News** (`news.google.com`) and set the **edition/language** to
   the source's search language (e.g. Vietnamese, Traditional Chinese).
2. Search: `(<term1> OR <term2> …) site:<domain>` — e.g.
   - PH: `("South China Sea" OR "West Philippine Sea") site:rappler.com`
   - VN: `"Biển Đông" site:tuoitre.vn`
   - TW: `("南海" OR "南中國海" OR "南中国海") site:cna.com.tw`
3. Restrict to the time window (see below).
4. Collect every result (title + link + date + outlet).

### Automated equivalent

`build_feed.py` issues the Google News query
`<terms> site:<domain> when:<N>d` under the source's locale (`hl`/`gl`/`ceid` in
`sources.json`). 

### Time window

- Pilot default: **7 days** (`--days 7`).
- Operational: the crawler runs every ~30 minutes and only records articles not
  seen before, so in practice it captures everything published since the last run.

We chose a short 7-day window to make sure that we don't exceed the 100-article limits of Google News. 

(Can we use the same platform for backward coding?)

---

## Stage 3 — Deduplication
TOGGLED OFF FOR NOW. 

An article already recorded in a **previous run or from another source** is a
duplicate and is **not** re-recorded.

- Clean the headline: lower-cased, with a trailing " - Outlet" suffix removed
  and punctuation collapsed. Two results with the same key are the same article. 

We may want to consider fuzzy matching later on. 

---

## Stage 4 — Relevance (inclusion criteria)

Google News does not strictly enforce the exact phrase, so a search returns both
on-topic and off-topic items. **Mark an article as relevant if its headline
contains at least one term from the Term Dictionary below**.

Assign exactly one **tier**, using the **first** category that matches in this
order — **SEA → FEATURE → ACTOR**:

### Term Dictionary

The authoritative list is [`term_dictionary.csv`](term_dictionary.csv) (columns `term`, `tier`, `language`, `match`, `active`, `note`). The lists below are a human-readable copy; if they ever differ, the CSV wins. Edit terms in the CSV, not in code. `match = word` makes a short term (e.g. an acronym) match only as a whole word. `language` records the language the term is written in (ISO 639-1 code such as `en`, `vi`, `zh`; several joined with `|`, e.g. `id|ms`; `any` for a proper name that is written the same in every language). It is a label: every active term is still tested against every headline.

**SEA** — the body of water itself:
`South China Sea`, `West Philippine Sea` / `West PH Sea` / `WPS`, `Dagat Kanlurang Pilipinas`,
`Dagat Timog Tsina`, `Laut China Selatan`, `Laut Cina Selatan`,
`Laut Natuna Utara`, `Biển Đông` / `bien dong`, `南シナ海`, `南海`,
`南中國海`, `南中国海`, `Philippine Sea` (the Pacific sea *east* of the
archipelago — a different body of water from the SCS, included by team decision)

**FEATURE** — a named reef / shoal / island / bank:
`Scarborough` / `Panatag` / `Bajo de Masinloc` / `Huangyan` (`黄岩`/`黃岩`),
`Spratly` / `Nansha` (`南沙`) / `Kalayaan`, `Paracel` / `Xisha` (`西沙`) /
`Hoàng Sa`, `Trường Sa`, `Second Thomas` / `Ayungin` / `Ren'ai` (`仁爱`/`仁愛`),
`Sabina` / `Escoda` / `Xianbin` (`仙宾`/`仙賓`), `Thitu` / `Pag-asa` /
`Zhongye` (`中業`/`中业`), `Whitsun` / `Julian Felipe` / `Niu'e` (`牛轭`),
`Reed Bank` / `Recto Bank`, `Natuna`, `Mischief Reef` / `Panganiban` /
`Meiji` (`美济`/`美濟`), `Subi Reef` (`渚碧`), `Half Moon Shoal`, and the generic
word `island` (also matches `islands`; included by team decision, accepting some
off-topic hits such as the Canary Islands or Phuket)

**ACTOR** — a maritime actor / incident term, when no sea or feature is named:
`coast guard`, `China Coast Guard` / `CCG` (`海警`), `PCG` (Philippine Coast
Guard; whole-word match), `maritime militia`
(`海上民兵`), `water cannon`, `cảnh sát biển`, `dân quân biển`,
`penjaga pantai`, `Bakamla`, `grey-zone` / `gray-zone`

> The tier records *why* an item matched, roughly strongest→weakest signal. Note that this hierarchy does not reflect how relevant an article might be. It's just an order-based matching algorithm. 

### Exclusions (not articles)

Exclude **index pages** (tag, topic, author, section-archive and front pages)
even if they match a term. They are not articles, and their extracted "text" is
a list of other headlines, so they also cause text and Wayback archiving
failures (`no_text`, `wayback_no_text`) or archive junk text. Excluded pages get
`relevant = false`, `tier = index`. Two checks, both in
[`term_dictionary.csv`](term_dictionary.csv):

1. **Headline markers** (`tier = exclude`), e.g.
   - CNA (Taiwan): `# 南海 - 中央社` (hash + space), `關鍵字新聞`, `記者文章列表`, `新聞專題`
   - Manila Bulletin `Tag: …`; Philstar `+ Follow … Tag`; Rappler `Latest News`
   - WordPress archives: `… Archives - …`, `… Archives | Page N` (Inquirer, Cebu Daily News, Military Times, Interaksyon)
   - SCMP `…: Latest News and Updates`; CNA (Singapore) `… latest news & coverage`
   - Indonesian: `Kumpulan Berita …` (ANTARA), `Berita dan Informasi …` (detik), `Berita Terbaru Tentang …` (Tempo), `Berita Harian …` (CNN Indonesia)
   - Vietnamese: `Từ khóa: …`, `Tin tức, sự kiện liên quan đến …` (Tuổi Trẻ), `Tin tức 24h` / `Tin nhanh 24h` / `Tổng hợp tin tức …` (VietNamNet)
2. **Resolved-URL markers** (`tier = exclude_url`): once the Google News link is
   decoded, a publisher URL path containing `/tag/`, `/tags/`, `/topic/`,
   `/topics/`, `/author/`, `/tu-khoa/`, `/keyword/` or `/page/` marks an index page
   whose headline gives no sign (e.g. `Jairo Bolledo - Rappler`). The archiver
   checks this before fetching text or requesting a Wayback snapshot.

A headline that merely *starts* with a hashtag (`#SHOWBIZ: …`, `#AnimatED: …`) is
an article, not an index page (the old "begins with `#`" rule excluded these).

---

## Stage 5 — Archiving Rules

For posterity and reproducibility, every recorded article is one row in the Postgres (Neon) `articles` table, the
permanent record and the deduplication state (see `db.py`). The full record is
kept as JSON. 

Note that the file [`archive/articles.jsonl`](archive/) is a frozen snapshot from
before the move to Postgres (Nov 6, 2026) and is no longer updated.

| Field | Meaning |
|-------|---------|
| `key` | normalized-headline dedup key |
| `title`, `link` | headline and Google News link |
| `pubDate`, `first_seen` | published date; when we first recorded it |
| `outlet`, `source_name`, `country`, `domain` | provenance |
| `relevant`, `tier` | headline inclusion flag and matched tier |
| `text_archived` | whether the body text has been archived |
| `resolved_url` | the real article URL (Google News redirect decoded) |
| `wayback_url` | Internet Archive snapshot (citable, link-rot-proof) |
| `text_file` | path to the extracted body text under `archive/text/` |
| `text_relevant` | **body-level** relevance: names a place **and** an incident/actor term |
| `text_source` | where the body text came from: `publisher`, or `wayback` when the publisher blocked us and an exact Wayback copy existed |
| `archive_status` | result of the last text fetch: `ok`, `ok_wayback`, `no_text`, `wayback_no_text`, `http_<code>` (e.g. `http_403`), `timeout`, `url_error`, `resolve_failed`, `requeued` |
| `archive_attempts`, `last_attempt_at` | text-fetch tries so far (max 3, at most one per ~20 h) and when the last one ran |
| `recovery_status`, `recovery_attempts`, `last_recovery_at` | weekly recovery pass (see below): `queued`, `recovered` or `given_up`; weekly passes so far (max 3) |
| `publisher_status` | the publisher's error when the text came from Wayback instead (e.g. `http_403`) |
| `wayback_attempts`, `wayback_last_attempt_at` | Wayback snapshot requests so far (max 3, one per ~20 h). Requested for **every** relevant article, even ones we cannot fetch ourselves |

### Weekly archive recovery

The 6-hourly job (`archive_text.py`) tries each relevant article **3 times** for
body text and 3 times for a Wayback snapshot. Anything still missing is written
to [`archive/recovery_queue.json`](archive/recovery_queue.json). Once a week
(Sundays 03:30 UTC) the `recover-archive` workflow runs `recover_archive.py` on
that queue, choosing a strategy from how the article failed:

| Failure | Strategy |
|---|---|
| `resolve_failed` | retry decoding the Google News redirect first |
| `http_401` / `403` / `451` (publisher blocks scripts) | skip the publisher; use an existing Wayback capture, else request a new one (the Wayback Machine fetches from its own servers) |
| `http_429` / `5xx`, `timeout`, `url_error` (traffic, outages) | one slower publisher retry, then the Wayback route |
| `no_text` (page loaded, extraction too short) | re-extract in recall mode, then the Wayback route |
| `wayback_no_text` (capture is a JavaScript shell) | request a fresh capture and extract from it |
| `http_404` / `410` (page gone) | look up an existing capture only |

Each article gets up to 3 weekly passes, then is marked `recovered` or
`given_up`. Given-up articles stay in the queue file with their full history.
Each pass writes a `reports/recovery-<timestamp>.json` summary.

### Body-level relevance (stage 4b)

PENDING. 

Once an article's text is archived, a second rule-based check marks it
`text_relevant` only if the body contains **both** a place term (SEA or FEATURE)
**and** an incident/actor term (coast guard, militia, water cannon, ram, blockade,
patrol, exercise, dispute, sovereignty, …). 

---

## Known limitations & planned refinements

1. **Relevance is headline-only (v1).** It cannot distinguish a *weather* report
   that mentions `Biển Đông` ("East Sea") from an *incident* in the same waters,
   and an article whose headline names neither the sea, a feature, nor an actor
   is missed. 
2. **Outlets Google News indexes poorly** return nothing via this method
   (currently: Ta Kung Pao, Kompas search subdomain, Gatra, BBC `bbc.com`,
   Mongabay, AMTI, The Defense Post, NHK, The Sun, VTC). These need a native feed
   or a per-site scraper.
3. **Wayback Machine limitation**. 
---

**Version:** 1.3 — 2026-11-17. Index-page exclusion widened: headline markers
for 11 outlet templates, resolved-URL markers, `#` rule narrowed to hash + space.
On the archive this removes 18 index pages from the 405 relevant headlines and
restores 27 real articles the old `#` rule excluded. Weekly archive recovery added.

**Version:** 1.2 — 2026-11-17. Added `west ph sea` (sea), `island` (feature,
generic) and `pcg` (actor, whole-word) after reviewing RA-flagged misses. Frozen
1.0/1.1 term list kept as `dictionaries/baseline_v1.0_pre-2026-11-13.csv`.

**Version:** 1.1 — 2026-11-13. Relevance terms moved from `REL_*` lists in
`build_feed.py` to `term_dictionary.csv`; term content unchanged from 1.0
(verified identical flags on all archived headlines). Matches `sources.json`
and `LANG_TERMS` in `build_feed.py` as of this date.
