from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Iterator
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")


def shanghai_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(SHANGHAI)
    if now.tzinfo is None:
        return now.replace(tzinfo=SHANGHAI)
    return now.astimezone(SHANGHAI)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


@contextmanager
def _exclusive_lock(path: Path, timeout: float = 30, stale_after: float = 120) -> Iterator[None]:
    started = time.monotonic()
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, str(os.getpid()).encode("ascii"))
            os.close(descriptor)
            break
        except FileExistsError:
            try:
                stale = time.time() - path.stat().st_mtime > stale_after
            except OSError:
                stale = False
            if stale:
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() - started >= timeout:
                raise TimeoutError(f"daily cache lock timeout: {path.name}")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def load_daily_json(
    path: Path,
    fetcher: Callable[[], dict[str, Any]],
    *,
    force_refresh: bool = False,
    now: datetime | None = None,
    lock_timeout: float = 30,
) -> dict[str, Any]:
    """Return one shared refresh record per Shanghai calendar day."""
    checked_at = shanghai_now(now)
    checked_date = checked_at.date().isoformat()
    existing = _read_json(path)
    if not force_refresh and existing and existing.get("checked_date") == checked_date:
        return {**existing, "cache_hit": True}

    lock_path = path.with_suffix(path.suffix + ".lock")
    with _exclusive_lock(lock_path, timeout=lock_timeout):
        existing = _read_json(path)
        if not force_refresh and existing and existing.get("checked_date") == checked_date:
            return {**existing, "cache_hit": True}

        previous_payload = (existing or {}).get("payload")
        try:
            payload = fetcher()
            if not isinstance(payload, dict):
                raise TypeError("daily cache fetcher must return a dict")
            record = {
                "checked_date": checked_date,
                "checked_at": checked_at.isoformat(timespec="seconds"),
                "source_date": payload.get("source_date"),
                "source": payload.get("source") or payload.get("sources"),
                "status": "ok",
                "fetch_state": "ok",
                "usable": True,
                "error": None,
                "payload": payload,
            }
        except Exception as exc:
            fallback = previous_payload if isinstance(previous_payload, dict) else {}
            record = {
                "checked_date": checked_date,
                "checked_at": checked_at.isoformat(timespec="seconds"),
                "source_date": fallback.get("source_date"),
                "source": fallback.get("source") or fallback.get("sources"),
                "status": "fallback" if fallback else "error",
                "fetch_state": "stale" if fallback else "failed",
                "usable": False,
                "error": f"{type(exc).__name__}: {exc}",
                "payload": fallback,
            }
        _atomic_write(path, record)
        return {**record, "cache_hit": False}
