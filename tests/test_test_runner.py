"""The parallel runner must preserve collection and propagate failing checks."""

import json
import os
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "test.py"


def run(tmp_path, source, *options):
    suite = tmp_path / "test_synthetic.py"
    suite.write_text(source)
    output = tmp_path / "results"
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--workers",
            "2",
            "--output-dir",
            str(output),
            *options,
            str(suite),
        ],
        # Do not load unrelated third-party pytest plugins in this synthetic suite.
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True,
        text=True,
        timeout=40,
    )
    return result, output


def test_every_collected_case_runs_once_and_failure_is_reported(tmp_path):
    result, output = run(
        tmp_path,
        """import pytest
@pytest.mark.parametrize("value", [0, 1, 2, 3])
def test_case(value):
    assert value != 1
""",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    cases = [
        case
        for xml in output.glob("worker-*.xml")
        for case in ElementTree.parse(xml).iter("testcase")
    ]
    assert sorted(case.attrib["name"] for case in cases) == [
        f"test_case[{value}]" for value in range(4)
    ]
    assert sum(case.find("failure") is not None for case in cases) == 1
    summary = json.loads((output / "run.json").read_text())
    assert summary["collected"] == 4
    assert summary["exit_code"] == 1
    assert all((output / f"worker-{n}.log").exists() for n in (1, 2))


def test_selection_runs_only_the_selected_cases(tmp_path):
    result, output = run(
        tmp_path,
        """def test_selected():
    pass
def test_unselected():
    assert False
""",
        "-k",
        "not unselected",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((output / "run.json").read_text())["collected"] == 1


def test_collection_errors_stop_before_any_worker_runs(tmp_path):
    result, output = run(tmp_path, 'raise RuntimeError("broken collection")\n')
    assert result.returncode == 2, result.stdout + result.stderr
    assert "broken collection" in (output / "collection.log").read_text()
    assert not list(output.glob("worker-*.xml"))


def test_empty_selection_reports_pytest_no_tests_status(tmp_path):
    result, output = run(tmp_path, "def test_present():\n    pass\n", "-k", "missing")
    assert result.returncode == 5, result.stdout + result.stderr
    assert json.loads((output / "run.json").read_text())["exit_code"] == 5
    assert not list(output.glob("worker-*.xml"))


def check_interruption(tmp_path, signal_value, exit_code):
    import time

    tmp_path.mkdir()

    suite = tmp_path / "test_wait.py"
    ready = tmp_path / "ready"
    suite.write_text(
        f"import time\nfrom pathlib import Path\ndef test_wait():\n"
        f"    Path({str(ready)!r}).touch()\n    time.sleep(30)\n"
    )
    output = tmp_path / "results"
    process = subprocess.Popen(
        [sys.executable, str(RUNNER), "--workers", "1", "--output-dir", str(output), str(suite)],
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists(), "synthetic worker did not reach the test"
        process.send_signal(signal_value)
        stdout, _ = process.communicate(timeout=15)
        assert process.returncode == exit_code, stdout
        assert json.loads((output / "run.json").read_text())["exit_code"] == exit_code
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_interrupt_stops_running_workers_and_records_interruption(tmp_path):
    import signal

    check_interruption(tmp_path / "interrupt", signal.SIGINT, 130)
    check_interruption(tmp_path / "terminate", signal.SIGTERM, 143)
