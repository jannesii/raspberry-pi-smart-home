from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple


LEVEL_NAMES = ["", "NOTSET", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
ALLOWED_LEVELS = set(LEVEL_NAMES)


def _level_name(level: int) -> str:
    try:
        return logging.getLevelName(level)
    except Exception:
        return str(level)


def _parse_level_name(name: str) -> Optional[int]:
    n = (name or "").strip().upper()
    if not n:
        return None
    if n not in ALLOWED_LEVELS:
        return None
    if n == "":
        return None
    try:
        return int(logging._nameToLevel.get(n, logging.NOTSET))  # type: ignore[attr-defined]
    except Exception:
        return None


def _handler_target(handler: logging.Handler) -> str:
    # Provide a stable-ish description so overrides can be persisted.
    try:
        base = getattr(handler, "baseFilename", None)
        if base:
            return str(base)
    except Exception:
        pass
    try:
        stream = getattr(handler, "stream", None)
        if stream is not None:
            name = getattr(stream, "name", None)
            if name:
                return str(name)
            return str(stream)
    except Exception:
        pass
    return ""


def handler_key(handler: logging.Handler) -> str:
    t = handler.__class__.__name__
    target = _handler_target(handler)
    return f"{t}:{target}" if target else t


@dataclass(frozen=True)
class LoggerRow:
    name: str
    explicit_level: str
    effective_level: str
    propagate: bool
    handler_count: int


@dataclass
class HandlerRow:
    key: str
    level: str
    class_name: str
    attached_to: List[str]


def _iter_loggers() -> Iterable[Tuple[str, logging.Logger]]:
    root = logging.getLogger()
    yield ("root", root)
    mgr = logging.Logger.manager  # type: ignore[attr-defined]
    for name, obj in getattr(mgr, "loggerDict", {}).items():
        if isinstance(obj, logging.Logger):
            yield (name, obj)


def snapshot_logging_state() -> Dict[str, Any]:
    rows: List[LoggerRow] = []
    handler_map: Dict[int, HandlerRow] = {}

    for name, lg in _iter_loggers():
        try:
            explicit = _level_name(int(getattr(lg, "level", 0)))
        except Exception:
            explicit = "NOTSET"
        try:
            effective = _level_name(int(lg.getEffectiveLevel()))
        except Exception:
            effective = explicit
        try:
            propagate = bool(getattr(lg, "propagate", True))
        except Exception:
            propagate = True
        try:
            handlers = list(getattr(lg, "handlers", []) or [])
        except Exception:
            handlers = []

        rows.append(
            LoggerRow(
                name=name,
                explicit_level=explicit,
                effective_level=effective,
                propagate=propagate,
                handler_count=len(handlers),
            )
        )

        for h in handlers:
            hid = id(h)
            if hid not in handler_map:
                handler_map[hid] = HandlerRow(
                    key=handler_key(h),
                    level=_level_name(int(getattr(h, "level", 0))),
                    class_name=h.__class__.__name__,
                    attached_to=[name],
                )
            else:
                handler_map[hid].attached_to.append(name)

    # Stable ordering for UI
    rows.sort(key=lambda r: (r.name != "root", r.name))
    handler_rows = list(handler_map.values())
    handler_rows.sort(key=lambda r: r.key)

    root_logger = logging.getLogger()
    return {
        "root_level": _level_name(int(getattr(root_logger, "level", 0))),
        "levels": LEVEL_NAMES,
        "loggers": [r.__dict__ for r in rows],
        "handlers": [h.__dict__ for h in handler_rows],
    }


def apply_logging_control_config(cfg: Dict[str, Any]) -> None:
    """Apply logging overrides to the current process.

    Note: for Gunicorn, each worker is a separate process; overrides must be
    applied in every worker (this app typically uses 1 worker).
    """
    if not isinstance(cfg, dict):
        return

    # Root logger level
    root_level_name = str(cfg.get("root_level") or "").strip()
    root_level = _parse_level_name(root_level_name)
    if root_level is not None:
        logging.getLogger().setLevel(root_level)

    # Per-logger levels
    logger_levels = cfg.get("logger_levels") or {}
    if isinstance(logger_levels, dict):
        for name, level_name in logger_levels.items():
            if not isinstance(name, str):
                continue
            lvl = _parse_level_name(str(level_name))
            if lvl is None:
                continue
            logging.getLogger(name).setLevel(lvl)

    # Per-handler levels (match by computed handler_key)
    handler_levels = cfg.get("handler_levels") or {}
    if isinstance(handler_levels, dict) and handler_levels:
        # Build runtime handler list once
        runtime_handlers: List[logging.Handler] = []
        for _, lg in _iter_loggers():
            try:
                runtime_handlers.extend(list(getattr(lg, "handlers", []) or []))
            except Exception:
                pass
        for h in runtime_handlers:
            k = handler_key(h)
            if k not in handler_levels:
                continue
            lvl = _parse_level_name(str(handler_levels.get(k)))
            if lvl is None:
                continue
            try:
                h.setLevel(lvl)
            except Exception:
                pass
