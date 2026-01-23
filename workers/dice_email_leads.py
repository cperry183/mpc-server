import os
import imaplib
import email
import re
import redis
import time
import smtplib
from email.mime.text import MIMEText

IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_USER = os.getenv("IMAP_USER")
IMAP_PASS = os.getenv("IMAP_PASS")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
MAIL_TO = os.getenv("MAIL_TO")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
POLL_SECONDS = 21600  # 6 hours

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

DICE_FROM_MATCH = re.compile(r"dice", re.IGNORECASE)
URL_MATCH = re.compile(r"https://www\.dice\.com/jobs/view/[^\"\\s]+")


def send_email(body: str):
    msg = MIMEText(body, "plain")
    msg["From"] = SMTP_USER
    msg["To"] = MAIL_TO
    msg["Subject"] = "New Dice Cyber Security Jobs"

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(SMTP_USER, [MAIL_TO], msg.as_string())


def main():
    while True:
        mail = imaplib.IMAP4_SSL(IMAP_HOST)
        mail.login(IMAP_USER, IMAP_PASS)
        mail.select("inbox")

        _, ids = mail.search(None, "ALL")
        ids = ids[0].split()[-20:]  # only recent emails

        found = []

        for i in ids:
            _, data = mail.fetch(i, "(RFC822)")
            msg = email.message_from_bytes(data[0][1])

            if not DICE_FROM_MATCH.search(msg.get("From", "")):
                continue

            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body += part.get_payload(decode=True).decode(errors="ignore")
            else:
                body = msg.get_payload(decode=True).decode(errors="ignore")

            for url in URL_MATCH.findall(body):
                if r.setnx(f"dice:{url}", 1):
                    r.expire(f"dice:{url}", 1209600)
                    found.append(url)

        mail.logout()

        if found:
            send_email("\n".join(found))
            print(f"Sent {len(found)} new Dice jobs")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
