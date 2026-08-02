from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
import time


def _listening(host: str, port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((host, port)) == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the optional DuckDuckGo MCP search service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7070)
    args = parser.parse_args()

    if _listening(args.host, args.port):
        print(f"DuckDuckGo MCP is already running: http://{args.host}:{args.port}/mcp")
        return 0

    uvx = shutil.which("uvx")
    if not uvx:
        print("uvx is unavailable. The pipeline can use its built-in public search fallback.")
        return 0

    command = [
        uvx, "--with", "duckduckgo-mcp-server[browser]", "duckduckgo-mcp-server",
        "--transport", "streamable-http", "--host", args.host, "--port", str(args.port),
    ]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(60):
        if _listening(args.host, args.port):
            print(f"DuckDuckGo MCP started: http://{args.host}:{args.port}/mcp")
            return 0
        time.sleep(2)
    print("DuckDuckGo MCP did not start within 120 seconds; built-in public search remains available.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
