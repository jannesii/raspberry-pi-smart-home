#!/usr/bin/env python3
"""Throwaway UI host. No app factory, devices, accounts, database or external API.

Run: python3 scripts/run_car_heater_ui_prototype.py
The existing /car_heater route is reproduced in a separate local HTTP process,
so simulated controls cannot reach production. The application navbar CSS is
reused; unrelated navbar links are deliberately disabled in this isolated host.
"""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


class PrototypeHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ("/", "/car_heater"):
            self.path = "/templates/car_heater_ui_prototype.html"
        elif path not in {
            "/static/css/base.css",
            "/static/css/navbar.css",
            "/static/css/car_heater_ui_prototype.css",
            "/static/js/car_heater_ui_prototype.js",
        }:
            self.send_error(404)
            return
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    handler = partial(PrototypeHandler, directory=ROOT / "app")
    print(
        f"Car heater UI prototype: http://{args.host}:{args.port}/car_heater?variant=A", flush=True
    )
    print("Synthetic data only. Ctrl+C to stop.", flush=True)
    ThreadingHTTPServer((args.host, args.port), handler).serve_forever()
