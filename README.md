# praamid-ferry-monitor

Watches the [praamid.ee](https://www.praamid.ee) ferry API and sends an **instant ntfy.sh phone
push** the moment a car spot (`capacities.sv`) frees up on an afternoon **VK** sailing on
**2026-06-19**, so you can jump on the booking page and grab it. Runs free on GitHub Actions.

It **never books** anything — it only alerts and deep-links you to the booking page.

## Setup (~5–10 min)

1. **Phone push (ntfy):** install the **ntfy** app (iOS / Android), and *subscribe to a topic* —
   pick a long random name, e.g. `praamid-virgo-7f3a9c2e1b`. The topic name is both address and
   password, so keep it private. In the app, allow notifications and set the topic to override
   Do Not Disturb (Android: also enable "Instant delivery"; iOS: mark the app Time-Sensitive) so the
   `urgent` alert wakes you.
2. **Repo:** create a **public** repo on your personal GitHub account and push these files.
3. **Secret:** repo → Settings → Secrets and variables → Actions → New repository secret →
   `NTFY_TOPIC` = your topic string.
4. **Start:** Actions tab → enable workflows → **ferry-monitor** → **Run workflow**. It chains
   itself from there and **auto-stops at 22:30 (Tallinn) on 19 Jun**.

### Optional email backup
Add secrets `SMTP_USER`, `SMTP_PASS` (a Gmail *app password* — needs 2-step verification),
`MAIL_TO` (e.g. `virgo.talk@gmail.com`), and uncomment the email lines in
`.github/workflows/monitor.yml`.

## How it stays alive ~26h on free infra

GitHub kills any job at 6h, so each run polls for ~5h40m then hands off:
- `concurrency` keeps a **singleton** (1 running + 1 queued successor) — no double polling.
- A `*/5` **cron watchdog** restarts the loop within 5 min if it ever dies.
- Each run **self-dispatches** its successor for a ~30s handoff, and **disables** the workflow once
  the deadline passes.

## Test it before trusting it overnight

```bash
# 1) Push pipeline (after subscribing the app): your phone should buzz within seconds.
curl -d "test push" ntfy.sh/<your-topic>

# 2) End-to-end alert locally — force "afternoon" to mean "any sailing" so a currently
#    full-but-morning boat (e.g. 07:20 -> sv:77) triggers a real urgent push, then exits.
NTFY_TOPIC=<your-topic> AFTERNOON_FROM_HOUR=0 MIN_LEAD_MINUTES=0 \
  POLL_INTERVAL=3 MAX_RUNTIME_SECONDS=10 python3 monitor.py
```

## Tuning (env vars / repo variables)

| Var | Default | Meaning |
|---|---|---|
| `AFTERNOON_FROM_HOUR` | `12` | Earliest departure hour to watch (Tallinn local) |
| `MIN_LEAD_MINUTES` | `30` | Ignore sailings departing sooner than this |
| `CAP_FIELD` | `sv` | Capacity field to watch (`sv` = cars) |
| `POLL_INTERVAL` | `10` | Seconds between polls |
| `STOP_AT` | `2026-06-19T22:30:00+03:00` | Hard stop; workflow disables itself after this |
| `RE_ALERT_COOLDOWN` | `300` | Re-ping interval while a sailing stays open |
| `HEARTBEAT_INTERVAL_SECONDS` | `3600` | Hourly low-priority "still healthy" ping (0 disables) |

## Stop / cleanup

Auto-disables at `STOP_AT`. To stop early: Actions → ferry-monitor → ••• → **Disable workflow**,
or just delete the repo.
