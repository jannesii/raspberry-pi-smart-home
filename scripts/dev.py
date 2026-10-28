#!/usr/bin/env python3
"""Run the isolated development instance of this checkout in the foreground.

    .venv/bin/python scripts/dev.py run --env-file ~/.config/jannenkoti-dev/dev.env

The app starts with only the development environment file plus a few
process basics (PATH, HOME, locale), never the caller's environment or this
checkout's ``jannenkoti.env``. It refuses to start when a target is missing or
is shared with the production environment reference. This never restarts or
reconfigures host services.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values

from app.environment import (
    APP_ENV_VAR,
    DEVELOPMENT,
    development_problems,
    production_overlap_problems,
)

logger = logging.getLogger("dev")

DEFAULT_PRODUCTION_ENV = Path("/home/jannesi/Code/server/jannenkoti.env")
DEFAULT_PORT = "5557"
# Only process basics pass through; everything else must come from the env file.
INHERITED_VARIABLES = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR")
REFUSED = 2


def _read_env_file(path: Path) -> dict[str, str] | None:
    if not path.is_file():
        return None
    try:
        values = dotenv_values(path)
    except OSError:
        return None
    return {key: value for key, value in values.items() if value is not None}


def _refuse(*reasons: str) -> int:
    print("Development launch refused:", file=sys.stderr)
    for reason in reasons:
        print(f"  - {reason}", file=sys.stderr)
    return REFUSED


def run(args: argparse.Namespace) -> int:
    dev_values = _read_env_file(args.env_file)
    if dev_values is None:
        return _refuse(f"cannot read the development environment file {args.env_file}")
    production_values = _read_env_file(args.production_env)
    if production_values is None:
        return _refuse(
            f"cannot read the production environment reference {args.production_env}; "
            "pass --production-env so shared targets can be detected"
        )
    if dev_values.get(APP_ENV_VAR, DEVELOPMENT).strip().lower() != DEVELOPMENT:
        return _refuse(f"{APP_ENV_VAR} in the development environment file must be development")

    env = {name: os.environ[name] for name in INHERITED_VARIABLES if name in os.environ}
    env.update(dev_values)
    env[APP_ENV_VAR] = DEVELOPMENT
    env.setdefault("PORT", DEFAULT_PORT)

    problems = development_problems(env)
    problems += [
        p for p in production_overlap_problems(env, production_values) if p not in problems
    ]
    if problems:
        return _refuse(*problems)

    bind = f"127.0.0.1:{env['PORT']}"
    logger.info("Starting development app from %s on http://%s", ROOT, bind)
    os.chdir(ROOT)
    argv = [
        sys.executable,
        "-m",
        "gunicorn",
        "--workers",
        "1",
        "--worker-class",
        "eventlet",
        "--bind",
        bind,
        "--timeout",
        "120",
        "run:app",
    ]
    os.execve(sys.executable, argv, env)
    return 0  # pragma: no cover - execve does not return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="run the development app in the foreground")
    run_parser.add_argument("--env-file", type=Path, required=True)
    run_parser.add_argument("--production-env", type=Path, default=DEFAULT_PRODUCTION_ENV)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="[dev] %(message)s")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
