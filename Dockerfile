FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN addgroup --system app && adduser --system --ingroup app app

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY workers ./workers
COPY scripts ./scripts

RUN chmod +x /srv/scripts/entrypoint.sh

USER app

EXPOSE 8000
CMD ["/srv/scripts/entrypoint.sh"]

