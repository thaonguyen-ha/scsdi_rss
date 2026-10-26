#!/usr/bin/env python3
"""Extract the SCSDI source list + search keywords from the coding workbook
into a clean sources.json the feed generator can consume.

Reads the "Progress" sheet of News coding 2024-2025.xlsx (the working list for
2024-2025: Source / Country / Link / key word) and normalises each row into:
    {name, country, domain, keyword, hl, gl, ceid, note}

hl/gl/ceid are the Google News locale params, chosen from the keyword's
language so each outlet is queried in the language the team actually searched.
"""
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
XLSX = ROOT / "data" / "News coding 2024-2025.xlsx"
OUT = Path(__file__).resolve().parent / "sources.json"

# Default keyword per country when the sheet leaves "key word" blank.
DEFAULT_KEYWORD = {
    "VN": "Biển Đông",
    "JP": "南シナ海",
    "ID": "Laut China Selatan",
    "TW": "南海",
}
DEFAULT_KEYWORD_EN = "South China Sea"

# Some "Link" cells point at a search/proxy host rather than the outlet; map to
# the real domain so site: queries target the outlet.
DOMAIN_FIX = {
    "cse.google.com": "viettimes.vn",
}


def locale_for(keyword: str, country: str):
    """Return (hl, gl, ceid) Google News locale for a keyword/country."""
    k = keyword or ""
    # Japanese (kana/kanji phrase)
    if re.search(r"[぀-ヿ]", k) or "南シナ海" in k:
        return "ja", "JP", "JP:ja"
    # Vietnamese (diacritics or the phrase)
    if "Biển" in k or "biển" in k.lower() or re.search(r"[đĐơơ]", k):
        return "vi", "VN", "VN:vi"
    # Indonesian
    if "Laut China Selatan" in k:
        return "id", "ID", "ID:id"
    # Chinese
    if re.search(r"[一-鿿]", k):
        if country == "TW":
            return "zh-TW", "TW", "TW:zh-Hant"
        return "zh-CN", "CN", "CN:zh-Hans"
    # Default English, geo-target by country where it helps
    gl = {"PH": "PH", "SG": "SG", "MY": "MY", "AU": "AU"}.get(country, "US")
    return "en", gl, f"{gl}:en"


def domain_of(link: str) -> str:
    if not link:
        return ""
    netloc = urlparse(link.strip()).netloc.lower()
    return re.sub(r"^www\.", "", netloc)


def main():
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    ws = wb["Progress"]
    rows = list(ws.iter_rows(values_only=True))
    header = [str(c).strip() if c else "" for c in rows[0]]
    idx = {name: i for i, name in enumerate(header)}

    def cell(row, name):
        i = idx.get(name)
        v = row[i] if i is not None and i < len(row) else None
        return str(v).strip() if v is not None else ""

    sources = []
    for row in rows[1:]:
        name = cell(row, "Source")
        link = cell(row, "Link")
        if not name or not link:
            continue
        country = cell(row, "Country")
        keyword = cell(row, "key word")
        # The "key word" column sometimes holds a prose note, not a search term.
        if not keyword or len(keyword) > 40 or "http" in keyword or "site:" in keyword:
            keyword = DEFAULT_KEYWORD.get(country, DEFAULT_KEYWORD_EN)
        domain = domain_of(link)
        domain = DOMAIN_FIX.get(domain, domain)
        hl, gl, ceid = locale_for(keyword, country)
        sources.append({
            "name": name,
            "country": country,
            "domain": domain,
            "keyword": keyword,
            "hl": hl,
            "gl": gl,
            "ceid": ceid,
            "assignment": cell(row, "Assignment"),
            "note": cell(row, "note"),
        })

    OUT.write_text(json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(sources)} sources -> {OUT}")
    # Quick sanity print
    for s in sources:
        print(f"  {s['country']:>3}  {s['domain']:<28} [{s['hl']}]  {s['keyword']}")


if __name__ == "__main__":
    main()
