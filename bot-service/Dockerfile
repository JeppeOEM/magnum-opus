FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /install /usr/local
COPY bot_service/ ./bot_service/
COPY strategies/ ./strategies/
RUN useradd -m -u 1000 bot && chown -R bot:bot /app
USER bot
CMD ["uvicorn", "bot_service.main:app", "--host", "0.0.0.0", "--port", "8090"]
