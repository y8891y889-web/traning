"""Throwaway helper: inspect why the FANZA doujin ranking page yields zero
ranked items -- dump status/redirect info, whether the age-gate cookie
worked, and where (if anywhere) product detail links actually show up in
the fetched HTML. Not part of the daily pipeline."""

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

URL = "https://www.dmm.co.jp/dc/doujin/-/ranking/=/term=daily/"
COOKIES = {"age_check_done": "1", "ckcy": "1"}

resp = requests.get(URL, headers=HEADERS, cookies=COOKIES, timeout=20, allow_redirects=True)
html = resp.content.decode(resp.apparent_encoding or "utf-8", errors="replace")
print(f"status={resp.status_code} final_url={resp.url} len={len(html)}")
print(f"history={[(r.status_code, r.url) for r in resp.history]}")

title_m = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
print(f"title={title_m.group(1).strip() if title_m else None!r}")

for marker in ["/detail/=/cid=", "cid=", "age_check", "age-check", "年齢確認", "18歳", "doujin"]:
    count = html.count(marker)
    print(f"marker {marker!r}: {count} occurrences")

# Show context around the first few "cid=" occurrences, wherever they are.
matches = list(re.finditer(re.escape("cid="), html))
print(f"\ntotal cid= matches: {len(matches)}")
for m in matches[:8]:
    s, e = max(0, m.start() - 120), min(len(html), m.end() + 60)
    print(repr(html[s:e]))
    print("---")

# Also show the first 2000 chars of <body> so we can see what's actually there.
body_m = re.search(r"<body[^>]*>(.*)", html, re.IGNORECASE | re.DOTALL)
print("\n=== body head (2000 chars) ===")
print((body_m.group(1) if body_m else html)[:2000])
