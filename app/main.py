"""FastAPI entry point for parsing, retrieval, judging and queued review tasks."""

from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Union
import logging
import os
from pathlib import Path
import html
import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.config import load_environment
from app.api.parse import router as parse_router
from app.api.search import router as search_router
from app.services.search import build_search_index
from app.api.judge import router as judge_router
from app.services.llm import CallBudget, LLMClient
from app.services.risk_judge import RiskJudge
from app.api.reviews import router as reviews_router
from app.api.auth import router as auth_router
from app.api.contracts import router as contracts_router
from app.api.admin import router as admin_router
from app.services.auth import authenticate_header, signing_key


logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent / "static"
PUBLIC_PATHS = {"/", "/index.html", "/login.html", "/contract-list.html", "/contract-detail.html",
                "/result.html", "/report.html", "/result.js", "/report.js", "/markdown.js",
                "/style.css", "/api.js", "/nav.js", "/login.js", "/contract-list.js", "/contract-detail.js",
                "/auth/login", "/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"}


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    load_environment()
    db.init_db()
    signing_key()
    try:
        with db.SessionLocal() as session:
            application.state.search_index = build_search_index(session)
        limit = os.environ.get("LLM_TEST_CALL_LIMIT")
        application.state.llm_client = LLMClient(db.SessionLocal, budget=CallBudget(int(limit)) if limit else None)
        application.state.risk_judge = RiskJudge(application.state.search_index, db.SessionLocal,
                                                application.state.llm_client)
        yield
    finally:
        if hasattr(application.state, "llm_client"):
            application.state.llm_client.close()
        db.engine.dispose()


app = FastAPI(title="Law Contract Review", lifespan=lifespan, docs_url=None, redoc_url=None)
app.include_router(parse_router)
app.include_router(search_router)
app.include_router(judge_router)
app.include_router(reviews_router)
app.include_router(auth_router)
app.include_router(contracts_router)
app.include_router(admin_router)


@app.middleware("http")
async def authenticate_all_routes(request: Request, call_next):
    if request.url.path not in PUBLIC_PATHS and not request.url.path.startswith("/health/"):
        try:
            request.state.user = await run_in_threadpool(authenticate_header, request.headers.get("Authorization"))
        except HTTPException as error:
            return JSONResponse(status_code=error.status_code, content={"detail": error.detail}, headers=error.headers)
        except SQLAlchemyError:
            return JSONResponse(status_code=503, content={"detail": "认证服务暂不可用"})
    return await call_next(request)


@app.get("/docs", include_in_schema=False, response_class=HTMLResponse)
@app.get("/redoc", include_in_schema=False, response_class=HTMLResponse)
def offline_docs():
    # Native, offline API reference: no default Swagger/ReDoc CDN dependencies.
    entries = []
    for path, methods in app.openapi()["paths"].items():
        for method, operation in methods.items():
            entries.append("<details class='panel'><summary>{} {} — {}</summary><pre>{}</pre></details>".format(
                html.escape(method.upper()), html.escape(path), html.escape(operation.get("summary", "")),
                html.escape(json.dumps(operation, ensure_ascii=False, indent=2))))
    return HTMLResponse("<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
                        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                        "<title>离线接口文档</title><link rel='stylesheet' href='/style.css'>"
                        "<script src='/api.js' defer></script><script src='/nav.js' defer></script></head>"
                        "<body><nav id='nav' aria-label='主导航'></nav><main><h1>离线接口文档</h1>"
                        "<p class='muted'>除登录、健康检查、页面和本文档外，业务接口需要 Authorization: Bearer &lt;access_token&gt;。</p>"
                        "<p><a href='/openapi.json'>查看完整 OpenAPI JSON</a> · 本页不依赖外网资源。</p>" +
                        "".join(entries) + "</main></body></html>")


@app.get("/health/live")
def health_live() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", response_model=None)
def health_ready() -> Union[Dict[str, str], JSONResponse]:
    try:
        db.check_database_ready()
    except SQLAlchemyError:
        logger.warning("Database readiness check failed")
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ok"}


# Mount last: API routes retain precedence over the root static application.
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="web")
