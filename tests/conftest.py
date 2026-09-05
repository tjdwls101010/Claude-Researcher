"""Shared pytest setup: make each skill's bundled ``scripts/`` importable.

Tests live at the repo root under ``tests/<component>/`` while the code they
exercise lives inside ``.claude/skills/<skill>/scripts/``. There is no
``pyproject.toml`` and nothing is pip-installed, so the path is added here.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"

for scripts_dir in sorted(SKILLS_DIR.glob("*/scripts")):
    path = str(scripts_dir)
    if path not in sys.path:
        sys.path.insert(0, path)
