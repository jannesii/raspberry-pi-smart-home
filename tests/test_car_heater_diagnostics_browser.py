"""Diagnostics acceptance through the authenticated page and isolated services."""

import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.parametrize("width", [393, 1440])
def test_authenticated_diagnostics_browser_flow(tmp_path, width):
    pytest.importorskip("playwright")
    scripts = Path(__file__).parent / "browser"
    host_file = tmp_path / "host.json"
    with (tmp_path / "host.log").open("w+") as log:
        host = subprocess.Popen(
            [
                sys.executable,
                str(scripts / "car_heater_sessions_host.py"),
                str(tmp_path),
                "diagnostics",
            ],
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 20
            while not host_file.exists() and host.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            assert host_file.exists(), "Isolated diagnostics host did not start; see host.log"
            result = subprocess.run(
                [
                    sys.executable,
                    str(scripts / "car_heater_diagnostics_flow.py"),
                    str(host_file),
                    str(width),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if "Executable doesn't exist" in result.stderr:
                pytest.skip("Run python -m playwright install chromium")
            log.seek(0)
            assert result.returncode == 0, result.stdout + result.stderr + log.read()[-2000:]
        finally:
            host.terminate()
            host.wait(timeout=10)
