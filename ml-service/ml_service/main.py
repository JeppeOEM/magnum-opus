from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date

import structlog
from fastapi import FastAPI
from pydantic import BaseModel

from fastapi import HTTPException

from ml_service.clustering.pipeline import run_clustering
from ml_service.config import get_settings
from ml_service.features.extractor import extract as _extract_features
from ml_service.inference import loop as _loop
from ml_service.labels.generator import add_labels_to_parquet
from ml_service.logging_setup import configure
from ml_service.registry import registry as _registry
from ml_service.training.trainer import run_training

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_settings()
    configure(cfg.log_level)
    _registry.init(cfg.ml_model_registry_path)
    log.info("ml_service_starting", questdb=cfg.questdb_http_addr)
    yield
    log.info("ml_service_stopped")


app = FastAPI(title="ml-service", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


class ExtractRequest(BaseModel):
    exchange: str
    symbol: str
    start_date: date
    end_date: date


@app.post("/features/extract")
async def features_extract(req: ExtractRequest):
    cfg = get_settings()
    stats = _extract_features(
        cfg.questdb_http_addr,
        cfg.ml_feature_store_path,
        req.exchange,
        req.symbol,
        req.start_date,
        req.end_date,
    )
    return stats


class LabelRequest(BaseModel):
    exchange: str
    symbol: str
    start_date: str  # "2026-05-01"
    end_date: str
    horizons: list[int] = [3, 10]
    threshold_bps: float = 1.0


@app.post("/labels/generate")
async def labels_generate(req: LabelRequest):
    cfg = get_settings()
    return add_labels_to_parquet(
        cfg.ml_feature_store_path,
        req.exchange,
        req.symbol,
        req.start_date,
        req.end_date,
        req.horizons,
        req.threshold_bps,
    )


class ClusterRequest(BaseModel):
    exchange: str
    symbol: str
    start_date: str
    end_date: str
    n_pca_components: int = 10
    min_cluster_size: int = 50


@app.post("/cluster")
async def cluster(req: ClusterRequest):
    cfg = get_settings()
    return run_clustering(
        cfg.ml_feature_store_path,
        cfg.ml_model_registry_path,
        req.exchange,
        req.symbol,
        req.start_date,
        req.end_date,
        req.n_pca_components,
        req.min_cluster_size,
    )


class TrainRequest(BaseModel):
    exchange: str
    symbol: str
    start_date: str
    end_date: str
    target: str = "label_revert_10"


@app.post("/train")
async def train(req: TrainRequest):
    cfg = get_settings()
    results = run_training(
        cfg.ml_feature_store_path,
        cfg.ml_model_registry_path,
        req.exchange,
        req.symbol,
        req.start_date,
        req.end_date,
        req.target,
    )
    # Upsert trained models into the registry
    if "error" not in results:
        for entry in results.values():
            _registry.upsert(
                exchange=req.exchange,
                symbol=req.symbol,
                regime=entry["regime"],
                cluster=entry["cluster"],
                model_type=entry["model_type"],
                cv_score=entry["cv_score"],
                n_samples=entry["n_samples"],
                feature_names=entry["feature_names"],
                shap_summary=entry["shap_summary"],
                artifact_dir=entry["artifact_dir"],
            )
    return results


@app.get("/registry")
async def registry_list():
    return _registry.get_all()


@app.delete("/registry/{entry_id}")
async def registry_delete(entry_id: str):
    ok = _registry.delete(entry_id)
    if not ok:
        raise HTTPException(404, "entry not found")
    return {"deleted": entry_id}


class StartRequest(BaseModel):
    symbols: list[dict]  # [{"exchange": "bybit", "symbol": "BTCUSDT"}]


@app.post("/inference/start")
async def inference_start(req: StartRequest):
    cfg = get_settings()
    return _loop.start(cfg.questdb_http_addr, cfg.redis_url, req.symbols)


@app.post("/inference/stop")
async def inference_stop():
    return await _loop.stop()


@app.get("/inference/status")
async def inference_status():
    return _loop.status()
