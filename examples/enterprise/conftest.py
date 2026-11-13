"""Global PyTest configuration and fixtures for ai-rag examples test suites."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SRC_DIR = _ROOT.parent.parent / "src"
_EXTERNAL_SRC = _ROOT.parent / "ai-rag-common-framework" / "src"

for p in (_ROOT, _SRC_DIR, _EXTERNAL_SRC):
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))
