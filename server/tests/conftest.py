from __future__ import annotations

import os
import sys
from pathlib import Path

# Tests must never reach a real database. ControllerBase builds its engine from
# DATABASE_URL when USE_SQLA_READS is set, and fixtures only fall back to SQLite
# when no engine exists, so a sourced production env would run tests on it.
for _var in ("DATABASE_URL", "USE_SQLA_READS"):
    os.environ.pop(_var, None)

ROOT = Path(__file__).resolve().parents[1]
root_str = str(ROOT)
if root_str not in sys.path:
    sys.path.insert(0, root_str)
