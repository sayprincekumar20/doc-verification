"""Validate downloaded files by their real content (magic bytes), not just the file name."""

from dataclasses import dataclass
from pathlib import PurePath

import filetype

# Formats the reading engine will handle (Phase 1B converts each to page images/text).
ALLOWED = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "tif": "image/tiff",
    "bmp": "image/bmp",
    "heic": "image/heic",
    "heif": "image/heif",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "doc": "application/msword",
    "xls": "application/vnd.ms-excel",
}
_ALIASES = {"jpeg": "jpg", "tiff": "tif"}


@dataclass(frozen=True)
class FileCheck:
    ok: bool
    extension: str | None = None
    mime_type: str | None = None
    reason: str | None = None


def _name_ext(file_name: str | None) -> str | None:
    if not file_name:
        return None
    ext = PurePath(file_name).suffix.lower().lstrip(".")
    return _ALIASES.get(ext, ext) or None


def check_file(data: bytes, file_name: str | None, max_bytes: int) -> FileCheck:
    if not data:
        return FileCheck(False, reason="File is empty")
    if len(data) > max_bytes:
        return FileCheck(False, reason=f"File is {len(data)} bytes; limit is {max_bytes}")

    kind = filetype.guess(data)
    detected = _ALIASES.get(kind.extension, kind.extension) if kind else None
    name_ext = _name_ext(file_name)

    if detected in ALLOWED:
        return FileCheck(True, detected, ALLOWED[detected])
    # Old binary Office formats (doc/xls) share an OLE container that filetype can't tell apart.
    if detected is None and name_ext in ("doc", "xls") and data[:8] == bytes.fromhex(
        "D0CF11E0A1B11AE1"
    ):
        return FileCheck(True, name_ext, ALLOWED[name_ext])
    return FileCheck(
        False,
        reason=f"Unsupported file type (detected={detected or 'unknown'}, name={file_name!r})",
    )
