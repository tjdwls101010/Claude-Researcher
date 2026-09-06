"""``doctor``: every external prerequisite with its install command; exit 7 when a required one is missing."""

from __future__ import annotations

import importlib
import shutil

from .errors import DoctorError

TOOLS = (
    ("tectonic", "brew install tectonic   (single-binary LaTeX; packages download on first build)", True),
    ("codex", "npm install -g @openai/codex   (review lane `codex`, ideation lanes)", True),
    ("claude", "https://claude.com/claude-code   (review lane `claude`, headless critic)", True),
)
PYTHON = (("yaml", "pyyaml", True), ("numpy", "numpy", False), ("matplotlib", "matplotlib", False))


def doctor() -> dict:
    checks = []
    for name, install, required in TOOLS:
        path = shutil.which(name)
        checks.append({"name": name, "ok": path is not None, "detail": path or f"missing -> {install}", "required": required})
    for mod, pip, required in PYTHON:
        try:
            importlib.import_module(mod)
            checks.append({"name": f"python:{mod}", "ok": True, "detail": "importable", "required": required})
        except ImportError:
            checks.append({"name": f"python:{mod}", "ok": False, "detail": f"missing -> python3 -m pip install {pip}", "required": required})
    missing = [c["name"] for c in checks if not c["ok"] and c["required"]]
    result = {"status": "ready" if not missing else f"{len(missing)} required item(s) missing", "checks": checks}
    if missing:
        raise DoctorError(f"missing: {', '.join(missing)}", findings=[{"severity": "major", "message": c["detail"], "location": c["name"]} for c in checks if not c["ok"]], data={"checks": checks})
    return result
