"""Wrapper so the tool runs from the repo root: python scripts/create_zoho_review_module.py -h"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tools.create_zoho_review_module import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
