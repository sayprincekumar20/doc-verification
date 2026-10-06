"""Tesseract OCR with per-word confidence and positions."""

from dataclasses import dataclass, field

import numpy as np
import pytesseract

DEFAULT_LANG = "eng"  # documents are English; add "+fil" for Filipino text


@dataclass
class Word:
    text: str
    conf: float                      # 0-100
    box: tuple[int, int, int, int]   # left, top, width, height (in the prepared image)
    line: tuple[int, int, int]       # block, paragraph, line numbers


@dataclass
class OcrResult:
    text: str
    mean_conf: float                 # mean word confidence, 0-100
    words: list[Word] = field(default_factory=list)


def run_ocr(gray: np.ndarray, lang: str = DEFAULT_LANG, psm: int = 3) -> OcrResult:
    data = pytesseract.image_to_data(gray, lang=lang, config=f"--psm {psm} --oem 1",
                                     output_type=pytesseract.Output.DICT)
    words: list[Word] = []
    for i, raw in enumerate(data["text"]):
        text = raw.strip()
        conf = float(data["conf"][i])
        if not text or conf < 0:
            continue
        words.append(Word(text, conf,
                          (data["left"][i], data["top"][i], data["width"][i], data["height"][i]),
                          (data["block_num"][i], data["par_num"][i], data["line_num"][i])))
    lines: dict[tuple[int, int, int], list[str]] = {}
    for w in words:
        lines.setdefault(w.line, []).append(w.text)
    text = "\n".join(" ".join(ws) for _, ws in sorted(lines.items()))
    mean = float(np.mean([w.conf for w in words])) if words else 0.0
    return OcrResult(text=text, mean_conf=round(mean, 1), words=words)
