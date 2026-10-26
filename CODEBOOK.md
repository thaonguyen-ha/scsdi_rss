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
- Relevance terms → `REL_SEA` / `REL_FEATURE` / `REL_ACTOR` in `build_feed.py`

If you change one, change the other in the same commit, and bump the version at
the bottom of this file.

---

## Stage 1 — Source frame

We search a **fixed, vetted list of outlets** (not the open web). This is the
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

We do **not** use each outlet's on-site search (they are inconsistent: infinite
scroll, no date filter, bot-blocking). Instead we use **Google News**, scoped to
one outlet at a time with the `site:` operator, in that outlet's language. This
is the same `site:` fallback the original coders used when a site's own search
failed.

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

> **Why per-language, not one combined string:** Google News returns results in
> the locale's language only, so terms in other languages never fire and only
> risk breaking the `site:` filter. Also, bare `南海` means "Nankai" in Japanese
> (a railway/region/earthquake), so Japanese uses the disambiguated `南シナ海`.

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

`build_feed.py` issues the Google **News RSS** query
`<terms> site:<domain> when:<N>d` under the source's locale (`hl`/`gl`/`ceid` in
`sources.json`). The RSS and the manual Google News search return the same frame.

### Time window

- Pilot default: **14 days** (`--days 14`).
- Operational: the crawler runs every ~30 minutes and only records articles not
  seen before, so in practice it captures everything published since the last run.
- A human doing a back-fill should set an explicit date range instead.

---

## Stage 3 — Deduplication

An article already recorded in a **previous run or from another source** is a
duplicate and is **not** re-recorded.

- **Key:** the headline, lower-cased, with a trailing " - Outlet" suffix removed
  and punctuation collapsed. Two results with the same key are the same article.
- **Human equivalent:** dedupe by headline (and by resolved URL where available);
  a wire story republished by several outlets is counted once.

---

## Stage 4 — Relevance (inclusion criteria)

Google News does not strictly enforce the exact phrase, so a search returns both
on-topic and off-topic items. **Include an article if, and only if, its headline
contains at least one term from the Term Dictionary below** (case-insensitive;
the lists already contain the needed script/diacritic forms).

Assign exactly one **tier**, using the **first** category that matches in this
order — **SEA → FEATURE → ACTOR**:

### Term Dictionary

**SEA** — the body of water itself:
`South China Sea`, `West Philippine Sea`, `Dagat Kanlurang Pilipinas`,
`Dagat Timog Tsina`, `Laut China Selatan`, `Laut Cina Selatan`,
`Laut Natuna Utara`, `Biển Đông` / `bien dong`, `南シナ海`, `南海`,
`南中國海`, `南中国海`

**FEATURE** — a named reef / shoal / island / bank:
`Scarborough` / `Panatag` / `Bajo de Masinloc` / `Huangyan` (`黄岩`/`黃岩`),
`Spratly` / `Nansha` (`南沙`) / `Kalayaan`, `Paracel` / `Xisha` (`西沙`) /
`Hoàng Sa`, `Trường Sa`, `Second Thomas` / `Ayungin` / `Ren'ai` (`仁爱`/`仁愛`),
`Sabina` / `Escoda` / `Xianbin` (`仙宾`/`仙賓`), `Thitu` / `Pag-asa` /
`Zhongye` (`中業`/`中业`), `Whitsun` / `Julian Felipe` / `Niu'e` (`牛轭`),
`Reed Bank` / `Recto Bank`, `Natuna`, `Mischief Reef` / `Panganiban` /
`Meiji` (`美济`/`美濟`), `Subi Reef` (`渚碧`), `Half Moon Shoal`

**ACTOR** — a maritime actor / incident term, when no sea or feature is named:
`coast guard`, `China Coast Guard` / `CCG` (`海警`), `maritime militia`
(`海上民兵`), `water cannon`, `cảnh sát biển`, `dân quân biển`,
`penjaga pantai`, `Bakamla`, `grey-zone` / `gray-zone`

> The tier records *why* an item matched, roughly strongest→weakest signal. It
> is not a severity ranking. FEATURE items are usually the most incident-relevant;
> ACTOR items carry the highest false-positive risk (matched only on an actor
> word), so audit those first.

### Exclusions (not articles)

Exclude Google News **index pages**, even if they match a term:
- author/profile pages — headline begins with `#`
- keyword / tag pages — contain `關鍵字新聞` / `关键字新闻`
- reporter article lists — contain `記者文章列表` / `记者文章列表`

---

## Stage 5 — What we record (archive schema)

Every recorded article is one JSON line in [`archive/articles.jsonl`](archive/)
(the permanent, append-only record and the deduplication state):

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

### Body-level relevance (stage 4b)

Once an article's text is archived, a second rule-based check marks it
`text_relevant` only if the body contains **both** a place term (SEA or FEATURE)
**and** an incident/actor term (coast guard, militia, water cannon, ram, blockade,
patrol, exercise, dispute, sovereignty, …). This is what distinguishes a genuine
incident from a weather report that merely names *Biển Đông*. It is an added
signal, not yet the feed gate (see limitations), and is the step an LLM
classifier would eventually replace.

---

## Known limitations & planned refinements

1. **Relevance is headline-only (v1).** It cannot distinguish a *weather* report
   that mentions `Biển Đông` ("East Sea") from an *incident* in the same waters,
   and an article whose headline names neither the sea, a feature, nor an actor
   is missed. The planned fix is a second pass over the **archived article text**
   (rule-based on the body, or an LLM classifier) — this is the open design
   decision.
2. **Body-level relevance is an added signal, not the feed gate.** `text_relevant`
   exists only for text-archived articles, and archiving runs incrementally, so
   inclusion in `feed.xml` is still decided by the headline rule above.
3. **Outlets Google News indexes poorly** return nothing via this method
   (currently: Ta Kung Pao, Kompas search subdomain, Gatra, BBC `bbc.com`,
   Mongabay, AMTI, The Defense Post, NHK, The Sun, VTC). These need a native feed
   or a per-site scraper.
4. **Regional blocks** (e.g. BBC/RFI Vietnamese) may surface in the feed via
   Google even when a coder cannot open them directly.

---

**Version:** 1.0 — 2026-10-26. Matches `sources.json` and the `LANG_TERMS` /
`REL_*` lists in `build_feed.py` as of this date.
