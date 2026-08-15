#!/usr/bin/env python3
import sys
sys.path.insert(0, r"C:\Users\pkoon\tripit_map")
import tripit_map_sync as t

text = t.fetch_tripit(t.SHARE_URL)
print("fetched:", len(text))
items = t.parse_tripit(text)
print("items:", len(items))
for it in items:
    print(f"  [{it['date']}] day={it['day']!r} time={it['time']!r} addr={str(it['address'])[:40]!r} title={it['title'][:60]!r}")
