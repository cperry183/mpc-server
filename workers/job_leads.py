import os
import time
import hashlib
import smtplib
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import List, Dict

import redis
import feedparser

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

KEY_PREFIX = "jobleads:_

