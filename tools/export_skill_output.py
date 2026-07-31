from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path


def safe_name(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).strip(" ._") or "analysis"


def export(text: str, stock: str = "", name: str = "") -> Path:
    if not text.strip():
        raise ValueError("output text is empty")
    output_dir = Path(os.environ.get("MODA_OUTPUT_DIR", Path(__file__).resolve().parent.parent / "knowledge" / "output"))
    output_dir.mkdir(parents=True, exist_ok=True)
    label = "_".join(safe_name(value) for value in (name, stock) if value.strip()) or "analysis"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = output_dir / f"moda-v4_{label}_{timestamp}.txt"
    path.write_text(text, encoding="utf-8-sig")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the final moda-v4 response")
    parser.add_argument("--stock", default="")
    parser.add_argument("--name", default="")
    parser.add_argument("--input", type=Path, help="Read final response from a UTF-8 text file; defaults to stdin")
    args = parser.parse_args()
    text = args.input.read_text(encoding="utf-8") if args.input else sys.stdin.read()
    print(export(text, args.stock, args.name))


if __name__ == "__main__":
    main()
