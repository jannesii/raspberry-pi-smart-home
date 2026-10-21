"""Public browser flow in separate processes for Eventlet/asyncio isolation."""

import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.mark.parametrize("width", [393, 1440, 0])
def test_authenticated_session_browser_flow(tmp_path, width):
    pytest.importorskip("playwright")
    scripts = Path(__file__).parent / "browser"
    host_file = tmp_path / "host.json"
    with (tmp_path / "host.log").open("w+") as log:
        host = subprocess.Popen(
            [sys.executable, str(scripts / "car_heater_sessions_host.py"), str(tmp_path)],
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 20
            while not host_file.exists() and host.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            assert host_file.exists(), "Isolated browser host did not start; see host.log"
            result = subprocess.run(
                [
                    sys.executable,
                    str(scripts / "car_heater_sessions_flow.py"),
                    str(host_file),
                    str(width),
                ],
                capture_output=True,
                text=True,
                timeout=90,
            )
            if "Executable doesn't exist" in result.stderr:
                pytest.skip("Run python -m playwright install chromium")
            assert result.returncode == 0, result.stdout + result.stderr
        finally:
            host.terminate()
            host.wait(timeout=10)


@pytest.mark.parametrize("width", [393, 1440])
def test_authenticated_bounded_heating_browser_flow(tmp_path, width):
    pytest.importorskip("playwright")
    scripts = Path(__file__).parent / "browser"
    host_file = tmp_path / "host.json"
    host = subprocess.Popen(
        [sys.executable, str(scripts / "car_heater_sessions_host.py"), str(tmp_path), "bounded"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        while not host_file.exists() and host.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert host_file.exists(), "Isolated bounded host did not start"
        result = subprocess.run(
            [
                sys.executable,
                str(scripts / "car_heater_bounded_flow.py"),
                str(host_file),
                str(width),
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        if "Executable doesn't exist" in result.stderr:
            pytest.skip("Run python -m playwright install chromium")
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        host.terminate()
        host.wait(timeout=10)


@pytest.mark.parametrize("width", [393, 1440])
def test_authenticated_departure_browser_flow(tmp_path, width):
    pytest.importorskip("playwright")
    scripts = Path(__file__).parent / "browser"
    host_file = tmp_path / "host.json"
    with (tmp_path / "host.log").open("w+") as log:
        host = subprocess.Popen(
            [
                sys.executable,
                str(scripts / "car_heater_sessions_host.py"),
                str(tmp_path),
                "departure",
            ],
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 20
            while not host_file.exists() and host.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            assert host_file.exists(), "Isolated departure host did not start; see host.log"
            result = subprocess.run(
                [
                    sys.executable,
                    str(scripts / "car_heater_departure_flow.py"),
                    str(host_file),
                    str(width),
                ],
                capture_output=True,
                text=True,
                timeout=90,
            )
            if "Executable doesn't exist" in result.stderr:
                pytest.skip("Run python -m playwright install chromium")
            assert result.returncode == 0, result.stdout + result.stderr
        finally:
            host.terminate()
            host.wait(timeout=10)


@pytest.mark.parametrize("width", [393, 1440, 0])
def test_authenticated_capture_browser_flow(tmp_path, width):
    pytest.importorskip("playwright")
    scripts = Path(__file__).parent / "browser"
    host_file = tmp_path / "host.json"
    with (tmp_path / "host.log").open("w+") as log:
        host = subprocess.Popen(
            [
                sys.executable,
                str(scripts / "car_heater_sessions_host.py"),
                str(tmp_path),
                "bounded",
            ],
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 20
            while not host_file.exists() and host.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            assert host_file.exists(), "Isolated capture host did not start; see host.log"
            result = subprocess.run(
                [
                    sys.executable,
                    str(scripts / "car_heater_capture_flow.py"),
                    str(host_file),
                    str(width),
                ],
                capture_output=True,
                text=True,
                timeout=180,
            )
            if "Executable doesn't exist" in result.stderr:
                pytest.skip("Run python -m playwright install chromium")
            assert result.returncode == 0, result.stdout + result.stderr
        finally:
            host.terminate()
            host.wait(timeout=10)


@pytest.mark.parametrize("width", [393, 1440, 0])
def test_authenticated_session_review_browser_flow(tmp_path, width):
    pytest.importorskip("playwright")
    scripts = Path(__file__).parent / "browser"
    host_file = tmp_path / "host.json"
    with (tmp_path / "host.log").open("w+") as log:
        host = subprocess.Popen(
            [sys.executable, str(scripts / "car_heater_sessions_host.py"), str(tmp_path), "review"],
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 20
            while not host_file.exists() and host.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            assert host_file.exists(), "Isolated review host did not start; see host.log"
            result = subprocess.run(
                [
                    sys.executable,
                    str(scripts / "car_heater_review_flow.py"),
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


@pytest.mark.parametrize("width", [393, 1440])
def test_authenticated_weather_collection_health_browser_flow(tmp_path, width):
    pytest.importorskip("playwright")
    scripts = Path(__file__).parent / "browser"
    host_file = tmp_path / "host.json"
    with (tmp_path / "host.log").open("w+") as log:
        host = subprocess.Popen(
            [
                sys.executable,
                str(scripts / "car_heater_sessions_host.py"),
                str(tmp_path),
                "weather",
            ],
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 20
            while not host_file.exists() and host.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            assert host_file.exists(), "Isolated weather host did not start; see host.log"
            result = subprocess.run(
                [
                    sys.executable,
                    str(scripts / "car_heater_weather_flow.py"),
                    str(host_file),
                    str(width),
                ],
                capture_output=True,
                text=True,
                timeout=90,
            )
            if "Executable doesn't exist" in result.stderr:
                pytest.skip("Run python -m playwright install chromium")
            assert result.returncode == 0, result.stdout + result.stderr
        finally:
            host.terminate()
            host.wait(timeout=10)


@pytest.mark.parametrize("width", [393, 1440])
def test_authenticated_session_weather_evidence_browser_flow(tmp_path, width):
    pytest.importorskip("playwright")
    scripts = Path(__file__).parent / "browser"
    host_file = tmp_path / "host.json"
    with (tmp_path / "host.log").open("w+") as log:
        host = subprocess.Popen(
            [
                sys.executable,
                str(scripts / "car_heater_sessions_host.py"),
                str(tmp_path),
                "weather_evidence",
            ],
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 20
            while not host_file.exists() and host.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            assert host_file.exists(), "Isolated weather evidence host did not start; see host.log"
            result = subprocess.run(
                [
                    sys.executable,
                    str(scripts / "car_heater_session_weather_flow.py"),
                    str(host_file),
                    str(width),
                ],
                capture_output=True,
                text=True,
                timeout=90,
            )
            if "Executable doesn't exist" in result.stderr:
                pytest.skip("Run python -m playwright install chromium")
            assert result.returncode == 0, result.stdout + result.stderr
        finally:
            host.terminate()
            host.wait(timeout=10)
