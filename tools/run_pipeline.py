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
    "business_data": "business_data",
    "tdx_analysis": "tdx_analysis",
    "scoring": "scoring",
    "announcements": "announcements",
    "market_events": "market_events",
    "popularity": "popularity",
    "social_sentiment": "social_sentiment",
    "congestion": "congestion",
    "supply_demand": "supply_demand",
    "macro_policy": "macro_policy",
    "web_research": "web_research",
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


def run_collectors(collectors: list[tuple]) -> list[dict]:
    with ThreadPoolExecutor(max_workers=min(6, len(collectors))) as executor:
        return list(executor.map(lambda module: run_module(*module), collectors))


def current_context(code: str, directories: tuple[str, ...], since: float) -> tuple[str, str]:
    from tools.scoring.evidence import build_evidence, read_reports

    evidence = build_evidence(code, code, read_reports(code, directories, since))
    industry = str(evidence.get("industry") or "综合")
    values = [
        industry,
        str(evidence.get("main_business") or ""),
        " ".join(str(item) for item in evidence.get("business_items", [])),
        " ".join(str(item) for item in evidence.get("concepts", [])),
    ]
    return industry, " ".join(value for value in values if value).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the moda-v4 structured A-share pipeline")
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
    first_wave = [
        ("finance_data", "tools/akshare/finance_data.py", [*common, *kline_args], 180),
        ("business_data", "tools/akshare/business_data.py", common, 60),
        ("tdx_analysis", "tools/tdx/analyzer.py", [*common, *kline_args], 120),
        ("announcements", "tools/akshare/announcements.py", [*common, "--days", "180"], 120),
        ("market_events", "tools/akshare/market_events.py", common, 90),
        ("popularity", "tools/akshare/popularity.py", common, 30),
        ("social_sentiment", "tools/akshare/social_sentiment.py", common, 45),
        ("congestion", "tools/akshare/congestion.py", common, 90),
    ]

    results = run_collectors(first_wave)
    first_sources = tuple(result["label"] for result in results if result.get("ok"))
    industry, context = current_context(code, first_sources, started_ts)
    second_wave = [
        ("supply_demand", "tools/scoring/supply_demand.py", [*common, "--context", context], 150),
        ("macro_policy", "tools/akshare/macro_policy.py", [*common, "--industry", industry], 150),
        ("web_research", "tools/scoring/web_research.py", [*common, "--context", context], 180),
    ]
    results.extend(run_collectors(second_wave))
    successful_sources = ",".join(result["label"] for result in results if result.get("ok"))
    requested_sources = ",".join([module[0] for module in first_wave + second_wave])
    scoring_args = [*common, "--sources", successful_sources, "--requested-sources", requested_sources, "--since", str(started_ts)]
    results.append(run_module("scoring", "tools/scoring/grader.py", scoring_args))
    output = ROOT / "knowledge/research/pipeline"
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"{code}.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = sum(item["ok"] for item in results)
    print(f"\nPipeline: {passed}/{len(results)} modules succeeded -> {path}")
    if not results[-1].get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
