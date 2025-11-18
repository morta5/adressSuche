FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE_URL=sqlite:////data/autocomplete.db \
    ASYNC_DATABASE_URL=sqlite+aiosqlite:////data/autocomplete.db

WORKDIR /app

# Install runtime deps
COPY v2/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy source
COPY v2 /app/v2
COPY advanced_search /app/advanced_search

EXPOSE 8001
ENV PYTHONPATH=/app
CMD ["uvicorn", "v2.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "4"]
