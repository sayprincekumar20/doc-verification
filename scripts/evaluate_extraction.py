"""Wrapper so the tool runs from the repo root: python scripts/evaluate_extraction.py --help"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tools.evaluate_extraction import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
