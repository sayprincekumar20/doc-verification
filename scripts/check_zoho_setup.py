"""Wrapper so the tool runs from the repo root: python scripts/check_zoho_setup.py --help"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tools.check_zoho_setup import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
