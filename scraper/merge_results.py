"""Merge every shard's scraped JSON into the dashboard's data.json.

Union, never delete -- a vehicle that failed to scrape this run (captcha
exhaustion, timeout) shouldn't make its previously-found challans vanish
from the dashboard. `paid` and `first_seen` carry forward for challans
already known; new ones default unpaid.

Usage: python3 merge_results.py existing_data.json shard1.json shard2.json ... -> prints merged JSON to stdout
"""

import json
import sys
from datetime import datetime, timezone


def main():
    existing_path = sys.argv[1]
    shard_paths = sys.argv[2:]

    known = {}
    try:
        with open(existing_path) as f:
            existing = json.load(f)
        for c in existing.get("challans", []):
            known[(c["portal"], c["challan_no"])] = c
    except FileNotFoundError:
        pass

    now = datetime.now(timezone.utc).isoformat()
    new_count = 0
    for shard_path in shard_paths:
        with open(shard_path) as f:
            shard = json.load(f)
        for c in shard["challans"]:
            key = (c["portal"], c["challan_no"])
            if key in known:
                known[key]["date"] = c["date"]
                known[key]["amount"] = c["amount"]
            else:
                known[key] = {
                    "portal": c["portal"], "reg_no": c["reg_no"], "challan_no": c["challan_no"],
                    "date": c["date"], "amount": c["amount"], "paid": False, "first_seen": now,
                }
                new_count += 1

    merged = sorted(known.values(), key=lambda c: (c["reg_no"], c["date"] or ""))
    print(json.dumps({"generated_at": now, "challans": merged}, indent=2))
    print(f"merged: {len(merged)} total, {new_count} new", file=sys.stderr)


if __name__ == "__main__":
    main()
