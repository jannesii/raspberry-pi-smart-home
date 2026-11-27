#!/usr/bin/env python3
"""SessionEnd hook: archive the ending session's transcript into the repo.

Writes <stamp>-<session>.jsonl (raw copy) and a readable .md next to it in
.claude/session-archive/. Reads the hook payload JSON from stdin.
"""

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path


def text_blocks(content):
    if isinstance(content, str):
        return [content]
    out = []
    for block in content or []:
        if block.get("type") == "text":
            out.append(block["text"])
        elif block.get("type") == "tool_use":
            out.append(f"_→ tool: {block.get('name')}_")
    return out


def render_markdown(transcript, session_id):
    lines = [f"# Session {session_id}", ""]
    for raw in transcript.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        role = entry.get("type")
        if role not in ("user", "assistant") or entry.get("isSidechain") or entry.get("isMeta"):
            continue
        parts = [p for p in text_blocks(entry.get("message", {}).get("content")) if p.strip()]
        if not parts:
            continue
        stamp = entry.get("timestamp", "")[:19].replace("T", " ")
        lines += [f"## {role.capitalize()} · {stamp}", "", *parts, ""]
    return "\n".join(lines)


def main():
    payload = json.load(sys.stdin)
    transcript = Path(payload.get("transcript_path") or "")
    if not transcript.is_file():
        print(f"archive-session: no transcript at {transcript}", file=sys.stderr)
        return 1
    session_id = payload.get("session_id") or transcript.stem
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or ".")
    archive = project / ".claude" / "session-archive"
    archive.mkdir(parents=True, exist_ok=True)
    base = archive / f"{datetime.now():%Y-%m-%d_%H%M%S}-{session_id[:8]}"
    shutil.copy2(transcript, base.with_suffix(".jsonl"))
    base.with_suffix(".md").write_text(render_markdown(transcript, session_id), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
