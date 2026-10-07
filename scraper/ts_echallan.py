"""Telangana Police e-Challan lookup by vehicle number (echallan.tspolice.gov.in).

Static curl probing hit a CSRF/session wall (crypt.js-encrypted AJAX payload,
302 without a browser-established session) -- Playwright sidesteps that by
running the site's own JS for real, same as for Parivahan.

Confirmed live 2026-10-06:
  - UA matters: the default Playwright UA gets a 403 from PendingChallans.do;
    a normal desktop Chrome UA works.
  - Captcha (#captchaDivtab1) is noisy digit/math-style, answer in #captchatab1
    (maxlength 2) -- Gemini is told to compute it if it's an expression.
  - Submit (#tab1btn) POSTs to PendingChallans.do; body is the literal string
    "Invalid Captcha" on a wrong answer (with the OLD sweetalert widget
    -- class "sweet-alert", not swal2 -- blocking the refresh-captcha link
    underneath until its .confirm button is clicked), or an HTML fragment
    with a #rtable table of pending challans on success (columns: Sno, (two
    hidden checkboxes), Unit Name, Echallan No, Date, Time, Place of
    Violation, PS Limits, Violation+Fine nested table, Fine Amount, User
    Charges, Total Fine, Image).
  - If the submit never fires an XHR at all, it's because #captchatab1 was
    left empty (client-side validation silently blocks the click) -- happens
    when OCR returns nothing; skip straight to a captcha refresh instead of
    clicking in that case.
"""

from captcha_solver import DIGITS, solve

BASE_URL = "https://echallan.tspolice.gov.in/publicview/"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
MAX_ATTEMPTS = 8


async def check_vehicle(context, reg_no, debug_dir=None):
    page = await context.new_page()
    try:
        # gov site, slow and slower still under concurrent load (same issue as
        # parivahan.py, confirmed live 2026-10-06) -- generous timeout + one retry.
        for goto_attempt in range(2):
            try:
                await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
                break
            except Exception:
                if goto_attempt == 1:
                    raise
        await page.wait_for_selector("#REG_NO", timeout=30000)
        await page.fill("#REG_NO", reg_no)

        for attempt in range(MAX_ATTEMPTS):
            captcha_bytes = await page.locator("#captchaDivtab1").screenshot()
            if debug_dir:
                (debug_dir / f"ts_{reg_no}_{attempt}.png").write_bytes(captcha_bytes)
            text = solve(captcha_bytes, charset=DIGITS)

            if not text:
                await _refresh_captcha(page)
                continue

            await page.fill("#captchatab1", text)
            # gov site under concurrent load (same root cause as the goto()
            # retry above, confirmed live 2026-10-06) -- default 30s isn't
            # generous enough once several vehicles run at once.
            async with page.expect_response(
                lambda r: "PendingChallans.do" in r.url and r.request.method == "POST", timeout=60000
            ) as resp_info:
                await page.click("#tab1btn")
            resp = await resp_info.value
            body = await resp.text()

            if "invalid captcha" in body.lower():
                await _refresh_captcha(page)
                continue

            return {"ok": True, "challans": await _parse_result(context, body)}

        return {"ok": False, "error": "captcha retries exhausted"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        await page.close()


async def _refresh_captcha(page):
    # old-style sweetalert ("sweet-alert", not swal2) blocks the refresh link
    # underneath it until dismissed -- confirmed live 2026-10-06. Its
    # count()>0-then-click() has a race (element can still be animating in,
    # or already gone) so this just best-effort-tries and moves on; the
    # force=True click below is the real fallback.
    try:
        await page.locator(".sweet-alert .confirm").click(timeout=3000)
    except Exception:
        pass
    await page.wait_for_timeout(300)
    await page.click("a[href=\"javascript:refreshCaptcha('captchaDivtab1')\"]", force=True)
    await page.wait_for_timeout(400)


async def _parse_result(context, html_fragment):
    frag_page = await context.new_page()
    try:
        await frag_page.set_content(html_fragment)
        if not await frag_page.locator("#rtable").count():
            text = (await frag_page.inner_text("body")).strip()
            # confirmed live 2026-10-06: this is the literal response body for
            # a plate with zero pending challans.
            if not text or text == "NoPendingChallans":
                return []
            return [{"challan_no": f"UNPARSED:{hash(text) & 0xffffffff:x}", "raw": text}]

        rows = []
        for tr in await frag_page.locator("#rtable tr").all():
            # "> td" (direct children only): the Violation cell holds its own
            # nested <table><td>, and a plain "td" selector matches THOSE too
            # (descendant, not just child), silently shifting every index
            # after it -- confirmed live 2026-10-06 (amount was coming out as
            # the inline Fine Amt from the nested table, not the real Total
            # Fine column).
            cells = await tr.locator("> td").all_inner_texts()
            cells = [c.strip() for c in cells]
            if len(cells) < 12 or not cells[0].isdigit():
                continue  # header/spacer rows
            rows.append({
                "challan_no": cells[3],
                "date": cells[4],
                "amount": cells[11],  # Total Fine (Fine Amount + User Charges)
                "status": None,
                "raw": " | ".join(cells[2:12]),
            })
        return rows
    finally:
        await frag_page.close()
