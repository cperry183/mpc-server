import hashlib
import logging
import os
import smtplib
import time
from dataclasses import dataclass
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import Iterable, List, Optional

import feedparser
import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "21600"))  # default: 6 hours
SEEN_TTL_SECONDS = int(os.getenv("SEEN_TTL_SECONDS", "1209600"))  # 14 days
RSS_URLS = [u.strip() for u in os.getenv("RSS_URLS", "").split(",") if u.strip()]
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
MAIL_FROM = os.getenv("MAIL_FROM", SMTP_USER)
MAIL_TO = os.getenv("MAIL_TO", "")
SMTP_TIMEOUT = int(os.getenv("SMTP_TIMEOUT", "20"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
KEY_PREFIX = os.getenv("KEY_PREFIX", "jobleads:")

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RssConfig:
    redis_url: str
    poll_seconds: int
    seen_ttl_seconds: int
    rss_urls: List[str]
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_pass: str
    mail_from: str
    mail_to: str
    smtp_timeout: int
    key_prefix: str


def load_config() -> RssConfig:
    if not RSS_URLS:
        raise RuntimeError("RSS_URLS is required and must include at least one URL.")
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS or not MAIL_TO:
        raise RuntimeError("SMTP_HOST/SMTP_USER/SMTP_PASS/MAIL_TO must be set.")
    return RssConfig(
        redis_url=REDIS_URL,
        poll_seconds=POLL_SECONDS,
        seen_ttl_seconds=SEEN_TTL_SECONDS,
        rss_urls=RSS_URLS,
        smtp_host=SMTP_HOST,
        smtp_port=SMTP_PORT,
        smtp_user=SMTP_USER,
        smtp_pass=SMTP_PASS,
        mail_from=MAIL_FROM,
        mail_to=MAIL_TO,
        smtp_timeout=SMTP_TIMEOUT,
        key_prefix=KEY_PREFIX,
    )


def get_redis_client(config: RssConfig) -> redis.Redis:
    return redis.Redis.from_url(config.redis_url, decode_responses=True)


def fetch_feed(url: str) -> feedparser.FeedParserDict:
    return feedparser.parse(url)


def iter_entries(feed: feedparser.FeedParserDict) -> Iterable[dict]:
    return feed.get("entries", []) or []


def entry_fingerprint(entry: dict) -> str:
    raw = entry.get("id") or entry.get("link") or entry.get("title", "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_key(config: RssConfig, fingerprint: str) -> str:
    return f"{config.key_prefix}{fingerprint}"


def mark_seen(client: redis.Redis, config: RssConfig, fingerprint: str) -> bool:
    key = build_key(config, fingerprint)
    if not client.setnx(key, 1):
        return False
    client.expire(key, config.seen_ttl_seconds)
    return True


def format_entry(entry: dict) -> str:
    title = entry.get("title", "Untitled")
    link = entry.get("link", "")
    return f"{title}\n{link}".strip()


def send_email(config: RssConfig, entries: List[str]) -> None:
    body = "\n\n".join(entries)
    message = MIMEText(body, "plain")
    message["From"] = config.mail_from
    message["To"] = config.mail_to
    message["Subject"] = "New Job Leads"
    message["Date"] = formatdate(localtime=True)

    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=config.smtp_timeout) as server:
        server.starttls()
        server.login(config.smtp_user, config.smtp_pass)
        server.sendmail(config.mail_from, [config.mail_to], message.as_string())


def poll_once(client: redis.Redis, config: RssConfig) -> List[str]:
    found: List[str] = []
    for url in config.rss_urls:
        feed = fetch_feed(url)
        for entry in iter_entries(feed):
            fingerprint = entry_fingerprint(entry)
            if mark_seen(client, config, fingerprint):
                found.append(format_entry(entry))
    return found


def sleep_with_backoff(base_seconds: int, error_count: int) -> None:
    delay = min(base_seconds * (2**error_count), 3600)
    time.sleep(delay)


def run_poll_loop() -> None:
    config = load_config()
    client = get_redis_client(config)
    error_count = 0

    while True:
        try:
            found = poll_once(client, config)
            if found:
                send_email(config, found)
                logger.info("Sent %s new job leads", len(found))
            error_count = 0
            time.sleep(config.poll_seconds)
        except Exception as exc:
            error_count += 1
            logger.exception("Polling failed: %s", exc)
            sleep_with_backoff(config.poll_seconds, error_count)


def main() -> None:
    run_poll_loop()


if __name__ == "__main__":
    main()
