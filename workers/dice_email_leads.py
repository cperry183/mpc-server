import email
import imaplib
import os
import re
import smtplib
import time
from dataclasses import dataclass
from email.message import Message
from email.mime.text import MIMEText
from typing import Iterable, List

import redis

IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_USER = os.getenv("IMAP_USER")
IMAP_PASS = os.getenv("IMAP_PASS")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
MAIL_TO = os.getenv("MAIL_TO")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "21600"))  # 6 hours
SEEN_TTL_SECONDS = int(os.getenv("SEEN_TTL_SECONDS", "1209600"))  # 14 days

DICE_FROM_MATCH = re.compile(r"dice", re.IGNORECASE)
URL_MATCH = re.compile(r"https://www\.dice\.com/jobs/view/[^\"\\s]+")


@dataclass(frozen=True)
class EmailConfig:
    imap_host: str
    imap_user: str
    imap_pass: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_pass: str
    mail_to: str


def load_config() -> EmailConfig:
    missing = [name for name, value in {
        "IMAP_USER": IMAP_USER,
        "IMAP_PASS": IMAP_PASS,
        "SMTP_USER": SMTP_USER,
        "SMTP_PASS": SMTP_PASS,
        "MAIL_TO": MAIL_TO,
    }.items() if not value]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    return EmailConfig(
        imap_host=IMAP_HOST,
        imap_user=IMAP_USER or "",
        imap_pass=IMAP_PASS or "",
        smtp_host=SMTP_HOST,
        smtp_port=SMTP_PORT,
        smtp_user=SMTP_USER or "",
        smtp_pass=SMTP_PASS or "",
        mail_to=MAIL_TO or "",
    )


def get_redis_client() -> redis.Redis:
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)


def open_mailbox(config: EmailConfig) -> imaplib.IMAP4_SSL:
    mail = imaplib.IMAP4_SSL(config.imap_host)
    mail.login(config.imap_user, config.imap_pass)
    mail.select("inbox")
    return mail


def iter_recent_message_ids(mail: imaplib.IMAP4_SSL, limit: int = 20) -> Iterable[bytes]:
    status, ids = mail.search(None, "ALL")
    if status != "OK":
        return []
    message_ids = ids[0].split()
    return message_ids[-limit:]


def fetch_message(mail: imaplib.IMAP4_SSL, message_id: bytes) -> Message:
    status, data = mail.fetch(message_id, "(RFC822)")
    if status != "OK":
        raise RuntimeError(f"Failed to fetch message id {message_id!r}")
    return email.message_from_bytes(data[0][1])


def extract_body(message: Message) -> str:
    if message.is_multipart():
        parts: List[str] = []
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                parts.append(part.get_payload(decode=True).decode(errors="ignore"))
        return "\n".join(parts)
    return message.get_payload(decode=True).decode(errors="ignore")


def message_from_dice(message: Message) -> bool:
    return bool(DICE_FROM_MATCH.search(message.get("From", "")))


def extract_urls(body: str) -> List[str]:
    return URL_MATCH.findall(body)


def is_new_url(client: redis.Redis, url: str) -> bool:
    key = f"dice:{url}"
    if client.setnx(key, 1):
        client.expire(key, SEEN_TTL_SECONDS)
        return True
    return False


def send_email(config: EmailConfig, body: str) -> None:
    msg = MIMEText(body, "plain")
    msg["From"] = config.smtp_user
    msg["To"] = config.mail_to
    msg["Subject"] = "New Dice Cyber Security Jobs"

    with smtplib.SMTP(config.smtp_host, config.smtp_port) as server:
        server.starttls()
        server.login(config.smtp_user, config.smtp_pass)
        server.sendmail(config.smtp_user, [config.mail_to], msg.as_string())


def poll_once(client: redis.Redis, config: EmailConfig) -> List[str]:
    mail = open_mailbox(config)
    found: List[str] = []

    try:
        for message_id in iter_recent_message_ids(mail):
            message = fetch_message(mail, message_id)
            if not message_from_dice(message):
                continue
            body = extract_body(message)
            for url in extract_urls(body):
                if is_new_url(client, url):
                    found.append(url)
    finally:
        mail.logout()

    return found


def run_poll_loop() -> None:
    config = load_config()
    client = get_redis_client()

    while True:
        found = poll_once(client, config)
        if found:
            send_email(config, "\n".join(found))
            print(f"Sent {len(found)} new Dice jobs")
        time.sleep(POLL_SECONDS)


def main() -> None:
    run_poll_loop()


if __name__ == "__main__":
    main()
