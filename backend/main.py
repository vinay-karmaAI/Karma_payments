import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from log_config import logger, audit_log, recon_log
from data_generator import generate_data
from reconciliation_engine import reconcile
from gemini_analyst import analyse_gaps, chat_with_data


FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


class Settings(BaseSettings):
    gemini_api_key: str = ""
    app_env:        str = "production"
    log_level:      str = "INFO"
    host:           str = "0.0.0.0"
    port:           int = 8000
    cors_origins:   str = "*"

    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
START_TIME = time.time()
APP_VERSION = "1.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Payments Reconciliation Engine v{APP_VERSION} starting")
    logger.info(f"Environment: {settings.app_env}")
    logger.info(f"Gemini AI: {'configured' if settings.gemini_api_key else 'NOT configured'}")
    logger.info(f"Frontend: {'found' if FRONTEND_DIR.exists() else 'not found'} at {FRONTEND_DIR}")
    audit_log("APPLICATION_START", version=APP_VERSION, env=settings.app_env)
    yield
    logger.info("Application shutting down")
    audit_log("APPLICATION_STOP")


app = FastAPI(
    title="Payments Reconciliation Engine",
    description="AI-powered month-end reconciliation gap detector with Gemini analysis",
    version=APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.cors_origins == "*" else settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logger(request: Request, call_next):
    req_id    = str(uuid.uuid4())[:8]
    start     = time.perf_counter()
    client_ip = request.client.host if request.client else "unknown"

    logger.info(
        f"-> {request.method} {request.url.path}",
        extra={"req_id": req_id, "client": client_ip},
    )

    try:
        response = await call_next(request)
    except Exception as exc:
        logger.error(f"Unhandled error in {request.url.path}: {exc}")
        raise

    elapsed = (time.perf_counter() - start) * 1000
    logger.info(
        f"<- {request.method} {request.url.path} -> {response.status_code} ({elapsed:.1f}ms)",
        extra={"req_id": req_id, "status": response.status_code, "ms": round(elapsed, 1)},
    )
    response.headers["X-Request-ID"] = req_id
    return response


class ReconcileRequest(BaseModel):
    platform_transactions: List[Dict[str, Any]] = Field(..., description="Platform ledger records")
    bank_settlements:      List[Dict[str, Any]] = Field(..., description="Bank settlement records")
    review_year:           int  = Field(2024, ge=2000, le=2100)
    review_month:          int  = Field(3,    ge=1,    le=12)


class AIAnalysisRequest(BaseModel):
    recon_result: Dict[str, Any] = Field(..., description="Output from /api/v1/reconcile")
    context:      Optional[str]  = Field(None, description="Optional analyst notes")


class ChatRequest(BaseModel):
    question:     str            = Field(..., min_length=1)
    recon_result: Dict[str, Any] = Field(..., description="Output from /api/v1/reconcile")
    history:      List[Dict[str, Any]] = Field(default_factory=list)


class SampleRequest(BaseModel):
    n_transactions: int = Field(80, ge=10, le=500)


@app.get("/api/v1/health", tags=["Health"])
async def health():
    uptime_s = int(time.time() - START_TIME)
    return {
        "status":            "healthy",
        "version":           APP_VERSION,
        "environment":       settings.app_env,
        "uptime_seconds":    uptime_s,
        "gemini_configured": bool(settings.gemini_api_key),
        "timestamp":         datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/v1/reconcile", tags=["Reconciliation"])
async def run_reconciliation(body: ReconcileRequest):
    run_id = str(uuid.uuid4())
    logger.info(
        f"[{run_id}] Reconciliation started — "
        f"{len(body.platform_transactions)} platform txns, "
        f"{len(body.bank_settlements)} bank records"
    )
    recon_log(run_id, "STARTED",
              platform_count=len(body.platform_transactions),
              bank_count=len(body.bank_settlements))
    audit_log("RECONCILIATION_RUN", run_id=run_id,
              platform_count=len(body.platform_transactions),
              bank_count=len(body.bank_settlements))

    try:
        result = reconcile(
            body.platform_transactions,
            body.bank_settlements,
            review_year=body.review_year,
            review_month=body.review_month,
        )
        result["run_id"]    = run_id
        result["completed"] = datetime.now(timezone.utc).isoformat()

        recon_log(run_id, "COMPLETED",
                  gaps_found=result["summary"]["total_gaps"],
                  variance=result["summary"]["total_variance_usd"])
        logger.info(
            f"[{run_id}] Reconciliation complete — "
            f"{result['summary']['total_gaps']} gaps, "
            f"variance USD {result['summary']['total_variance_usd']}"
        )
        return result

    except Exception as exc:
        logger.error(f"[{run_id}] Reconciliation failed: {exc}")
        recon_log(run_id, "FAILED", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Reconciliation error: {exc}")


@app.post("/api/v1/reconcile/sample", tags=["Reconciliation"])
async def reconcile_sample(body: SampleRequest):
    run_id = str(uuid.uuid4())
    logger.info(f"[{run_id}] Generating sample data ({body.n_transactions} txns)")

    data   = generate_data(body.n_transactions)
    result = reconcile(
        data["platform_transactions"],
        data["bank_settlements"],
    )

    result["run_id"]       = run_id
    result["completed"]    = datetime.now(timezone.utc).isoformat()
    result["sample_meta"]  = data["meta"]
    result["planted_gaps"] = data["planted_gaps"]

    recon_log(run_id, "SAMPLE_COMPLETED", gaps_found=result["summary"]["total_gaps"])
    audit_log("SAMPLE_RECONCILIATION_RUN", run_id=run_id, txn_count=body.n_transactions)
    return result


@app.post("/api/v1/ai/analyse", tags=["AI"])
async def ai_analyse(body: AIAnalysisRequest):
    logger.info("AI analysis requested")
    audit_log("AI_ANALYSIS_REQUEST", gap_count=len(body.recon_result.get("gaps", [])))

    analysis = analyse_gaps(
        api_key=settings.gemini_api_key,
        recon_result=body.recon_result,
        context=body.context,
    )
    return {"analysis": analysis, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/api/v1/ai/chat", tags=["AI"])
async def ai_chat(body: ChatRequest):
    logger.info(f"AI chat: {body.question[:80]}")
    audit_log("AI_CHAT_REQUEST", question_preview=body.question[:80])

    answer = chat_with_data(
        api_key=settings.gemini_api_key,
        question=body.question,
        recon_result=body.recon_result,
        history=body.history,
    )
    return {"answer": answer, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/v1/logs/recent", tags=["Admin"])
async def recent_logs(n: int = 50):
    log_dir  = Path(__file__).parent / "logs"
    today    = datetime.now().strftime("%Y-%m-%d")
    log_file = log_dir / f"app-{today}.log"

    if not log_file.exists():
        logs = sorted(log_dir.glob("app-*.log"), reverse=True)
        if not logs:
            return {"lines": [], "message": "No log files found yet"}
        log_file = logs[0]

    lines = log_file.read_text(errors="replace").splitlines()
    return {"file": log_file.name, "total": len(lines), "lines": lines[-n:]}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    logger.warning(f"HTTP {exc.status_code}: {exc.detail} [{request.url.path}]")
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "status_code": exc.status_code},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception at {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "status_code": 500},
    )


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.app_env == "development",
        log_level=settings.log_level.lower(),
        access_log=False,
    )
