FROM ghcr.io/astral-sh/uv:0.10.12 AS uv
FROM python:3.11-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
# amd64 is intentional: the current provider uses Google Chrome, not a substituted browser.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg libatomic1 xvfb xauth x11vnc ffmpeg fonts-noto-core fonts-noto-cjk tini \
    && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main' > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home easydial \
    && mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
RUN uv sync --frozen --no-dev && chown -R easydial:easydial /app
COPY deploy/container/entrypoint.sh /usr/local/bin/easydial-entrypoint
RUN chmod 755 /usr/local/bin/easydial-entrypoint
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 DISPLAY=:99 \
    WHATSAPP_DATABASE=/data/whatsapp.sqlite3 CUSTOMER_SESSION_DIRECTORY=/data/customers
USER easydial
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
 CMD python -c "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8001/health',timeout=3))['status']=='ok'"
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/easydial-entrypoint"]
