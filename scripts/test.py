#!/usr/bin/env python3
"""Run the complete pytest selection in independent process shards.

Eventlet patches threading when app is imported. Separate pytest invocations
avoid changing execnet's already-running native threads, as happens with xdist.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
COLLECTION_FILE = "_SERVER_TEST_COLLECTION_FILE"


def pytest_collection_finish(session):
    """Discovery-only plugin: transfer node IDs without parsing pytest output."""
    destination = os.environ.get(COLLECTION_FILE)
    if destination:
        nodeids = []
        for item in session.items:
            filename, separator, name = item.nodeid.partition("::")
            nodeids.append(str(session.config.rootpath / filename) + separator + name)
        Path(destination).write_text(json.dumps(nodeids))


def _terminate(signum, _frame):
    raise SystemExit(128 + signum)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=["tests"], help="pytest paths or node IDs")
    parser.add_argument("--workers", type=int, default=min(6, os.cpu_count() or 1))
    parser.add_argument("--output-dir", type=Path, help="new directory for logs, XML and run.json")
    parser.add_argument(
        "--tmp-dir", type=Path, help="parent for disposable databases (default: TMPDIR)"
    )
    parser.add_argument("-k", default="", help="pytest keyword expression")
    parser.add_argument("-m", default="", help="pytest marker expression")
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    output = args.output_dir or Path(tempfile.mkdtemp(prefix="server-test-results-"))
    output = output.resolve()
    if args.output_dir:
        output.mkdir(parents=True, exist_ok=False)
    print(f"Test results: {output}", flush=True)
    started = time.monotonic()
    previous_termination_handler = signal.signal(signal.SIGTERM, _terminate)
    summary = {"collected": 0, "workers": [], "exit_code": 2}
    try:
        with tempfile.TemporaryDirectory(prefix="server-tests-", dir=args.tmp_dir) as temporary:
            work = Path(temporary)
            manifest = work / "collection.json"
            # App settings are cleared by conftest as in an ordinary pytest run.
            # Keep fixture databases away from the repository and synced folders.
            env = {**os.environ, "TMPDIR": str(work)}
            selection = [*args.paths, "-k", args.k, "-m", args.m]
            with (output / "collection.log").open("w") as log:
                collected = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        "--collect-only",
                        "-q",
                        "-p",
                        "scripts.test",
                        *selection,
                    ],
                    cwd=ROOT,
                    env={**env, COLLECTION_FILE: str(manifest)},
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            if collected.returncode:
                summary["exit_code"] = collected.returncode
                print((output / "collection.log").read_text(), flush=True)
                return collected.returncode
            nodeids = json.loads(manifest.read_text())
            summary["collected"] = len(nodeids)
            count = min(args.workers, len(nodeids))
            print(
                f"Collected {len(nodeids)} tests; running {count} independent pytest processes",
                flush=True,
            )
            pending = []
            try:
                for index in range(count):
                    number = index + 1
                    shard = nodeids[index::count]
                    targets = output / f"worker-{number}.txt"
                    targets.write_text("\n".join(shard) + "\n")
                    command = [
                        sys.executable,
                        "-m",
                        "pytest",
                        "-q",
                        "-ra",
                        "--durations=10",
                        f"--junitxml={output / f'worker-{number}.xml'}",
                        f"@{targets}",
                    ]
                    with (output / f"worker-{number}.log").open("w") as log:
                        process = subprocess.Popen(
                            command,
                            cwd=ROOT,
                            env=env,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                        )
                    pending.append((number, process))
                summary["exit_code"] = 0
                while pending:
                    for number, process in pending[:]:
                        status = process.poll()
                        if status is None:
                            continue
                        pending.remove((number, process))
                        summary["workers"].append({"worker": number, "exit_code": status})
                        if status:
                            summary["exit_code"] = 1
                        xml = output / f"worker-{number}.xml"
                        counts = "no JUnit report"
                        if xml.exists():
                            suites = list(ElementTree.parse(xml).iter("testsuite"))
                            counts = ", ".join(
                                f"{sum(int(s.get(key, 0)) for s in suites)} {key}"
                                for key in ("tests", "failures", "errors", "skipped")
                            )
                        print(f"Worker {number}: exit {status}; {counts}", flush=True)
                        if status:
                            print((output / f"worker-{number}.log").read_text(), flush=True)
                    if pending:
                        time.sleep(0.2)
            finally:
                # Interrupt the whole shard, including browser/host children. Give
                # pytest a chance to tear down its disposable database fixtures.
                for _, process in pending:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGINT)
                for _, process in pending:
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
            return summary["exit_code"]
    except (KeyboardInterrupt, SystemExit) as interruption:
        summary["exit_code"] = (
            130 if isinstance(interruption, KeyboardInterrupt) else interruption.code
        )
        return summary["exit_code"]
    finally:
        signal.signal(signal.SIGTERM, previous_termination_handler)
        summary["wall_seconds"] = round(time.monotonic() - started, 3)
        (output / "run.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(f"Finished in {summary['wall_seconds']:.1f}s; results: {output}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
