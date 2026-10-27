"""Announcement page in a real browser, host and driver in separate processes."""

import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.parametrize("width", [393, 1440])
def test_preview_speaker_test_and_stop_browser_flow(tmp_path, width):
    pytest.importorskip("playwright")
    scripts = Path(__file__).parent / "browser"
    host_file = tmp_path / "host.json"
    with (tmp_path / "host.log").open("w+") as log:
        host = subprocess.Popen(
            [sys.executable, str(scripts / "announcements_host.py"), str(tmp_path)],
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 20
            while not host_file.exists() and host.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            assert host_file.exists(), "Isolated announcement host did not start; see host.log"
            result = subprocess.run(
                [
                    sys.executable,
                    str(scripts / "announcements_flow.py"),
                    str(host_file),
                    str(width),
                ],
                capture_output=True,
                text=True,
                timeout=90,
            )
            if "Executable doesn't exist" in result.stderr:
                pytest.skip("Run python -m playwright install chromium")
            log.seek(0)
            assert result.returncode == 0, result.stdout + result.stderr + log.read()[-3000:]
        finally:
            host.terminate()
            host.wait(timeout=10)


@pytest.mark.parametrize("width", [393, 1440])
def test_one_time_reminder_browser_flow(tmp_path, width):
    pytest.importorskip("playwright")
    scripts = Path(__file__).parent / "browser"
    host_file = tmp_path / "host.json"
    with (tmp_path / "host.log").open("w+") as log:
        host = subprocess.Popen(
            [sys.executable, str(scripts / "announcements_host.py"), str(tmp_path)],
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 20
            while not host_file.exists() and host.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            assert host_file.exists(), "Isolated announcement host did not start; see host.log"
            result = subprocess.run(
                [
                    sys.executable,
                    str(scripts / "announcements_schedule_flow.py"),
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
            assert result.returncode == 0, result.stdout + result.stderr + log.read()[-3000:]
        finally:
            host.terminate()
            host.wait(timeout=10)
