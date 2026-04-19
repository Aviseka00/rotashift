# RotaShift API + static UI. Scale horizontally: run N containers behind a load balancer; shared MongoDB.
FROM python:3.12-slim-bookworm

WORKDIR /app

RUN useradd --create-home --shell /usr/sbin/nologin --uid 10001 rotashift

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

USER rotashift

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

# Render and other hosts inject PORT; default 8000 for local Docker.
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health/live')" || exit 1

CMD ["/bin/sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*'"]
