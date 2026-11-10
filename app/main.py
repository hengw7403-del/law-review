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
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
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
DIST_DIR = Path(__file__).resolve().parent.parent / "dist"
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
        try:
            from app.services.agent import AgentManager
            application.state.agent_manager = AgentManager()
        except Exception:
            logger.exception('Agent initialization failed; core review remains available')
            application.state.agent_manager = None
        yield
    finally:
        if getattr(application.state, 'agent_manager', None):
            application.state.agent_manager.stop.set()
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
from app.api.dashboard import router as dashboard_router
app.include_router(dashboard_router)
from app.api.conversations import router as conversations_router
app.include_router(conversations_router)
from app.api.tickets import router as tickets_router
app.include_router(tickets_router)
from app.api.agent import router as agent_router
app.include_router(agent_router)


@app.middleware("http")
async def authenticate_all_routes(request: Request, call_next):
    spa_asset = request.method in {"GET", "HEAD"} and (
        request.url.path == "/workspace" or request.url.path.startswith(("/workspace/", "/assets/")))
    if not spa_asset and request.url.path not in PUBLIC_PATHS and not request.url.path.startswith("/health/"):
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


@app.get("/", include_in_schema=False)
def web_entry():
    return FileResponse(DIST_DIR / "index.html" if (DIST_DIR / "index.html").is_file() else STATIC_DIR / "index.html")


@app.get("/workspace", include_in_schema=False)
@app.get("/workspace/{path:path}", include_in_schema=False)
def spa_entry(path: str = ""):
    if not (DIST_DIR / "index.html").is_file():
        raise HTTPException(503, "前端尚未构建，请执行 npm ci 和 npm run build")
    return FileResponse(DIST_DIR / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets"), check_dir=False), name="spa-assets")
# Legacy HTML stays available; API routes retain precedence.
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="web")
