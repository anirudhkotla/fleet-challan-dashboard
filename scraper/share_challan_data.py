"""Email data.json out via RITA's noc-service, same transport/attachment
mechanism the lithium-scraper repo's uber_scraper/uber_mail.py uses for the
Uber PRIOR/SP_DAILY reports (same NOC endpoint, same attachment1 multipart
field, confirmed working there 2026-10-27). Needs no mail credentials of its
own -- the endpoint is open, same one credential_alerts.py already posts to.

Usage: python3 share_challan_data.py [data.json] [--to=email1,email2]
"""

import json
import os
import sys
from pathlib import Path

import requests

NOC_SEND_EMAIL_URL = os.environ.get(
    "NOC_SEND_EMAIL_URL",
    "https://ritaprodapi.project-lithium.com/noc-service/send-email")
DEFAULT_RECIPIENTS = ["anirudh@tericsoft.com"]


def send(data_path, to=None):
    data_path = Path(data_path)
    data = json.loads(data_path.read_text())
    challans = data.get("challans", [])
    generated_at = data.get("generated_at", "unknown")

    to = to or DEFAULT_RECIPIENTS
    # Not "TS eChallan" -- data.json is a cumulative merge, not a fresh TS-only
    # snapshot. Scraping is TS-only going forward (see merge_results.py's
    # history and commit b7006e6), but old Delhi/Karnataka rows from before
    # that pivot are still in here and never purged by a merge, just no
    # longer refreshed. Keep the subject state-agnostic so it doesn't claim
    # more precision about the data's coverage than is actually true.
    subject = f"Fleet Challan data -- {len(challans)} challan(s), generated {generated_at}"
    body = f"""<html><body>
<p>Fleet challan data attached ({data_path.name}).</p>
<ul>
  <li>Generated at: {generated_at}</li>
  <li>Challans: {len(challans)}</li>
</ul>
</body></html>"""

    payload = json.dumps({"to_email": list(to), "cc_email": [],
                          "subject": subject, "body": body})
    parts = [("payload", (None, payload, "application/json")),
             ("html_file", ("body.html", body.encode("utf-8"), "text/html")),
             ("attachment1", (data_path.name, data_path.read_bytes(), "application/json"))]

    resp = requests.post(NOC_SEND_EMAIL_URL, files=parts, timeout=120)
    resp.raise_for_status()
    print(f"sent {data_path.name} ({len(challans)} challans) to {', '.join(to)}")
    return resp.json()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    data_path = args[0] if args else "data.json"
    to_arg = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--to=")), None)
    to = to_arg.split(",") if to_arg else None
    send(data_path, to=to)
