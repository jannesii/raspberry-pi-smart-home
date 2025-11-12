#!/usr/bin/env python3
"""
ESP API Tester — quick endpoint smoke test for the ESP32 Temperature & Humidity Poster

Endpoints covered (see device firmware README):
  - GET  /status
  - GET  /read
  - GET  /config
    - GET  /metrics
  - POST /config         (optional: via --config-json FILE or --config-inline JSON)
  - POST /task           (optional: via --task-tests to suspend/resume/restart SensorPostTask)

Usage examples:
    python esp_api_tester.py --base-url http://192.168.10.42 --api-key sk_http_local
    python esp_api_tester.py --base-url http://esp.local --api-key sk_http_local --task-tests
    python esp_api_tester.py --base-url http://192.168.10.42 --api-key sk_http_local --config-inline '{"device_location":"lab"}'
    python esp_api_tester.py --base-url http://192.168.10.42 --api-key sk_http_local --config-json ./patch.json

Requires: requests (pip install requests)
"""

from __future__ import annotations
import argparse
import json
import sys
import time
from typing import Any, Dict, Optional

try:
    import requests
except ModuleNotFoundError:
    print("This tool needs 'requests'. Install with:  pip install requests", file=sys.stderr)
    sys.exit(2)


def pretty(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)
    except Exception:
        return str(obj)


class EspApi:
    def __init__(self, base_url: str, api_key: Optional[str] = None, timeout: float = 5.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.headers = {"Accept": "application/json"}
        # Embedded HTTP API requires the bearer token; allow callers to supply it.
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    # ---- GET endpoints ----
    def get_status(self) -> requests.Response:
        return self.session.get(self._url("/status"), headers=self.headers, timeout=self.timeout)

    def get_read(self) -> requests.Response:
        return self.session.get(self._url("/read"), headers=self.headers, timeout=self.timeout)

    def get_config(self) -> requests.Response:
        return self.session.get(self._url("/config"), headers=self.headers, timeout=self.timeout)

    def get_metrics(self) -> requests.Response:
        headers = {**self.headers, "Accept": "text/plain"}
        return self.session.get(self._url("/metrics"), headers=headers, timeout=self.timeout)

    # ---- POST endpoints ----
    def post_config(self, patch: Dict[str, Any]) -> requests.Response:
        return self.session.post(self._url("/config"), headers={**self.headers, "Content-Type": "application/json"},
                                 data=json.dumps(patch), timeout=self.timeout)

    def post_task(self, name: str, action: str) -> requests.Response:
        body = {"name": name, "action": action}
        return self.session.post(self._url("/task"), headers={**self.headers, "Content-Type": "application/json"},
                                 data=json.dumps(body), timeout=self.timeout)


def run_smoke_tests(api: EspApi,
                    do_task_tests: bool = False,
                    config_patch: Optional[Dict[str, Any]] = None) -> int:
    failures = 0

    print(f"==> GET /status")
    try:
        r = api.get_status()
        print(f"HTTP {r.status_code}")
        print(pretty(r.json() if r.headers.get("Content-Type",
              "").startswith("application/json") else r.text))
        r.raise_for_status()
    except Exception as e:
        print(f"[ERROR] /status failed: {e}")
        failures += 1

    print("\n==> GET /read")
    try:
        r = api.get_read()
        print(f"HTTP {r.status_code}")
        print(pretty(r.json() if r.headers.get("Content-Type",
              "").startswith("application/json") else r.text))
        r.raise_for_status()
    except Exception as e:
        print(f"[ERROR] /read failed: {e}")
        failures += 1

    print("\n==> GET /config")
    try:
        r = api.get_config()
        print(f"HTTP {r.status_code}")
        cfg = r.json() if r.headers.get("Content-Type",
                                        "").startswith("application/json") else r.text
        print(pretty(cfg))
        r.raise_for_status()
    except Exception as e:
        print(f"[ERROR] /config (GET) failed: {e}")
        failures += 1

    print("\n==> GET /metrics")
    try:
        r = api.get_metrics()
        print(f"HTTP {r.status_code}")
        print(r.text)
        r.raise_for_status()
    except Exception as e:
        print(f"[ERROR] /metrics failed: {e}")
        failures += 1

    if config_patch:
        print("\n==> POST /config (patch)")
        try:
            r = api.post_config(config_patch)
            print(f"HTTP {r.status_code}")
            print(pretty(r.json() if r.headers.get("Content-Type",
                  "").startswith("application/json") else r.text))
            r.raise_for_status()
        except Exception as e:
            print(f"[ERROR] /config (POST) failed: {e}")
            failures += 1

    if do_task_tests:
        # Exercise SensorPostTask without permanently disabling your API
        task_name = "SensorPostTask"
        for action in ("suspend", "resume", "restart"):
            print(f"\n==> POST /task {{name:{task_name}, action:{action}}}")
            try:
                r = api.post_task(task_name, action)
                print(f"HTTP {r.status_code}")
                print(pretty(r.json() if r.headers.get("Content-Type",
                      "").startswith("application/json") else r.text))
                r.raise_for_status()
            except Exception as e:
                print(f"[ERROR] /task {action} failed: {e}")
                failures += 1
            time.sleep(0.5)

    print("\n==> DONE")
    if failures:
        print(f"Completed with {failures} failure(s).")
    else:
        print("All checks passed.")
    return 1 if failures else 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Quick tester for ESP32-C3 HTTP API endpoints")
    p.add_argument("--base-url", default="http://esp.local",
                   help="Base URL to the ESP (e.g., http://192.168.10.42) [default: %(default)s]")
    p.add_argument("--api-key", default=None,
                   help="HTTP API key for Authorization header (Bearer token)")
    p.add_argument("--timeout", type=float, default=5.0,
                   help="HTTP timeout in seconds [default: %(default)s]")
    p.add_argument("--task-tests", action="store_true",
                   help="Also exercise /task suspend/resume/restart for SensorPostTask")
    p.add_argument("--config-json", type=str, default=None,
                   help="Path to JSON file to POST to /config (partial patch)")
    p.add_argument("--config-inline", type=str, default=None,
                   help="Inline JSON to POST to /config (partial patch)")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    api = EspApi(args.base_url, api_key=args.api_key, timeout=args.timeout)

    patch: Optional[Dict[str, Any]] = None
    if args.config_json and args.config_inline:
        print("Please provide either --config-json or --config-inline, not both.", file=sys.stderr)
        return 2
    if args.config_json:
        with open(args.config_json, "r", encoding="utf-8") as fh:
            patch = json.load(fh)
    elif args.config_inline:
        patch = json.loads(args.config_inline)

    return run_smoke_tests(api, do_task_tests=args.task_tests, config_patch=patch)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
