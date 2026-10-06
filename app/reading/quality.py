"""Page quality score: is this page good enough to extract from, or should we ask for a retake?"""

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Quality:
    label: str          # GOOD | FAIR | POOR | UNREADABLE
    score: float        # 0-1
    sharpness: float    # variance of Laplacian at a fixed size (higher = sharper)
    resolution: int     # original long side in pixels
    ocr_conf: float     # mean OCR word confidence 0-100
    words: int
    reasons: list[str]


def assess(original: np.ndarray, gray: np.ndarray, ocr_conf: float, word_count: int,
           text_layer: bool = False) -> Quality:
    small = cv2.resize(gray, (1000, int(1000 * gray.shape[0] / gray.shape[1])),
                       interpolation=cv2.INTER_AREA)
    sharpness = float(cv2.Laplacian(small, cv2.CV_64F).var())
    resolution = int(max(original.shape[:2]))
    reasons = []
    if text_layer:
        return Quality("GOOD", 1.0, round(sharpness, 1), resolution, 100.0, word_count,
                       ["digital PDF text layer"])
    if resolution < 1000:
        reasons.append(f"low resolution ({resolution}px long side)")
    if sharpness < 60:
        reasons.append("blurry")
    if word_count < 15:
        reasons.append("very little readable text")
    conf_part = np.clip((ocr_conf - 40) / 50, 0, 1)
    sharp_part = np.clip(sharpness / 300, 0, 1)
    res_part = np.clip(resolution / 2000, 0, 1)
    score = round(float(0.6 * conf_part + 0.25 * sharp_part + 0.15 * res_part), 2)
    if word_count < 15 or ocr_conf < 40:
        label = "UNREADABLE"
    elif score >= 0.7:
        label = "GOOD"
    elif score >= 0.5:
        label = "FAIR"
    else:
        label = "POOR"
    return Quality(label, score, round(sharpness, 1), resolution, ocr_conf, word_count, reasons)
