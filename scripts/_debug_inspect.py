"""Throwaway helper: run the real collect_site() against sites that were
scoring poorly, so we can see what it picks without touching data/. Not
part of the daily pipeline."""

import sys

sys.path.insert(0, "scripts")
from fetch_new_info import collect_site, SITES  # noqa: E402

TARGETS = ["stat", "soumu"]

for key in TARGETS:
    meta = SITES[key]
    print(f"=== {key} ({meta['name']}) {meta['url']} ===")
    try:
        entries, source_url = collect_site(key, meta["url"])
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        continue
    dated = sum(1 for e in entries if e.date)
    print(f"source_url={source_url} total={len(entries)} dated={dated}")
    for e in entries[:15]:
        print(f"  {e.date} {e.title!r} -> {e.url}")
    print()
