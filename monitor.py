#!/usr/bin/env python3
"""Praamid ferry-spot monitor.

Polls the praamid.ee events API and sends an instant ntfy.sh phone push the moment a
car spot (`capacities.sv`) frees up on an afternoon VK sailing for the target date.

Zero dependencies — Python 3 standard library only. Everything is configured via env vars
(see the table below), so the same file runs locally and inside GitHub Actions unchanged.

The script NEVER books anything. It only watches and alerts; you grab the seat manually
via the booking page that the push deep-links to.
"""

import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

# ---- Config (env with sensible defaults) -----------------------------------
URL = os.environ.get(
    "URL",
    "https://www.praamid.ee/online/events?direction=VK&departure-date=2026-06-19",
)
BOOK_URL = os.environ.get("BOOK_URL", URL)            # tapping the push opens this
CAP_FIELD = os.environ.get("CAP_FIELD", "sv")          # capacities.sv = car spots
AFTERNOON_FROM_HOUR = int(os.environ.get("AFTERNOON_FROM_HOUR", "12"))
MIN_LEAD_MINUTES = int(os.environ.get("MIN_LEAD_MINUTES", "30"))  # ignore sailings too soon to reach
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "10"))
MAX_RUNTIME_SECONDS = int(os.environ.get("MAX_RUNTIME_SECONDS", "20400"))  # 5h40m < 6h job cap
STOP_AT = os.environ.get("STOP_AT", "2026-06-19T22:30:00+03:00")          # hard deadline
RE_ALERT_COOLDOWN = int(os.environ.get("RE_ALERT_COOLDOWN", "300"))       # re-ping every 5 min while open
HEARTBEAT_INTERVAL_SECONDS = int(os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "3600"))  # "still healthy" ping (0 = off)

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()

# Optional email backup (all three must be set to enable)
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("SMTP_PASS", "").strip()
MAIL_TO = os.environ.get("MAIL_TO", "").strip()
EMAIL_ENABLED = bool(SMTP_USER and SMTP_PASS and MAIL_TO)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}Z] {msg}", flush=True)


def parse_dt(s):
    """Parse praamid timestamps ('...+0300', optional '.000') and STOP_AT ('...+03:00')."""
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return datetime.fromisoformat(s)  # last-ditch fallback


def _safe_hour(item):
    try:
        return parse_dt(item["dtstart"]).hour
    except Exception:
        return None


def fetch():
    req = urllib.request.Request(
        URL, headers={"User-Agent": UA, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def actionable(data, now_utc):
    """Return afternoon, not-too-soon sailings that currently have a car spot free."""
    out = []
    items = data.get("items", []) if isinstance(data, dict) else []
    cutoff = now_utc + timedelta(minutes=MIN_LEAD_MINUTES)
    for it in items:
        try:
            dt = parse_dt(it["dtstart"])
        except Exception:
            continue
        if dt.hour < AFTERNOON_FROM_HOUR:      # +0300 offset == Tallinn wall hour
            continue
        if dt <= cutoff:                        # already gone / too soon to catch
            continue
        cap = (it.get("capacities") or {}).get(CAP_FIELD)
        if not isinstance(cap, (int, float)) or cap <= 0:
            continue
        out.append(
            {
                "uid": it.get("uid"),
                "time": dt.strftime("%H:%M"),
                "ship": (it.get("ship") or {}).get("code", "?"),
                "sv": cap,
            }
        )
    return out


def ntfy(title, body, priority="urgent", tags="rotating_light,ferry", click=None):
    """Send a push. Emojis come from `tags` (ntfy renders shortcodes); headers stay ASCII."""
    if not NTFY_TOPIC:
        log("NTFY_TOPIC not set — skipping push.")
        return False
    headers = {
        "Title": title,
        "Priority": priority,
        "Tags": tags,
        "User-Agent": UA,
    }
    if click:
        headers["Click"] = click
    req = urllib.request.Request(
        f"{NTFY_SERVER}/{NTFY_TOPIC}", data=body.encode("utf-8"),
        headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except Exception as e:
        log(f"ntfy push failed: {e}")
        return False


def send_email(subject, body):
    if not EMAIL_ENABLED:
        return False
    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = SMTP_USER
    msg["To"] = MAIL_TO
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as smtp:
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)
        return True
    except Exception as e:
        log(f"email failed: {e}")
        return False


def alert(s):
    title = f"Ferry spot free - 19 Jun {s['time']} ({s['ship']})"
    body = (
        f"{s['sv']} car spot(s) just opened on the {s['time']} {s['ship']} sailing.\n"
        f"Grab it: {BOOK_URL}"
    )
    push_ok = ntfy(title, body, priority="urgent", tags="rotating_light,ferry", click=BOOK_URL)
    if EMAIL_ENABLED:
        send_email(title, body)
    log(f"ALERT {s['time']} {s['ship']} sv={s['sv']} (push_ok={push_ok}, email={EMAIL_ENABLED})")


def set_output(name, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    try:
        with open(path, "a") as f:
            f.write(f"{name}={value}\n")
    except Exception as e:
        log(f"could not write GITHUB_OUTPUT: {e}")


def main():
    stop_dt = parse_dt(STOP_AT)
    log(f"monitor start | field={CAP_FIELD} hour>={AFTERNOON_FROM_HOUR} lead>={MIN_LEAD_MINUTES}m")
    log(f"poll={POLL_INTERVAL}s max_runtime={MAX_RUNTIME_SECONDS}s stop_at={STOP_AT}")
    log(f"ntfy={'set' if NTFY_TOPIC else 'NOT SET'} email={'on' if EMAIL_ENABLED else 'off'}")

    start = time.monotonic()
    last_heartbeat = start  # for the periodic "still healthy" liveness ping
    alerted = {}            # uid -> last alert time (monotonic)
    consec_fail = 0
    degraded = False
    # Quiet liveness ping only when a human/handoff dispatched the run (not cron restarts).
    heartbeat_pending = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"

    while True:
        now_utc = datetime.now(timezone.utc)

        if now_utc >= stop_dt:
            log("deadline reached — stopping, no rechain.")
            set_output("deadline_reached", "true")
            return 0
        if time.monotonic() - start >= MAX_RUNTIME_SECONDS:
            log("max runtime reached — exiting for clean handoff.")
            set_output("deadline_reached", "false")
            return 0

        try:
            data = fetch()
            consec_fail = 0
            if degraded:
                ntfy("Ferry monitor recovered", "Polling works again.",
                     priority="default", tags="white_check_mark")
                degraded = False

            if heartbeat_pending:
                total_aft = sum(
                    1 for it in data.get("items", [])
                    if (_safe_hour(it) or -1) >= AFTERNOON_FROM_HOUR
                )
                ntfy(
                    "Ferry monitor started",
                    f"Watching {total_aft} afternoon VK sailings (19 Jun). "
                    f"You'll get an URGENT push the instant a car spot frees up.",
                    priority="low", tags="eyes",
                )
                heartbeat_pending = False

            open_sailings = actionable(data, now_utc)
            now_mono = time.monotonic()
            open_uids = set()
            for s in open_sailings:
                open_uids.add(s["uid"])
                last = alerted.get(s["uid"])
                if last is None or (now_mono - last) >= RE_ALERT_COOLDOWN:
                    alert(s)
                    alerted[s["uid"]] = now_mono
            # Forget sailings that closed again, so a re-open alerts immediately.
            for uid in [u for u in alerted if u not in open_uids]:
                del alerted[uid]

            if open_sailings:
                log("OPEN: " + ", ".join(f"{s['time']} {s['ship']}={s['sv']}" for s in open_sailings))
            else:
                log("no afternoon car spots open")

            # Periodic liveness ping so silence is never mistaken for "monitor died".
            if HEARTBEAT_INTERVAL_SECONDS > 0 and (now_mono - last_heartbeat) >= HEARTBEAT_INTERVAL_SECONDS:
                total_aft = sum(
                    1 for it in data.get("items", []) if (_safe_hour(it) or -1) >= AFTERNOON_FROM_HOUR
                )
                detail = (
                    "OPEN " + ", ".join(f"{s['time']} {s['ship']}={s['sv']}" for s in open_sailings)
                    if open_sailings else "no spots open yet"
                )
                ntfy(
                    "Ferry monitor healthy",
                    f"Still running. Watching {total_aft} afternoon sailings; {detail}.",
                    priority="low", tags="green_heart",
                )
                log("heartbeat sent")
                last_heartbeat = now_mono

        except urllib.error.HTTPError as e:
            consec_fail += 1
            log(f"HTTP {e.code} (consec={consec_fail})")
            if e.code == 429:
                log("rate limited — backing off 60s")
                time.sleep(60)
                continue
        except Exception as e:
            consec_fail += 1
            log(f"poll error: {e} (consec={consec_fail})")

        if consec_fail >= 6 and not degraded:
            ntfy("Ferry monitor DEGRADED",
                 f"{consec_fail} consecutive poll failures — monitoring may be down.",
                 priority="high", tags="warning")
            degraded = True

        time.sleep(POLL_INTERVAL + random.uniform(0, 2))  # small jitter, be polite


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("interrupted")
        sys.exit(0)
