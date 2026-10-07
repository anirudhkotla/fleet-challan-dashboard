"""Scrape one shard of the fleet vehicle list, write results as JSON.

No SQLite here -- GitHub Actions runners are ephemeral/stateless, so each
shard just emits its raw findings; a separate merge step (merge_results.py)
combines every shard's output with the dashboard's existing data.json.

TS eChallan only -- confirmed live 2026-10-07 via a plain curl from an
Actions runner: echallan.parivahan.gov.in returns HTTP 000 (connect refused/
timeout) from GitHub's Azure IP ranges, while echallan.tspolice.gov.in
responds normally in ~1.4s. Parivahan actively blocks cloud/datacenter IPs;
every attempt from here would just burn ~2 minutes failing before giving up
(confirmed: the first full run sat at 0/20 shards after 47 minutes for
exactly this reason). The nationwide Parivahan portion runs from
local/scraper_local.py instead (a residential IP, where it already works)
and merges into this same data.json.

Usage: python3 run_shard.py vehicles_shard.txt out.json
  (vehicles_shard.txt: one reg_no per line)
"""

import asyncio
import json
import sys

from playwright.async_api import async_playwright

import ts_echallan

CONCURRENCY = 8  # Actions runners are dedicated (no other load competing
# for CPU/network the way a local machine running other work does), and
# with only ts_echallan left to scrape (no more wasted Parivahan timeouts)
# there's plenty of headroom above the local tool's CONCURRENCY=2.
UA = ts_echallan.UA


async def _run_one(sem, browser, module, portal_name, reg_no):
    async with sem:
        context = await browser.new_context(user_agent=UA)
        try:
            result = await module.check_vehicle(context, reg_no)
        finally:
            await context.close()
    return portal_name, reg_no, result


async def run(vehicles):
    sem = asyncio.Semaphore(CONCURRENCY)
    rows = []
    errors = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        tasks = []
        for reg_no in vehicles:
            tasks.append(_run_one(sem, browser, ts_echallan, "ts_echallan", reg_no))

        for coro in asyncio.as_completed(tasks):
            portal_name, reg_no, result = await coro
            if not result["ok"]:
                errors.append({"portal": portal_name, "reg_no": reg_no, "error": result["error"]})
                print(f"  FAIL  {portal_name:12} {reg_no}: {result['error']}")
                continue
            for challan in result["challans"]:
                if challan["challan_no"].startswith("UNPARSED:"):
                    continue
                rows.append({
                    "portal": portal_name,
                    "reg_no": reg_no,
                    "challan_no": challan["challan_no"],
                    "date": challan.get("date"),
                    "amount": challan.get("amount"),
                })

        await browser.close()

    return rows, errors


def main():
    shard_file, out_file = sys.argv[1], sys.argv[2]
    with open(shard_file) as f:
        vehicles = [line.strip().upper() for line in f if line.strip()]

    print(f"shard: {len(vehicles)} vehicles")
    rows, errors = asyncio.run(run(vehicles))
    print(f"found {len(rows)} challans, {len(errors)} scrape failures")

    with open(out_file, "w") as f:
        json.dump({"challans": rows, "errors": errors}, f)


if __name__ == "__main__":
    main()
