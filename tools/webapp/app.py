from __future__ import annotations

import re
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.requests import Request

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.data_patch import apply_data_patches, patch_status, proxy_health_check
from tools.providers.a_stock_data_provider import health_check as a_stock_data_health_check
from tools.providers.easy_tdx_provider import health_check as easy_tdx_health_check
from tools.webapp import chain_db, dashboard, reports, runner, workbench

BASE = Path(__file__).resolve().parent
app = FastAPI(title="莫大 v3 A股研究工作台")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))


class AnalyzeRequest(BaseModel):
    code: str
    name: str = ""


class BatchRequest(BaseModel):
    stocks: list[dict[str, str]]


class PoolUpdate(BaseModel):
    state: str
    note: str = Field(default="", max_length=500)


def _code(value: str) -> str:
    code = str(value or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise HTTPException(status_code=400, detail="code must be a 6-digit A-share code")
    return code


@app.on_event("startup")
def startup() -> None:
    apply_data_patches()
    workbench.ensure_db()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/dashboard")
def market_dashboard_redirect():
    return RedirectResponse(url="/#market-pressure", status_code=307)


@app.get("/api/status")
def status(refresh_proxy: bool = False):
    patch = patch_status()
    if refresh_proxy:
        patch["proxy_health"] = proxy_health_check(verbose=False)
    return {
        "patch": patch,
        "proxy_health": patch.get("proxy_health"),
        "cloakbrowser": runner.cloakbrowser_status(),
        "easy_tdx": easy_tdx_health_check(),
        "a_stock_data": a_stock_data_health_check(),
    }


@app.get("/api/search")
def search(q: str, limit: int = 20):
    return workbench.search_companies(q, limit)


@app.get("/api/dashboard/market")
def market_dashboard_data(days: int = 20, refresh: bool = False):
    return dashboard.get_market_dashboard(days=days, refresh=refresh)


@app.get("/api/chain/search")
def chain_search(q: str, limit: int = 20):
    return chain_db.search(q, limit)


@app.get("/api/chain/stock/{code}")
def chain_stock(code: str):
    return chain_db.stock_chain(_code(code))


@app.get("/api/chain/industry")
def chain_industry(name: str, limit: int = 30):
    return chain_db.industry_chain(name, limit)


@app.get("/api/chain/nav")
def chain_nav():
    return chain_db.industry_nav()


@app.get("/api/pool")
def pool(query: str = "", industry: str = "", state: str = "", limit: int = 100, offset: int = 0):
    return workbench.get_pool(query=query, industry=industry, state=state, limit=limit, offset=offset)


@app.put("/api/pool/{code}")
def update_pool(code: str, update: PoolUpdate):
    try:
        return workbench.put_pool_entry(_code(code), update.state, update.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/pool/{code}")
def delete_pool(code: str):
    workbench.delete_pool_entry(_code(code))
    return {"ok": True}


@app.get("/api/quotes")
def quotes(codes: str = "", refresh: bool = False):
    try:
        return workbench.get_quotes([code for code in codes.split(",") if code], refresh=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/discovery")
def discovery(refresh: bool = False, limit: int = 30):
    return workbench.get_discovery(refresh=refresh, limit=limit)


@app.get("/api/market-pressure")
def market_pressure(days: int = 60, refresh: bool = False):
    return workbench.get_market_pressure(days=days, refresh=refresh)


@app.post("/api/analyze/stock")
def analyze_stock(request: AnalyzeRequest):
    try:
        job_id = runner.start_stock_job(_code(request.code), request.name.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"job_id": job_id}


@app.post("/api/analyze/batch")
def analyze_batch(request: BatchRequest):
    try:
        return {"job_id": runner.start_batch_job(request.stocks)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = runner.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.get("/api/jobs/{job_id}/logs")
def get_job_logs(job_id: str, after: int = 0):
    logs = runner.get_logs(job_id, after)
    if logs is None:
        raise HTTPException(status_code=404, detail="job not found")
    return logs


@app.get("/api/reports/{code}")
def get_reports(code: str):
    code = _code(code)
    return {"code": code, "summary": reports.extract_score_summary(code), "reports": reports.read_reports(code)}
