from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIRS = {
    "finance_data": "finance_data", "finance_deep": "finance_deep",
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
                "elapsed_seconds": round((datetime.now() - started).total_seconds(), 1)}
    except subprocess.TimeoutExpired:
        return {"label": label, "ok": False, "error": f"timeout after {timeout}s"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the moda-v3 non-web pipeline")
    parser.add_argument("--stock", required=True)
    parser.add_argument("--name", default="")
    args = parser.parse_args()
    code = args.stock.strip()
    if len(code) != 6 or not code.isdigit():
        parser.error("--stock must be a 6-digit A-share code")

    common = ["--stock", code, "--name", args.name or code]
    modules = [
        ("finance_data", "tools/akshare/finance_data.py", common),
        ("finance_deep", "tools/baostock/finance_deep.py", common),
        ("tdx_analysis", "tools/tdx/analyzer.py", common),
        ("announcements", "tools/akshare/announcements.py", [*common, "--days", "30"]),
        ("scoring", "tools/scoring/grader.py", common),
    ]

    results = [run_module(*module) for module in modules]
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
