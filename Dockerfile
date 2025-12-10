FROM python:3.11-slim AS spellfix-builder
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY spellfix.c .
RUN gcc -fPIC -shared spellfix.c -o spellfix.so

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE_URL=sqlite:////data/autocomplete.db \
    ASYNC_DATABASE_URL=sqlite+aiosqlite:////data/autocomplete.db \
    DB_DOWNLOAD_URL=https://cloud.farshidhakimy.de/s/ScfdTePfPc3oaR6/download

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libsqlite3-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=spellfix-builder /build/spellfix.so /app/spellfix.so

RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 8001
ENV PYTHONPATH=/app
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]