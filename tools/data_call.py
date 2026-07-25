from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass
class CallResult(Generic[T]):
    label: str
    source: str
    ok: bool
    value: T | None = None
    error: str = ""
    elapsed_ms: int = 0


def run_with_timeout(
    label: str,
    fn: Callable[[], T],
    seconds: int = 12,
    source: str = "akshare",
    retries: int = 0,
    empty: Callable[[T], bool] | None = None,
) -> CallResult[T]:
    """Run one data-source call with a short wall-clock timeout."""
    last_error = ""
    attempts = max(1, retries + 1)
    for attempt in range(1, attempts + 1):
        started = time.perf_counter()
        print(f"  [source] {label} via {source} attempt {attempt}/{attempts} ...")
        result_queue: queue.Queue[tuple[str, T | BaseException]] = queue.Queue(maxsize=1)

        def target() -> None:
            try:
                result_queue.put(("ok", fn()), block=False)
            except BaseException as exc:
                result_queue.put(("error", exc), block=False)

        worker = threading.Thread(target=target, daemon=True)
        try:
            worker.start()
            state, payload = result_queue.get(timeout=seconds)
            if state == "error":
                raise payload
            value = payload
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            if empty and empty(value):
                last_error = "empty result"
                print(f"  [source] {label} via {source} empty ({elapsed_ms}ms)")
                continue
            print(f"  [source] {label} via {source} ok ({elapsed_ms}ms)")
            return CallResult(label=label, source=source, ok=True, value=value, elapsed_ms=elapsed_ms)
        except queue.Empty:
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            last_error = f"timeout after {seconds}s"
            print(f"  [source] {label} via {source} timeout ({elapsed_ms}ms)")
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"  [source] {label} via {source} failed ({elapsed_ms}ms): {last_error}")
    return CallResult(label=label, source=source, ok=False, error=last_error, elapsed_ms=elapsed_ms if "elapsed_ms" in locals() else 0)


def run_direct_then_proxy(
    label: str,
    fn: Callable[[], T],
    hook_domains: list[str],
    seconds: int = 12,
    empty: Callable[[T], bool] | None = None,
    proxy_reason: str = "",
) -> CallResult[T]:
    """Try the normal source first; enable the paid proxy only after failure."""
    direct = run_with_timeout(label, fn, seconds=seconds, source="akshare-direct", retries=0, empty=empty)
    if direct.ok:
        return direct

    from tools.data_patch import ensure_akshare_proxy_patch

    ensure_akshare_proxy_patch(hook_domains, reason=proxy_reason or f"{label} direct failed: {direct.error}")
    return run_with_timeout(label, fn, seconds=seconds, source="akshare-proxy", retries=0, empty=empty)


def dataframe_empty(value: object) -> bool:
    return value is None or bool(getattr(value, "empty", False))
