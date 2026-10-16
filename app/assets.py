"""Template asset registry helper.

Allows templates to register CSS/JS assets per-view and inject them in base.html.
"""

from __future__ import annotations

from flask import g


class AssetRegistry:
    def __init__(self):
        self.styles: list[str] = []
        self.scripts: list[str] = []

    def style(self, href: str) -> str:
        try:
            if href and href not in self.styles:
                self.styles.append(href)
        except Exception:
            pass
        return ""

    def script(self, src: str) -> str:
        try:
            if src and src not in self.scripts:
                self.scripts.append(src)
        except Exception:
            pass
        return ""


def register_assets(app) -> None:
    @app.context_processor
    def _inject_assets():
        if not hasattr(g, "_assets"):
            g._assets = AssetRegistry()
        return {"assets": g._assets}
