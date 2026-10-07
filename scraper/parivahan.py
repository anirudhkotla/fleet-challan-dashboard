"""Parivahan (nationwide) eChallan lookup by vehicle number.

Flow confirmed live 2026-10-06 against https://echallan.parivahan.gov.in/index/accused-challan:
  1. click radio #rc_number_new (switches the form from Challan/DL to Vehicle Number mode)
  2. fill #rc_no with the plate, #captcha with the OCR'd 6-char captcha (image at #captchaimg)
  3. click #btnSearch -> POSTs to /index/search-challan
  4. response status: "common" = genuine vehicle-number lookup, Angular renders
     #my-table directly (no OTP) -- this is the only status a plate-based (not
     DL-based) search should hit. "Failed" = bad captcha or no challans; if the
     message mentions captcha, refresh (click the fa-refresh link next to
     #captchaimg) and retry. Anything else (the Aadhar-OTP branch) is a DL-number
     identity-verification path that doesn't apply to vehicle-number search --
     treated as an error rather than guessed at.
"""

import re

from captcha_solver import ALNUM, solve

BASE_URL = "https://echallan.parivahan.gov.in/index/accused-challan"
# response message on a wrong captcha is literally "CAPTCHA_NOT_MATCHED" --
# confirmed live 2026-10-06, even a visually-correct-looking OCR guess can
# still miss (distorted/noisy font), so a generous retry budget rather than
# treating one miss as a real failure.
MAX_ATTEMPTS = 8


async def check_vehicle(context, reg_no, debug_dir=None):
    page = await context.new_page()
    try:
        # gov site, slow and slower still under concurrent load (confirmed live
        # 2026-10-06: fine solo, several goto()s in parallel can exceed the 30s
        # default) -- generous timeout + one retry rather than a tight budget.
        for goto_attempt in range(2):
            try:
                await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
                break
            except Exception:
                if goto_attempt == 1:
                    raise
        # the radio itself is a hidden jQuery-mobile-style widget (off-viewport) --
        # confirmed live 2026-10-06: clicking it directly times out, its <label> is
        # the real clickable surface.
        await page.click("label[for='rc_number_new']")
        await page.fill("#rc_no", reg_no)

        for attempt in range(MAX_ATTEMPTS):
            captcha_bytes = await page.locator("#captchaimg").screenshot()
            if debug_dir:
                (debug_dir / f"parivahan_{reg_no}_{attempt}.png").write_bytes(captcha_bytes)
            text = solve(captcha_bytes, charset=ALNUM)
            await page.fill("#captcha", text)

            # gov site under concurrent load (confirmed live 2026-10-06, same
            # root cause as the goto() retry above) -- default 30s timeout
            # isn't generous enough once several vehicles run at once.
            async with page.expect_response(lambda r: "search-challan" in r.url, timeout=60000) as resp_info:
                await page.click("#btnSearch")
            resp = await resp_info.value
            data = await resp.json()

            status = data.get("status")
            if status == "common":
                rows = await _scrape_table(page)
                return {"ok": True, "challans": rows}

            if status == "Failed":
                message = str(data.get("message", "")).upper()
                if "CAPTCHA" in message:
                    # every failed attempt pops a SweetAlert2 modal that blocks the
                    # refresh-captcha link underneath it -- confirmed live 2026-10-06.
                    await page.keyboard.press("Escape")
                    await page.wait_for_selector(".swal2-container", state="hidden", timeout=5000)
                    await page.click("a[href='javascript: refreshCaptcha();']")
                    await page.wait_for_timeout(300)
                    continue
                return {"ok": True, "challans": []}  # no challans for this plate

            return {"ok": False, "error": f"unexpected status {status!r} (needs OTP/identity flow, skipped)"}

        return {"ok": False, "error": "captcha retries exhausted"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        await page.close()


async def _scrape_table(page):
    await page.wait_for_selector("#my-table tbody tr", timeout=8000)
    rows = []
    for row in await page.locator("#my-table tbody tr").all():
        cells = [c.strip() for c in await row.locator("> td").all_inner_texts()]
        if not cells or not cells[0]:
            continue
        amount = cells[3] if len(cells) > 3 else None
        if not amount or amount == "0":
            # confirmed live 2026-10-07: a handful of "common" search results
            # come back with amount "0" and the DL/RC Number column showing a
            # totally unrelated vehicle (not a reliable mismatch signal on its
            # own -- plenty of legitimate nonzero challans ALSO show a
            # different-looking RC/DL number in that column, so that's not
            # the differentiator). Zero amount, though, is never a real
            # payable challan -- skip rather than surface a ₹0 line item.
            continue
        challan_no = re.sub(r"\s+", "", cells[0])
        rows.append({
            "challan_no": challan_no,
            "date": cells[2] if len(cells) > 2 else None,
            "amount": amount,
            "status": cells[4] if len(cells) > 4 else None,
            "raw": " | ".join(cells),
        })
    return rows
