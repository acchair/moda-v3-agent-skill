from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Generic, Iterable, TypeVar

T = TypeVar("T")


@dataclass
class CallResult(Generic[T]):
    label: str
    source: str
    ok: bool
    value: T | None = None
    error: str = ""
    elapsed_ms: int = 0
    fetch_state: str = "failed"
    source_chain: list[dict[str, object]] | None = None


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
    saw_empty = False
    chain: list[dict[str, object]] = []
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
                saw_empty = True
                last_error = "empty result"
                print(f"  [source] {label} via {source} empty ({elapsed_ms}ms)")
                chain.append({"source": source, "status": "empty", "error": last_error, "elapsed_ms": elapsed_ms})
                continue
            print(f"  [source] {label} via {source} ok ({elapsed_ms}ms)")
            chain.append({"source": source, "status": "ok", "error": "", "elapsed_ms": elapsed_ms})
            return CallResult(
                label=label,
                source=source,
                ok=True,
                value=value,
                elapsed_ms=elapsed_ms,
                fetch_state="ok",
                source_chain=chain,
            )
        except queue.Empty:
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            last_error = f"timeout after {seconds}s"
            print(f"  [source] {label} via {source} timeout ({elapsed_ms}ms)")
            chain.append({"source": source, "status": "failed", "error": last_error, "elapsed_ms": elapsed_ms})
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"  [source] {label} via {source} failed ({elapsed_ms}ms): {last_error}")
            chain.append({"source": source, "status": "failed", "error": last_error, "elapsed_ms": elapsed_ms})
    return CallResult(
        label=label,
        source=source,
        ok=False,
        error=last_error,
        elapsed_ms=elapsed_ms if "elapsed_ms" in locals() else 0,
        fetch_state="empty" if saw_empty and not any(item["status"] == "failed" for item in chain) else "failed",
        source_chain=chain,
    )


def run_fallback_chain(
    label: str,
    attempts: Iterable[tuple[str, Callable[[], T]]],
    *,
    seconds: int = 12,
    empty: Callable[[T], bool] | None = None,
) -> CallResult[T]:
    """Try semantically equivalent sources and preserve every attempt."""
    chain: list[dict[str, object]] = []
    saw_empty = False
    last_error = ""
    for index, (source, fn) in enumerate(attempts):
        result = run_with_timeout(label, fn, seconds=seconds, source=source, empty=empty)
        chain.extend(result.source_chain or [])
        if result.ok:
            result.fetch_state = "ok" if index == 0 else "fallback_ok"
            result.source_chain = chain
            return result
        saw_empty = saw_empty or result.fetch_state == "empty"
        last_error = result.error
    return CallResult(
        label=label,
        source=chain[-1]["source"] if chain else "",
        ok=False,
        error=last_error,
        fetch_state="empty" if saw_empty and not any(item["status"] == "failed" for item in chain) else "failed",
        source_chain=chain,
    )


def chain_payload(result: CallResult[object]) -> dict[str, object]:
    """Return JSON-safe status fields for structured module payloads."""
    return {
        "fetch_state": result.fetch_state,
        "source": result.source,
        "source_chain": result.source_chain or [],
        "error": result.error or None,
    }


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
