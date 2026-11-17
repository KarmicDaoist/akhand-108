FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Kolkata \
    DATA_DIR=/data \
    LIBRARY_PATH=/app/library/library.json

# tzdata: the host runs UTC, day boundaries and the gathering time follow TZ via zoneinfo
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin akhand \
 && mkdir -p /app/library /data /export \
 && chown akhand:akhand /data /export

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY akhand ./akhand
# the sankalpa template is editable text; bind-mount a corrected copy over it (or set SANKALPA_TEMPLATE_PATH)
COPY sankalpa ./sankalpa
# The text library is mounted (compose does it) or copied to /app/library/library.json.

USER akhand
VOLUME ["/data"]
STOPSIGNAL SIGTERM
# healthy and degraded both pass; only "down" (exit 2) fails
HEALTHCHECK --interval=60s --timeout=15s --start-period=90s --retries=3 \
  CMD sh -c 'python -m akhand.ctl status >/dev/null; [ $? -ne 2 ]'
CMD ["python", "-m", "akhand.main"]
