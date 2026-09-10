"""Throwaway helper: dump raw HTML around date-like substrings so we can
see what markup actually wraps entries on pages the generic extractor
handles poorly. Not part of the daily pipeline."""

import re

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

URLS = {
    "soumu": "https://www.soumu.go.jp/toukei/snews_back.html",
    "stat": "https://www.stat.go.jp/data/index.html",
}

DATE_PATTERN = re.compile(
    r"(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}|令和\d{1,2}年\d{1,2}月\d{1,2})"
)

for key, url in URLS.items():
    resp = requests.get(url, headers=HEADERS, timeout=20)
    html = resp.content.decode(resp.apparent_encoding or "utf-8", errors="replace")
    print(f"=== {key} {url} status={resp.status_code} len={len(html)} ===")
    matches = list(DATE_PATTERN.finditer(html))
    print(f"date-like matches: {len(matches)}")
    for m in matches[:10]:
        s, e = max(0, m.start() - 100), min(len(html), m.end() + 100)
        print(repr(html[s:e]))
        print("---")
    print()
