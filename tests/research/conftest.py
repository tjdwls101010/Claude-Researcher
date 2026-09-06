"""``research.py`` and the ``research/`` package share a basename, so the CLI module is loaded here as ``research_cli``."""

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / ".claude" / "skills" / "research" / "scripts"

if "research_cli" not in sys.modules:
    spec = importlib.util.spec_from_file_location("research_cli", SCRIPTS / "research.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["research_cli"] = module
    spec.loader.exec_module(module)
