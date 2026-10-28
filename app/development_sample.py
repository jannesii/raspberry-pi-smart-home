"""Minimal deterministic synthetic data for the development database.

``scripts/dev.py`` runs this module after setup and reset, inside the selected
worktree with only the development environment. It creates the dedicated
development login and a few clearly synthetic readings, never production
accounts, schedules or data. Add fixtures here only when a feature needs them,
and keep them idempotent: setup may run this again on existing data.
"""

from __future__ import annotations

import logging
import os
import sys

from .environment import (
    DEVELOPMENT,
    EnvironmentConfigError,
    development_problems,
    selected_environment,
)

logger = logging.getLogger(__name__)

# (location, temperature °C, relative humidity %)
SAMPLE_READINGS = (
    ("Kehitys olohuone", 21.5, 40.0),
    ("Kehitys makuuhuone", 19.0, 45.0),
    ("Kehitys ulkona", -4.5, 85.0),
)


def seed(ctrl, username: str, password: str) -> None:
    ctrl.register_user(username, password=password, is_admin=True, is_root_admin=True)
    ctrl.set_user_admin_flags(username, is_admin=True, is_root_admin=True)
    known = {row["location"] for row in ctrl.get_unique_locations()}
    added = 0
    for location, temperature, humidity in SAMPLE_READINGS:
        if location not in known:
            ctrl.record_esp32_temphum(location, temperature, humidity)
            added += 1
    logger.info("Development sample ready for login %s (%d readings added)", username, added)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[sample] %(message)s")
    try:
        if selected_environment() != DEVELOPMENT:
            raise EnvironmentConfigError("sample data is only seeded with APP_ENV=development")
    except EnvironmentConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    problems = development_problems(os.environ)
    if problems:
        print("Sample data refused: " + "; ".join(problems), file=sys.stderr)
        return 2

    from .core import Controller

    ctrl = Controller(os.environ["DB_PATH"])
    if ctrl._sa_engine is None:
        print(
            "Sample data needs USE_SQLA_READS=1 and the development DATABASE_URL", file=sys.stderr
        )
        return 1
    seed(ctrl, os.environ["WEB_USERNAME"], os.environ["WEB_PASSWORD"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
