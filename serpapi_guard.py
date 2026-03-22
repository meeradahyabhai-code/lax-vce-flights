"""SerpAPI rate limiter — prevents runaway spending.

Tracks all ad-hoc SerpAPI calls (outside the scheduled 48h refresh) in a
persistent log file. If calls exceed the threshold in a 24h window, blocks
further calls and emails mdahya@gmail.com.

Usage:
    from serpapi_guard import check_serpapi_budget, log_serpapi_calls

    # Before making calls:
    check_serpapi_budget(num_calls=1)  # raises BudgetExceeded if over limit

    # After making calls:
    log_serpapi_calls(num_calls=1, source="hotels")
"""

import json
import os
import smtplib
import time
from email.mime.text import MIMEText

# Use /tmp on Vercel (read-only filesystem), data/ locally
_data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DATA_DIR = "/tmp" if os.environ.get("VERCEL") else _data_dir
RATE_LOG_FILE = os.path.join(DATA_DIR, "serpapi_rate_log.json")

# Threshold: max ad-hoc calls in a 24h rolling window
ADHOC_THRESHOLD_24H = 100
WINDOW_SECONDS = 24 * 3600

# Alert recipient
ALERT_EMAIL = "mdahya@gmail.com"


class BudgetExceeded(Exception):
    """Raised when SerpAPI ad-hoc call threshold is exceeded."""
    pass


def _read_log() -> list[dict]:
    """Read the persistent rate log."""
    try:
        with open(RATE_LOG_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _write_log(entries: list[dict]) -> None:
    """Write the rate log, pruning entries older than 24h."""
    cutoff = time.time() - WINDOW_SECONDS
    recent = [e for e in entries if e.get("ts", 0) > cutoff]
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(RATE_LOG_FILE, "w") as f:
        json.dump(recent, f, indent=2)


def _count_recent() -> int:
    """Count ad-hoc SerpAPI calls in the last 24h."""
    cutoff = time.time() - WINDOW_SECONDS
    entries = _read_log()
    return sum(e.get("count", 1) for e in entries if e.get("ts", 0) > cutoff)


def _send_alert(current_count: int, attempted: int, source: str) -> None:
    """Email alert when threshold is hit."""
    gmail_user = os.environ.get("GMAIL_USER", "")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not gmail_user or not gmail_pass:
        print(f"[SerpAPI Guard] ALERT: {current_count} calls in 24h "
              f"(threshold: {ADHOC_THRESHOLD_24H}). Email not configured.")
        return

    subject = f"SerpAPI Budget Alert — {current_count} calls in 24h"
    body = (
        f"SerpAPI ad-hoc call threshold exceeded.\n\n"
        f"Calls in last 24h: {current_count}\n"
        f"Threshold: {ADHOC_THRESHOLD_24H}\n"
        f"Blocked request: {attempted} calls from '{source}'\n\n"
        f"The scheduled 48h flight refresh is NOT counted in this total.\n"
        f"This alert means something outside the normal schedule is making calls.\n\n"
        f"Check the rate log at: data/serpapi_rate_log.json"
    )

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = ALERT_EMAIL

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(gmail_user, gmail_pass)
            smtp.sendmail(gmail_user, ALERT_EMAIL, msg.as_string())
        print(f"[SerpAPI Guard] Alert email sent to {ALERT_EMAIL}")
    except Exception as exc:
        print(f"[SerpAPI Guard] Failed to send alert email: {exc}")


def check_serpapi_budget(num_calls: int = 1, source: str = "unknown") -> None:
    """Check if we can make `num_calls` more SerpAPI calls within budget.

    Raises BudgetExceeded if the 24h threshold would be exceeded.
    Sends an email alert on first breach.
    """
    current = _count_recent()
    if current + num_calls > ADHOC_THRESHOLD_24H:
        _send_alert(current, num_calls, source)
        raise BudgetExceeded(
            f"SerpAPI budget exceeded: {current} calls in last 24h "
            f"(threshold: {ADHOC_THRESHOLD_24H}). "
            f"Blocked {num_calls} calls from '{source}'. "
            f"Alert sent to {ALERT_EMAIL}."
        )


def log_serpapi_calls(num_calls: int = 1, source: str = "unknown") -> None:
    """Log ad-hoc SerpAPI calls to the persistent rate file."""
    entries = _read_log()
    entries.append({
        "ts": time.time(),
        "count": num_calls,
        "source": source,
    })
    _write_log(entries)
