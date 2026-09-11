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

COOKIES = {"age_check_done": "1", "ckcy": "1"}

# First run showed the original URL 302s to accounts.dmm.co.jp/service/login
# (a real login wall, not just an age-gate) and the body is a Next.js SPA
# shell ("__next" div) with no ranking markup at all. Try a spread of
# plausible current URLs to see whether that's specific to this one stale
# URL or true of the whole doujin ranking/listing surface.
CANDIDATES = [
    "https://www.dmm.co.jp/dc/doujin/-/ranking/=/term=daily/",
    "https://www.dmm.co.jp/dc/doujin/-/ranking/",
    "https://www.dmm.co.jp/dc/doujin/-/list/=/sort=ranking/",
    "https://www.dmm.co.jp/dc/doujin/",
]

for url in CANDIDATES:
    try:
        resp = requests.get(url, headers=HEADERS, cookies=COOKIES, timeout=20, allow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        print(f"=== {url} -> ERROR {exc} ===\n")
        continue
    html = resp.content.decode(resp.apparent_encoding or "utf-8", errors="replace")
    title_m = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    cid_count = html.count("cid=")
    detail_count = html.count("/detail/=/cid=")
    is_login_redirect = "accounts.dmm.co.jp" in resp.url
    print(
        f"=== {url}\n"
        f"  -> status={resp.status_code} final_url={resp.url} len={len(html)}\n"
        f"  -> history={[(r.status_code, r.url) for r in resp.history]}\n"
        f"  -> title={title_m.group(1).strip() if title_m else None!r}\n"
        f"  -> redirected_to_login={is_login_redirect} cid_count={cid_count} detail_cid_count={detail_count}\n"
    )
    if cid_count and not is_login_redirect:
        matches = list(re.finditer(re.escape("cid="), html))
        for m in matches[:5]:
            s, e = max(0, m.start() - 120), min(len(html), m.end() + 60)
            print("   sample:", repr(html[s:e]))
    print()
