FROM python:3.13.13-slim
WORKDIR /app

# build-essential required for hdbscan C++ compilation
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

RUN adduser --disabled-password --gecos "" appuser

COPY pyproject.toml .
RUN pip install --upgrade pip setuptools && \
    pip install --no-cache-dir -e .

COPY ml_service/ ml_service/

USER appuser
CMD ["uvicorn", "ml_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
