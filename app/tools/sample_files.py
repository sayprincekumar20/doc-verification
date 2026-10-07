"""Find gold-set documents under samples/, however they were saved.

fetch_account.py saves files as "<source>_<zoho id>_<name>" with unsafe characters replaced, while
gold.json may use the original name ("BIR 2303 - CLIENT.png") or an underscored one
("BIR_2303_-_CLIENT.png"). Names are matched on a canonical form (prefix removed, lowercase,
letters and digits only).
"""

import re
from pathlib import Path

_PREFIX = re.compile(r"^(attachment|file_field|note_attachment)_[0-9A-Za-z]+_", re.IGNORECASE)


def canonical(name: str) -> str:
    stem = _PREFIX.sub("", Path(name).name)
    return re.sub(r"[^a-z0-9]", "", stem.lower())


def index_files(roots: list[Path]) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.name.endswith(".json"):
                index.setdefault(canonical(path.name), path)
    return index


def find(index: dict[str, Path], gold_name: str) -> Path | None:
    return index.get(canonical(gold_name))
