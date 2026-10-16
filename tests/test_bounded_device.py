"""Run shipped firmware executor and Shelly script together, with isolated RPC/time."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_device_execution_survives_router_loss_without_remote_off(tmp_path):
    compiler = shutil.which("g++")
    node = shutil.which("node")
    json_include = Path("ESP32C3-Car-Heater/.pio/libdeps/native/ArduinoJson/src")
    if not compiler or not node or not json_include.exists():
        pytest.skip("Requires g++, node and ArduinoJson installed by pio test -e native")
    driver = tmp_path / "relay_executor"
    subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-I" + str(json_include),
            "-IESP32C3-Car-Heater/lib/BoundedRelay/src",
            "tests/device/relay_executor_driver.cpp",
            "-o",
            str(driver),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    result = subprocess.run(
        [node, "--test", "tests/device/bounded_relay.test.cjs"],
        env={**os.environ, "RELAY_EXECUTOR_DRIVER": str(driver)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
