import email
import imaplib
import logging
import os
import re
import smtplib
import time
from dataclasses import dataclass
from email.header import decode_header
from email.message import Message
from email.mime.text import MIMEText
from typing import Iterable, List, Optional

import redis

IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_USER = os.getenv("IMAP_USER")
IMAP_PASS = os.getenv("IMAP_PASS")
IMAP_MAILBOX = os.getenv("IMAP_MAILBOX", "inbox")
RECENT_LIMIT = int(os.getenv("RECENT_LIMIT", "20"))

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
MAIL_TO = os.getenv("MAIL_TO")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "21600"))  # 6 hours
SEEN_TTL_SECONDS = int(os.getenv("SEEN_TTL_SECONDS", "1209600"))  # 14 days
SMTP_TIMEOUT = int(os.getenv("SMTP_TIMEOUT", "20"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

DICE_FROM_MATCH = re.compile(r"dice", re.IGNORECASE)
URL_MATCH = re.compile(r"https://www\.dice\.com/jobs/view/[^\"\\s]+")

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


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
    imap_mailbox: str


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
        imap_mailbox=IMAP_MAILBOX,
    )


def get_redis_client() -> redis.Redis:
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)


def open_mailbox(config: EmailConfig) -> imaplib.IMAP4_SSL:
    mail = imaplib.IMAP4_SSL(config.imap_host)
    mail.login(config.imap_user, config.imap_pass)
    mail.select(config.imap_mailbox)
    return mail


def iter_recent_message_ids(mail: imaplib.IMAP4_SSL, limit: int = RECENT_LIMIT) -> Iterable[bytes]:
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


def decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="ignore")
    except LookupError:
        return payload.decode("utf-8", errors="ignore")


def decode_header_value(value: Optional[str]) -> str:
    if not value:
        return ""
    pieces = []
    for part, charset in decode_header(value):
        if isinstance(part, bytes):
            encoding = charset or "utf-8"
            try:
                pieces.append(part.decode(encoding, errors="ignore"))
            except LookupError:
                pieces.append(part.decode("utf-8", errors="ignore"))
        else:
            pieces.append(part)
    return "".join(pieces)


def extract_body(message: Message) -> str:
    if message.is_multipart():
        parts: List[str] = []
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                parts.append(decode_part(part))
        return "\n".join(parts)
    return decode_part(message)


def message_from_dice(message: Message) -> bool:
    from_header = decode_header_value(message.get("From", ""))
    subject = decode_header_value(message.get("Subject", ""))
    return bool(DICE_FROM_MATCH.search(from_header) or DICE_FROM_MATCH.search(subject))


def extract_urls(body: str) -> List[str]:
    return URL_MATCH.findall(body)


def build_dedupe_key(url: str) -> str:
    return f"dice:{url}"


def mark_seen(client: redis.Redis, url: str) -> bool:
    key = build_dedupe_key(url)
    if not client.setnx(key, 1):
        return False
    client.expire(key, SEEN_TTL_SECONDS)
    return True


def send_email(config: EmailConfig, body: str) -> None:
    msg = MIMEText(body, "plain")
    msg["From"] = config.smtp_user
    msg["To"] = config.mail_to
    msg["Subject"] = "New Dice Cyber Security Jobs"

    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=SMTP_TIMEOUT) as server:
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
                if mark_seen(client, url):
                    found.append(url)
    finally:
        try:
            mail.logout()
        except imaplib.IMAP4.error:
            logger.warning("Failed to logout cleanly from IMAP")

    return found


def sleep_with_backoff(base_seconds: int, error_count: int) -> None:
    delay = min(base_seconds * (2**error_count), 3600)
    time.sleep(delay)


def run_poll_loop() -> None:
    config = load_config()
    client = get_redis_client()
    error_count = 0

    while True:
        try:
            found = poll_once(client, config)
            if found:
                send_email(config, "\n".join(found))
                logger.info("Sent %s new Dice jobs", len(found))
            error_count = 0
            time.sleep(POLL_SECONDS)
        except Exception as exc:
            error_count += 1
            logger.exception("Polling failed: %s", exc)
            sleep_with_backoff(POLL_SECONDS, error_count)


def main() -> None:
    run_poll_loop()


if __name__ == "__main__":
    main()
