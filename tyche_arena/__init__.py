"""Thin Arena adapters around TYCHE's shared research implementation."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".agents/skills/lead-sourcing"
sys.path.insert(0, str(SKILL / "scripts"))
