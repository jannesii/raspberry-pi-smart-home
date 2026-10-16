"""Enforce the instruction budgets without loading application dependencies."""

import subprocess
from pathlib import Path


def check_budgets(root: Path) -> list[str]:
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root, text=True).split("\0")
    paths = {root / "AGENTS.md", root / ".github/copilot-instructions.md"}
    paths.update(root / name for name in tracked if Path(name).name == "AGENTS.md")
    errors = []
    total_lines = total_words = 0
    for path in sorted(paths):
        if not path.exists():
            if path == root / "AGENTS.md":
                errors.append("Root AGENTS.md is missing")
            continue
        content = path.read_text(encoding="utf-8")
        lines, words = len(content.splitlines()), len(content.split())
        total_lines += lines
        total_words += words
        if path == root / "AGENTS.md" and (lines > 120 or words > 1100):
            errors.append(f"AGENTS.md: {lines}/120 lines, {words}/1100 words")
    if total_lines > 180 or total_words > 1600:
        errors.append(f"Combined instructions: {total_lines}/180 lines, {total_words}/1600 words")
    return errors


if __name__ == "__main__":
    failures = check_budgets(Path(__file__).resolve().parents[1])
    if failures:
        print("Instruction budget failed:\n" + "\n".join(failures))
        print("Shorten instructions; put reference detail in feature docs or tests.")
        raise SystemExit(1)
    print("Instruction budgets passed.")
