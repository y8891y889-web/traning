"""Daily fetcher for "新着情報" (what's new) listings on Japanese government sites.

For each configured site this script:
  1. Fetches the homepage and looks for an RSS/Atom feed link in <head>.
  2. If no feed is found, looks for a navigation link whose text contains
     "新着" and follows it, then extracts date/title/link entries with a
     handful of generic HTML patterns (<li>, <dl>, <table> rows).
  3. Compares the freshly extracted entries against the previously saved
     snapshot (data/<site>/latest.json) and records anything new.
  4. Updates data/<site>/latest.json, appends new entries to
     data/<site>/history.jsonl, and writes a same-day digest under
     data/digest/<YYYY-MM-DD>.md.

Site structures are not guessed ahead of time: feed/what's-new links are
discovered from the actual page HTML at run time, so this only relies on
URLs the user supplied for the site roots.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TIMEOUT = 20
# A generic bot UA gets a 403 from some ministry WAFs (e.g. METI); a
# regular-browser-looking UA plus ja Accept-Language passes.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

SITES = {
    "cao": {"name": "内閣府", "url": "https://www.cao.go.jp/"},
    "meti": {"name": "経済産業省", "url": "https://www.meti.go.jp/"},
    "mof": {"name": "財務省", "url": "https://www.mof.go.jp/"},
}

DATE_RE = re.compile(r"(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?")


@dataclass
class Entry:
    title: str
    url: str
    date: str | None = None

    def key(self) -> str:
        return self.url


def fetch(url: str) -> requests.Response:
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp


def find_feed_url(base_url: str, soup: BeautifulSoup) -> str | None:
    for link in soup.find_all("link", rel=lambda v: v and "alternate" in v):
        type_ = (link.get("type") or "").lower()
        if "rss" in type_ or "atom" in type_:
            href = link.get("href")
            if href:
                return urljoin(base_url, href)
    return None


def find_whatsnew_url(base_url: str, soup: BeautifulSoup) -> str | None:
    candidates = soup.find_all("a", string=re.compile("新着"))
    for a in candidates:
        href = a.get("href")
        if href:
            return urljoin(base_url, href)
    return None


def extract_date(text: str) -> str | None:
    m = DATE_RE.search(text)
    if not m:
        return None
    y, mo, d = m.groups()
    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"


def parse_feed(feed_url: str, base_url: str) -> list[Entry]:
    parsed = feedparser.parse(feed_url)
    entries = []
    for item in parsed.entries:
        title = (item.get("title") or "").strip()
        link = item.get("link") or ""
        if not title or not link:
            continue
        date = None
        if item.get("published"):
            date = extract_date(item["published"])
        entries.append(Entry(title=title, url=urljoin(base_url, link), date=date))
    return entries


def parse_whatsnew_page(page_url: str, soup: BeautifulSoup) -> list[Entry]:
    entries: list[Entry] = []
    seen_urls: set[str] = set()

    # Strategy 1: <li> items containing a link
    for li in soup.find_all("li"):
        a = li.find("a", href=True)
        if not a:
            continue
        title = a.get_text(strip=True)
        if not title or len(title) < 4:
            continue
        href = urljoin(page_url, a["href"])
        if href in seen_urls:
            continue
        date = extract_date(li.get_text(" ", strip=True))
        entries.append(Entry(title=title, url=href, date=date))
        seen_urls.add(href)

    # Strategy 2: definition lists (<dt> date / <dd> link) common on gov sites
    if not entries:
        for dl in soup.find_all("dl"):
            dts = dl.find_all("dt")
            dds = dl.find_all("dd")
            for dt, dd in zip(dts, dds):
                a = dd.find("a", href=True)
                if not a:
                    continue
                title = a.get_text(strip=True)
                href = urljoin(page_url, a["href"])
                if not title or href in seen_urls:
                    continue
                date = extract_date(dt.get_text(strip=True))
                entries.append(Entry(title=title, url=href, date=date))
                seen_urls.add(href)

    # Strategy 3: table rows
    if not entries:
        for tr in soup.find_all("tr"):
            a = tr.find("a", href=True)
            if not a:
                continue
            title = a.get_text(strip=True)
            href = urljoin(page_url, a["href"])
            if not title or href in seen_urls:
                continue
            date = extract_date(tr.get_text(" ", strip=True))
            entries.append(Entry(title=title, url=href, date=date))
            seen_urls.add(href)

    return entries[:200]


def collect_site(site_key: str, site_url: str) -> tuple[list[Entry], str]:
    """Returns (entries, source_url_used)."""
    home = fetch(site_url)
    soup = BeautifulSoup(home.text, "lxml")

    feed_url = find_feed_url(site_url, soup)
    if feed_url:
        entries = parse_feed(feed_url, site_url)
        if entries:
            return entries, feed_url

    whatsnew_url = find_whatsnew_url(site_url, soup)
    if whatsnew_url:
        page = fetch(whatsnew_url)
        page_soup = BeautifulSoup(page.text, "lxml")
        entries = parse_whatsnew_page(whatsnew_url, page_soup)
        if entries:
            return entries, whatsnew_url

    # Fallback: try extracting straight off the homepage.
    entries = parse_whatsnew_page(site_url, soup)
    return entries, site_url


def load_previous(site_key: str) -> dict:
    path = DATA_DIR / site_key / "latest.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {e["url"]: e for e in data.get("entries", [])}


def save_latest(site_key: str, source_url: str, entries: list[Entry]) -> None:
    path = DATA_DIR / site_key / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(JST).isoformat(),
        "source_url": source_url,
        "entries": [asdict(e) for e in entries],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def append_history(site_key: str, new_entries: list[Entry]) -> None:
    if not new_entries:
        return
    path = DATA_DIR / site_key / "history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(JST).isoformat()
    with path.open("a", encoding="utf-8") as f:
        for e in new_entries:
            record = asdict(e)
            record["found_at"] = now
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> int:
    today = datetime.now(JST).strftime("%Y-%m-%d")
    digest_lines = [f"# 官公庁 新着情報ダイジェスト {today}", ""]
    any_new = False
    exit_code = 0

    for site_key, meta in SITES.items():
        site_name = meta["name"]
        site_url = meta["url"]
        digest_lines.append(f"## {site_name} ({site_url})")
        try:
            entries, source_url = collect_site(site_key, site_url)
        except Exception as exc:  # noqa: BLE001 - one site failing must not stop the rest
            digest_lines.append(f"- 取得エラー: {exc}")
            digest_lines.append("")
            print(f"[{site_key}] ERROR: {exc}", file=sys.stderr)
            exit_code = 1
            continue

        if not entries:
            digest_lines.append("- 新着情報の抽出に失敗しました(サイト構造の見直しが必要です)")
            digest_lines.append("")
            print(f"[{site_key}] WARNING: no entries extracted from {source_url}")
            continue

        previous = load_previous(site_key)
        new_entries = [e for e in entries if e.key() not in previous]

        save_latest(site_key, source_url, entries)
        append_history(site_key, new_entries)

        if new_entries:
            any_new = True
            for e in new_entries[:30]:
                date_part = f"{e.date} " if e.date else ""
                digest_lines.append(f"- {date_part}[{e.title}]({e.url})")
        else:
            digest_lines.append("- 新着なし")
        digest_lines.append(f"- (取得元: {source_url})")
        digest_lines.append("")
        print(f"[{site_key}] {len(new_entries)} new / {len(entries)} total from {source_url}")

    digest_dir = DATA_DIR / "digest"
    digest_dir.mkdir(parents=True, exist_ok=True)
    (digest_dir / f"{today}.md").write_text(
        "\n".join(digest_lines) + "\n", encoding="utf-8"
    )

    if not any_new:
        print("No new entries found across any site today.")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
