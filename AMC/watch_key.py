"""
Temporary script: polls until the AMC API key becomes active,
then sends a Gmail alert. Stops at midnight local time.
"""

import smtplib
import time
from datetime import datetime
from email.mime.text import MIMEText

import requests
from dotenv import load_dotenv
import os

load_dotenv()

API_KEY         = os.getenv("AMC_API_KEY", "")
GMAIL_USER      = os.getenv("GMAIL_USER", "")
GMAIL_PASSWORD  = os.getenv("GMAIL_APP_PASSWORD", "")
NOTIFY_EMAIL    = os.getenv("NOTIFY_EMAIL", "")
INTERVAL        = 15 * 60  # 15 minutes

TEST_URL = "https://api.amctheatres.com/v2/theatres?q=empire"
HEADERS  = {"X-AMC-Vendor-Key": API_KEY, "Accept": "application/json"}


def is_active() -> bool:
    try:
        r = requests.get(TEST_URL, headers=HEADERS, timeout=15)
        data = r.json()
        # Key is rejected when errors contain code 12005
        errors = data.get("errors", [])
        if any(e.get("code") == 12005 for e in errors):
            return False
        return True
    except Exception as e:
        print(f"[{now()}] Request error: {e}")
        return False


def send_alert():
    msg = MIMEText(
        "Your AMC API key is now active!\n\n"
        "Head back to Cursor — we can run the theatre lookup and finish setup."
    )
    msg["Subject"] = "[AMC Bot] API key is live!"
    msg["From"]    = GMAIL_USER
    msg["To"]      = NOTIFY_EMAIL
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as s:
            s.ehlo(); s.starttls()
            s.login(GMAIL_USER, GMAIL_PASSWORD)
            s.sendmail(GMAIL_USER, NOTIFY_EMAIL, msg.as_string())
        print(f"[{now()}] Alert email sent to {NOTIFY_EMAIL}.")
    except Exception as e:
        print(f"[{now()}] Failed to send email: {e}")


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def end_of_day() -> float:
    t = datetime.now()
    midnight = t.replace(hour=23, minute=59, second=59, microsecond=0)
    return midnight.timestamp()


if __name__ == "__main__":
    deadline = end_of_day()
    print(f"[{now()}] Watching for API key activation. Checking every 15 min until midnight.")

    while time.time() < deadline:
        print(f"[{now()}] Checking key...", end=" ", flush=True)
        if is_active():
            print("ACTIVE!")
            send_alert()
            break
        else:
            print("not yet.")
            next_check = min(time.time() + INTERVAL, deadline)
            sleep_secs = next_check - time.time()
            if sleep_secs > 0:
                time.sleep(sleep_secs)
    else:
        print(f"[{now()}] End of day reached. Key still not active — try again tomorrow.")
