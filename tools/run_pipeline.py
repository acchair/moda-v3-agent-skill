from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CACHE_ROOT = ROOT / "knowledge" / "research" / "pipeline" / "cache"
REPORT_DIRS = {
    "finance_data": "finance_data",
    "tdx_analysis": "tdx_analysis", "scoring": "scoring",
    "announcements": "announcements",
}

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def run_module(label: str, script: str, args: list[str], timeout: int = 180) -> dict:
    command = [sys.executable, str(ROOT / script), *args]
    print(f"\n[{label}] {' '.join(command)}")
    started = datetime.now()
    started_ts = time.time()
    try:
        result = subprocess.run(command, cwd=ROOT, timeout=timeout, check=False)
        code = args[args.index("--stock") + 1]
        report = ROOT / "knowledge/research" / REPORT_DIRS[label] / f"{code}.md"
        fresh = report.exists() and report.stat().st_mtime >= started_ts - 1
        return {"label": label, "ok": result.returncode == 0 and fresh, "returncode": result.returncode,
                "report_fresh": fresh,
                "coverage": _report_coverage(label, report) if fresh else 0,
                "elapsed_seconds": round((datetime.now() - started).total_seconds(), 1)}
    except subprocess.TimeoutExpired:
        return {"label": label, "ok": False, "error": f"timeout after {timeout}s"}


def _report_coverage(label: str, path: Path) -> int:
    """Report comparable coverage without making missing data a hard failure."""
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8", errors="replace")
    if label == "finance_data":
        return sum(marker in text and "无数据" not in text[text.find(marker):text.find(marker) + 160]
                   for marker in ("实时行情", "公司信息", "财务摘要", "近期行情", "同行估值"))
    if label == "scoring":
        return sum(bool(line.strip()) and line.startswith("| F") for line in text.splitlines())
    if label == "announcements":
        return int("最新公告" in text) + int("投资者互动问答" in text)
    return int("评分:" in text or "ALPHA-SOROS" in text)


def prepare_kline(code: str) -> Path | None:
    try:
        from tools.providers.easy_tdx_provider import fetch_kline_daily

        frame = fetch_kline_daily(code)
        if frame.empty:
            return None
        CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        path = CACHE_ROOT / f"{code}.csv"
        frame.to_csv(path, index=False)
        print(f"[kline] easy_tdx -> {len(frame)} rows")
        return path
    except Exception as exc:
        print(f"[kline] shared cache unavailable: {type(exc).__name__}: {exc}")
        return None


def run_collectors(collectors: list[tuple[str, str, list[str]]]) -> list[dict]:
    with ThreadPoolExecutor(max_workers=len(collectors)) as executor:
        return list(executor.map(lambda module: run_module(*module), collectors))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the moda-v3 non-web pipeline")
    parser.add_argument("--stock", required=True)
    parser.add_argument("--name", default="")
    args = parser.parse_args()
    code = args.stock.strip()
    if len(code) != 6 or not code.isdigit():
        parser.error("--stock must be a 6-digit A-share code")

    started_ts = time.time()
    common = ["--stock", code, "--name", args.name or code]
    kline_path = prepare_kline(code)
    kline_args = ["--kline-file", str(kline_path)] if kline_path else []
    collectors = [
        ("finance_data", "tools/akshare/finance_data.py", [*common, *kline_args]),
        ("tdx_analysis", "tools/tdx/analyzer.py", [*common, *kline_args]),
        ("announcements", "tools/akshare/announcements.py", [*common, "--days", "30"]),
    ]

    results = run_collectors(collectors)
    successful_sources = ",".join(result["label"] for result in results if result.get("ok"))
    scoring_args = [*common, "--sources", successful_sources, "--since", str(started_ts)]
    results.append(run_module("scoring", "tools/scoring/grader.py", scoring_args))
    output = ROOT / "knowledge/research/pipeline"
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"{code}.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = sum(item["ok"] for item in results)
    print(f"\nPipeline: {passed}/{len(results)} modules succeeded -> {path}")
    if passed != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
