FROM python:3.13.13-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.13.13-slim
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /install /usr/local
COPY app.py layout.py layout_bots.py layout_backtest.py layout_ml.py layout_ml_clusters.py layout_ml_data.py layout_ml_live.py layout_ml_models.py layout_strategies.py callbacks.py callbacks_bots.py callbacks_backtest.py callbacks_ml.py callbacks_strategies.py data.py bot_data.py backtest_data.py charts.py ./
COPY assets/ ./assets/
RUN useradd -m -u 1000 dashboard && chown -R dashboard:dashboard /app
USER dashboard
HEALTHCHECK --interval=10s --timeout=5s --retries=3 CMD curl -f http://localhost:8050/_dash-layout || exit 1
CMD ["python", "app.py"]
