"""Daily sales-ranking / genre-trend tracker for FANZA同人 and DLsite.

For each configured adult-doujin marketplace this script:
  1. Fetches that day's sales ranking page (age-gate cookie included, since
     both sites interstitial-redirect an unverified visitor away from the
     ranking listing).
  2. Extracts ranked items by their product **detail-page URL pattern**
     (e.g. DMM's "/detail/=/cid=..." , DLsite's "/work/=/product_id/...")
     rather than by CSS class names. Class names on these sites' ranking
     pages get renamed across front-end redesigns; the URL grammar for a
     product page is a much more stable target to search a
     ranking is by that pattern -- the same fallback-first philosophy
     scripts/fetch_new_info.py uses for government "what's new" listings.
  3. For each ranked item, looks for genre/tag links near it (FANZA shows
     these inline on the ranking tile; DLsite's ranking list only shows
     them on the work's own detail page, so for DLsite this makes one
     extra request per ranked item, politely rate-limited).
  4. Aggregates genre occurrence counts across that day's top N ranked
     items, saves the day's snapshot to
     data/sales_trends/<site>/latest.json, upserts it into
     data/sales_trends/<site>/history.jsonl (one record per calendar
     date), and writes a same-day trend digest under
     data/sales_trends/digest/<YYYY-MM-DD>.md comparing today's genre
     counts against the most recent previous day on record.

IMPORTANT CAVEAT: this script was written without the ability to fetch
either site from the authoring environment (both domains are blocked by
that environment's outbound network policy), so the URL grammar and genre
extraction heuristics below are best-effort, based on each site's
long-stable URL conventions rather than a live inspection of current page
HTML. The first run (e.g. via workflow_dispatch) should be checked for the
"抽出に失敗しました" warning path below; if either site's markup has
drifted enough to break the detail-URL-pattern matching, the RANKING_URL /
DETAIL_URL_MARKER / genre marker constants are the place to adjust, the
same way earlier commits in this repo iterated on gov-site "what's new"
selectors.

This only collects ranking metadata (rank, title, circle/maker, genre
tags, URL) for aggregate trend analysis -- never any paid or explicit
content itself.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "sales_trends"
TIMEOUT = 20
REQUEST_DELAY = 0.6  # seconds between per-item detail-page fetches
TOP_N = 30  # how many ranked items to analyze per site per day

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

SITES = {
    "fanza_doujin": {
        "name": "FANZA同人",
        "ranking_url": "https://www.dmm.co.jp/dc/doujin/-/ranking/=/term=daily/",
        # Bypasses the age-verification interstitial that otherwise
        # redirects an unverified visitor away from adult-floor pages.
        "cookies": {"age_check_done": "1", "ckcy": "1"},
        "detail_url_marker": "/detail/=/cid=",
        "genre_href_markers": ("article=keyword", "article=genre"),
    },
    "dlsite_maniax": {
        "name": "DLsite(成年向け)",
        "ranking_url": "https://www.dlsite.com/maniax/ranking/=/term/day/",
        "cookies": {"adultchecked": "1"},
        "detail_url_marker": "/work/=/product_id/",
        "genre_href_markers": ("/genre/",),
    },
}


@dataclass
class RankedItem:
    rank: int
    title: str
    url: str
    genres: list[str] = field(default_factory=list)


def fetch(url: str, cookies: dict[str, str] | None = None) -> requests.Response:
    resp = requests.get(url, headers=HEADERS, cookies=cookies, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp


def find_ranked_items(
    soup: BeautifulSoup, base_url: str, detail_url_marker: str, limit: int
) -> list[tuple[str, str, Tag]]:
    """Ranked (url, title, anchor_tag) triples, in on-page order, found by
    matching each anchor's href against the site's stable product
    detail-page URL grammar rather than a class name."""
    items: list[tuple[str, str, Tag]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        if detail_url_marker not in href:
            continue
        key = href.split("?")[0]
        if key in seen:
            continue
        title = a.get_text(strip=True)
        if not title:
            img = a.find("img")
            if img and img.get("alt"):
                title = img["alt"].strip()
        if not title:
            continue
        seen.add(key)
        items.append((key, title, a))
        if len(items) >= limit:
            break
    return items


def extract_genres_near(a_tag: Tag, markers: tuple[str, ...], max_depth: int = 4) -> list[str]:
    """Walks up from a ranked item's anchor looking for nearby genre/tag
    links (identified by href pattern, same rationale as
    find_ranked_items). Capped depth so this doesn't walk all the way up
    to a page-wide genre filter sidebar and attribute it to every item."""
    node = a_tag
    for _ in range(max_depth):
        node = node.parent
        if not isinstance(node, Tag):
            break
        genres: list[str] = []
        seen: set[str] = set()
        for g in node.find_all("a", href=True):
            href = g["href"]
            if not any(m in href for m in markers):
                continue
            text = g.get_text(strip=True)
            if text and text not in seen:
                seen.add(text)
                genres.append(text)
        if genres:
            return genres
    return []


def fetch_dlsite_work_genres(work_url: str, cookies: dict[str, str]) -> list[str]:
    """DLsite's ranking list doesn't show genre tags inline; fetch the
    work's own detail page and read them off its outline table (the row
    labelled "ジャンル")."""
    try:
        resp = fetch(work_url, cookies=cookies)
    except Exception as exc:  # noqa: BLE001 - one bad item shouldn't sink the whole run
        print(f"  (skipping genre lookup for {work_url}: {exc})", file=sys.stderr)
        return []
    soup = BeautifulSoup(resp.content, "lxml")
    genres: list[str] = []
    seen: set[str] = set()
    for label in soup.find_all(["th", "dt"]):
        if "ジャンル" not in label.get_text():
            continue
        sibling = label.find_next_sibling(["td", "dd"])
        if not sibling:
            continue
        for a in sibling.find_all("a"):
            text = a.get_text(strip=True)
            if text and text not in seen:
                seen.add(text)
                genres.append(text)
        if genres:
            return genres
    return genres


def collect_fanza(meta: dict) -> tuple[list[RankedItem], str]:
    url = meta["ranking_url"]
    resp = fetch(url, cookies=meta.get("cookies"))
    soup = BeautifulSoup(resp.content, "lxml")
    raw = find_ranked_items(soup, url, meta["detail_url_marker"], TOP_N)
    items = [
        RankedItem(
            rank=rank,
            title=title,
            url=item_url,
            genres=extract_genres_near(a, meta["genre_href_markers"]),
        )
        for rank, (item_url, title, a) in enumerate(raw, start=1)
    ]
    return items, url


def collect_dlsite(meta: dict) -> tuple[list[RankedItem], str]:
    url = meta["ranking_url"]
    cookies = meta.get("cookies")
    resp = fetch(url, cookies=cookies)
    soup = BeautifulSoup(resp.content, "lxml")
    raw = find_ranked_items(soup, url, meta["detail_url_marker"], TOP_N)
    items: list[RankedItem] = []
    for rank, (item_url, title, a) in enumerate(raw, start=1):
        genres = extract_genres_near(a, meta["genre_href_markers"])
        if not genres:
            genres = fetch_dlsite_work_genres(item_url, cookies)
            time.sleep(REQUEST_DELAY)
        items.append(RankedItem(rank=rank, title=title, url=item_url, genres=genres))
    return items, url


COLLECTORS = {
    "fanza_doujin": collect_fanza,
    "dlsite_maniax": collect_dlsite,
}


def aggregate_genres(items: list[RankedItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        for genre in item.genres:
            counts[genre] = counts.get(genre, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def load_history(site_key: str) -> dict[str, dict]:
    path = DATA_DIR / site_key / "history.jsonl"
    if not path.exists():
        return {}
    records: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        records[rec["date"]] = rec
    return records


def save_day(
    site_key: str,
    date: str,
    source_url: str,
    items: list[RankedItem],
    genre_counts: dict[str, int],
) -> dict[str, dict]:
    site_dir = DATA_DIR / site_key
    site_dir.mkdir(parents=True, exist_ok=True)

    latest_payload = {
        "fetched_at": datetime.now(JST).isoformat(),
        "date": date,
        "source_url": source_url,
        "genre_counts": genre_counts,
        "items": [asdict(i) for i in items],
    }
    (site_dir / "latest.json").write_text(
        json.dumps(latest_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # One record per calendar date (upsert, not append): a same-day rerun
    # reflects the ranking's current state rather than duplicating it.
    records = load_history(site_key)
    records[date] = {
        "date": date,
        "source_url": source_url,
        "item_count": len(items),
        "genre_counts": genre_counts,
    }
    history_path = site_dir / "history.jsonl"
    with history_path.open("w", encoding="utf-8") as f:
        for d in sorted(records):
            f.write(json.dumps(records[d], ensure_ascii=False) + "\n")
    return records


def previous_date_before(records: dict[str, dict], today: str) -> str | None:
    earlier = sorted(d for d in records if d < today)
    return earlier[-1] if earlier else None


def build_genre_trend_lines(
    genre_counts_today: dict[str, int], genre_counts_prev: dict[str, int] | None
) -> list[str]:
    lines = []
    for i, (genre, count) in enumerate(list(genre_counts_today.items())[:15], start=1):
        if genre_counts_prev is None:
            lines.append(f"{i}. {genre} — {count}件")
            continue
        prev_count = genre_counts_prev.get(genre)
        if prev_count is None:
            lines.append(f"{i}. {genre} — {count}件 (NEW)")
        else:
            diff = count - prev_count
            trend = f"{diff:+d}" if diff != 0 else "±0"
            lines.append(f"{i}. {genre} — {count}件 ({trend})")
    return lines


def main() -> int:
    today = datetime.now(JST).strftime("%Y-%m-%d")
    digest_lines = [f"# 売上ジャンル傾向ダイジェスト {today}(FANZA同人 / DLsite)", ""]
    exit_code = 0

    for site_key, meta in SITES.items():
        collector = COLLECTORS[site_key]
        digest_lines.append(f"## {meta['name']} ({meta['ranking_url']})")
        try:
            items, source_url = collector(meta)
        except Exception as exc:  # noqa: BLE001 - one site failing must not stop the rest
            digest_lines.append(f"- 取得エラー: {exc}")
            digest_lines.append("")
            print(f"[{site_key}] ERROR: {exc}", file=sys.stderr)
            exit_code = 1
            continue

        if not items:
            digest_lines.append("- ランキングの抽出に失敗しました(サイト構造の見直しが必要です)")
            digest_lines.append("")
            print(f"[{site_key}] WARNING: no items extracted from {source_url}")
            exit_code = 1
            continue

        genre_counts = aggregate_genres(items)
        records = save_day(site_key, today, source_url, items, genre_counts)
        prev_date = previous_date_before(records, today)
        prev_counts = records[prev_date]["genre_counts"] if prev_date else None

        if not genre_counts:
            digest_lines.append(
                f"- 上位{len(items)}件を取得しましたが、ジャンル情報を抽出できませんでした"
                "(ジャンル抽出セレクタの見直しが必要です)"
            )
        else:
            header = f"- 取得件数: {len(items)}件"
            header += f" / 前回取得日: {prev_date}" if prev_date else " (初回取得)"
            digest_lines.append(header)
            digest_lines.append("- ジャンル別出現数(上位、前回比):")
            digest_lines.extend(build_genre_trend_lines(genre_counts, prev_counts))
        digest_lines.append("")
        digest_lines.append("**売上上位(抜粋)**")
        for item in items[:10]:
            genre_part = f" [{', '.join(item.genres)}]" if item.genres else ""
            digest_lines.append(f"{item.rank}. [{item.title}]({item.url}){genre_part}")
        digest_lines.append("")
        digest_lines.append(f"- (取得元: {source_url})")
        digest_lines.append("")
        print(f"[{site_key}] {len(items)} items, {len(genre_counts)} genres from {source_url}")

    digest_dir = DATA_DIR / "digest"
    digest_dir.mkdir(parents=True, exist_ok=True)
    (digest_dir / f"{today}.md").write_text(
        "\n".join(digest_lines) + "\n", encoding="utf-8"
    )

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
