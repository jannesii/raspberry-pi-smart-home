"""Identify the prepared release this code runs from.

``scripts/release.py`` exports a release into ``releases/<candidate>/code`` and
records the revision beside it in ``releases/<candidate>/release.json``.
Readiness checks compare this value, reported by the running process, with the
release they expect, so a stale process or a unit pointing elsewhere is not
mistaken for a successful deployment. A working checkout has no such record.

Importing this module has no side effects.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def release_revision(code_root: Path) -> str | None:
    """Return the release revision for ``code_root``, or None outside a release."""
    try:
        record = json.loads((code_root.resolve().parent / "release.json").read_text())
    except (OSError, ValueError):
        return None
    revision = record.get("revision") if isinstance(record, dict) else None
    return revision if isinstance(revision, str) else None
