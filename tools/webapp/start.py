from __future__ import annotations

import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def find_port(start: int = 8765) -> int:
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("No free port found")


def main() -> None:
    import uvicorn

    port = find_port()
    print(f"莫大 v3 本地工作台: http://127.0.0.1:{port}")
    uvicorn.run("tools.webapp.app:app", host="127.0.0.1", port=port, reload=False)


if __name__ == "__main__":
    main()
