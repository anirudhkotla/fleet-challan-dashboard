"""Captcha OCR. Primary path is Gemini vision (handles the portals' real
fonts/noise far better than template OCR); local tesseract is the fallback
when Gemini is unavailable (rate-limited, API down, no key) so a transient
outage on Google's side doesn't stall the whole daily run.

Gemini key: GEMINI_API_KEY, read from the environment or this directory's
.env (gitignored, never committed -- see README). Confirmed live 2026-10-06
against gemini-3.1-flash-lite: inlineData/mimeType (camelCase) is the correct
REST JSON field naming, thinkingBudget:0 skips reasoning tokens since reading
~6 characters off an image needs none.
"""

import base64
import io
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import requests
from PIL import Image

ALNUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
DIGITS = "0123456789"

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"


def _load_dotenv():
    env_path = Path(__file__).with_name(".env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


def solve(image_bytes, charset=ALNUM, max_retries=3):
    """Gemini first, tesseract on failure -- see module docstring."""
    if os.environ.get("GEMINI_API_KEY"):
        try:
            return solve_gemini(image_bytes, charset=charset, max_retries=max_retries)
        except Exception as exc:
            # confirmed live 2026-10-07: silently swallowing this (bare
            # `except Exception: pass`) hid a 100%-failure-rate Gemini bug on
            # GitHub Actions behind a misleading "tesseract not found" error
            # instead -- always surface the real cause before falling back.
            print(f"  [captcha_solver] Gemini failed, falling back to tesseract: {exc}")
    return solve_tesseract(image_bytes, charset=charset)


def solve_gemini(image_bytes, charset=ALNUM, max_retries=3, timeout=20):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set (check .env)")

    payload = {
        "contents": [{"parts": [
            {"text": (
                "This is a captcha image, possibly obscured by dot/line noise. "
                "If it shows a math expression (e.g. digits with +/-), compute the result "
                "and reply with ONLY the final numeric answer. Otherwise reply with ONLY the "
                "exact characters shown. No spaces, no punctuation, no explanation."
            )},
            {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(image_bytes).decode()}},
        ]}],
        "generationConfig": {"thinkingConfig": {"thinkingBudget": 0}, "maxOutputTokens": 20},
    }

    last_err = None
    for attempt in range(max_retries):
        try:
            resp = requests.post(GEMINI_URL, params={"key": api_key}, json=payload, timeout=timeout)
            if resp.status_code == 200:
                text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
                return re.sub(f"[^{re.escape(charset)}]", "", text.upper())
            # 503 = transient overload (confirmed live, common on Gemini) -- worth a retry.
            last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except requests.RequestException as exc:
            last_err = str(exc)
        time.sleep(2 ** attempt)
    raise RuntimeError(f"gemini captcha solve failed after {max_retries} attempts: {last_err}")


def solve_tesseract(image_bytes, charset=ALNUM, scale=3, threshold=140, timeout=15):
    img = Image.open(io.BytesIO(image_bytes)).convert("L")
    img = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
    # ponytail: fixed global threshold, not adaptive/Otsu -- upgrade if real
    # captchas turn out to have uneven background shading that this misreads.
    img = img.point(lambda p: 255 if p > threshold else 0)

    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        img.save(f.name)
        result = subprocess.run(
            ["tesseract", f.name, "stdout", "--psm", "7", "-c", f"tessedit_char_whitelist={charset}"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    return re.sub(f"[^{re.escape(charset)}]", "", result.stdout.upper())


def _sample_captcha_png():
    from PIL import ImageDraw

    img = Image.new("L", (160, 50), color=255)
    ImageDraw.Draw(img).text((20, 10), "AB12C9", fill=0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def demo():
    png = _sample_captcha_png()
    text = solve_tesseract(png, charset=ALNUM)
    assert text == "AB12C9", f"tesseract: expected AB12C9, got {text!r}"
    print("tesseract self-check ok:", text)

    if os.environ.get("GEMINI_API_KEY"):
        text = solve_gemini(png, charset=ALNUM)
        assert text == "AB12C9", f"gemini: expected AB12C9, got {text!r}"
        print("gemini self-check ok:", text)
    else:
        print("gemini self-check skipped (no GEMINI_API_KEY)")


if __name__ == "__main__":
    demo()
