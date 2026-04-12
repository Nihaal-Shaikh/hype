"""
pytest conftest at the repo root.

Adds the repo root to sys.path so that tests/ subdirectory modules can
`from hype_bot import ...` without needing an installed package. This is
the minimal shim compatible with the flat-file bootstrap decision
(Phase 3 plan, Decision 1).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
