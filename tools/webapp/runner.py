from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import json
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent.parent
JOBS: dict[str, dict[str, Any]] = {}
LOCK = threading.Lock()
MAX_JOB_LOGS = 2000
MAX_COMPLETED_JOBS = 100
PIPELINE_TIMEOUT_SECONDS = 960


def _new_job(kind: str, payload: dict[str, Any]) -> tuple[str, bool]:
    with LOCK:
        if kind == "stock":
            for existing_id, job in JOBS.items():
                if job["kind"] == kind and job["status"] in {"queued", "running"} and job["payload"]["code"] == payload["code"]:
                    return existing_id, False
        job_id = uuid.uuid4().hex[:12]
        JOBS[job_id] = {
            "id": job_id,
            "kind": kind,
            "payload": payload,
            "status": "queued",
            "created_at": time.time(),
            "updated_at": time.time(),
            "modules": {},
            "logs": [],
            "log_seq": 0,
        }
        completed = sorted(
            (job for job in JOBS.values() if job["status"] not in {"queued", "running"}),
            key=lambda job: job["updated_at"],
            reverse=True,
        )
        for old_job in completed[MAX_COMPLETED_JOBS:]:
            JOBS.pop(old_job["id"], None)
    return job_id, True


def _update(job_id: str, **changes: Any) -> None:
    with LOCK:
        JOBS[job_id].update(changes, updated_at=time.time())


def _append_log(job_id: str, module: str, line: str, level: str = "info") -> None:
    text = str(line).rstrip()
    if not text:
        return
    with LOCK:
        job = JOBS[job_id]
        job["log_seq"] += 1
        job["logs"].append(
            {"seq": job["log_seq"], "time": time.strftime("%H:%M:%S"), "module": module, "line": text, "level": level}
        )
        job["logs"] = job["logs"][-MAX_JOB_LOGS:]
        job["updated_at"] = time.time()


def get_job(job_id: str) -> dict[str, Any] | None:
    with LOCK:
        return copy.deepcopy(JOBS.get(job_id))


def get_logs(job_id: str, after: int = 0) -> dict[str, Any] | None:
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None
        return {
            "job_id": job_id,
            "after": after,
            "latest_seq": job["log_seq"],
            "status": job["status"],
            "logs": [entry for entry in job["logs"] if entry["seq"] > after],
        }


def cloakbrowser_status() -> dict[str, Any]:
    try:
        with urlopen("http://localhost:9222/json/version", timeout=2) as response:
            return {"ok": True, "detail": response.read(200).decode("utf-8", errors="replace")}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


def _load_pipeline_result(code: str) -> dict[str, dict[str, Any]]:
    path = ROOT / "knowledge" / "research" / "pipeline" / f"{code}.json"
    if not path.exists():
        return {}
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        str(row.get("label", "module")): {
            "title": str(row.get("label", "module")),
            "status": "success" if row.get("ok") else "failed",
            **row,
        }
        for row in rows
    }


def _run_pipeline(job_id: str, code: str, name: str, module_prefix: str = "") -> bool:
    command = [sys.executable, "-u", "tools/run_pipeline.py", "--stock", code, "--name", name or code]
    _append_log(job_id, module_prefix or "pipeline", f"开始分析 {name or code}({code})", "system")
    current_module = module_prefix or "pipeline"
    try:
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
        assert process.stdout is not None
        deadline = time.time() + PIPELINE_TIMEOUT_SECONDS
        for line in process.stdout:
            match = re.match(r"\[([^]]+)]", line.strip())
            if match:
                current_module = f"{module_prefix}{match.group(1)}"
            _append_log(job_id, current_module, line)
            if time.time() > deadline:
                process.kill()
                raise TimeoutError(f"pipeline timeout after {PIPELINE_TIMEOUT_SECONDS}s")
        returncode = process.wait(timeout=10)
    except Exception as exc:
        _append_log(job_id, current_module, f"分析失败: {exc}", "error")
        return False

    modules = _load_pipeline_result(code)
    with LOCK:
        if module_prefix:
            JOBS[job_id]["modules"].update({f"{module_prefix}{key}": value for key, value in modules.items()})
        else:
            JOBS[job_id]["modules"] = modules
    _append_log(job_id, module_prefix or "pipeline", f"分析结束: returncode={returncode}", "system" if returncode == 0 else "warn")
    return returncode == 0


def _run_stock_job(job_id: str, code: str, name: str) -> None:
    _update(job_id, status="running")
    ok = _run_pipeline(job_id, code, name)
    modules = get_job(job_id).get("modules", {}) if get_job(job_id) else {}
    any_success = any(item.get("status") == "success" for item in modules.values())
    _update(job_id, status="success" if ok else "success_with_warnings" if any_success else "failed")


def _run_batch_job(job_id: str, stocks: list[dict[str, str]]) -> None:
    _update(job_id, status="running")
    def run(stock: dict[str, str]) -> bool:
        code = str(stock.get("code", "")).strip()
        name = str(stock.get("name", "") or code).strip()
        return _run_pipeline(job_id, code, name, f"{code}:")

    with ThreadPoolExecutor(max_workers=min(2, len(stocks))) as executor:
        results = list(executor.map(run, stocks))
    _update(job_id, status="success" if results and all(results) else "success_with_warnings" if any(results) else "failed")


def start_stock_job(code: str, name: str = "") -> str:
    code = str(code).strip()
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("code must be a 6-digit A-share code")
    job_id, created = _new_job("stock", {"code": code, "name": name or code})
    if created:
        threading.Thread(target=_run_stock_job, args=(job_id, code, name or code), daemon=True).start()
    return job_id


def start_batch_job(stocks: list[dict[str, str]]) -> str:
    cleaned = []
    seen: set[str] = set()
    for stock in stocks[:20]:
        code = str(stock.get("code", "")).strip()
        if not re.fullmatch(r"\d{6}", code):
            raise ValueError(f"invalid A-share code: {code}")
        if code in seen:
            continue
        seen.add(code)
        cleaned.append({"code": code, "name": str(stock.get("name", "") or code).strip()})
    if not cleaned:
        raise ValueError("stocks required")
    job_id, _ = _new_job("batch", {"stocks": cleaned})
    threading.Thread(target=_run_batch_job, args=(job_id, cleaned), daemon=True).start()
    return job_id
