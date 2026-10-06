"""Image cleanup for phone photos and scans: orientation, document crop, deskew, enhancement."""

import logging
import re
from dataclasses import dataclass

import cv2
import numpy as np
import pytesseract

log = logging.getLogger(__name__)

TARGET_LONG_SIDE = 2400   # ~280 DPI for A4: enough for OCR, fast on big phone photos
OSD_TRUST_CONFIDENCE = 3.0


@dataclass
class Prepared:
    color: np.ndarray        # oriented/cropped/deskewed RGB (for the vision model + reviewers)
    ocr: np.ndarray          # grayscale for Tesseract
    rotation: int            # degrees applied to fix orientation (0/90/180/270)
    cropped: bool            # document edges detected and perspective corrected
    skew: float              # small-angle correction applied (degrees)
    scale: float             # resize factor applied


def _resize_long_side(img: np.ndarray, target: int) -> tuple[np.ndarray, float]:
    h, w = img.shape[:2]
    factor = target / max(h, w)
    if abs(factor - 1) < 0.05:
        return img, 1.0
    interp = cv2.INTER_CUBIC if factor > 1 else cv2.INTER_AREA
    return cv2.resize(img, None, fx=factor, fy=factor, interpolation=interp), factor


def _readable_words(gray: np.ndarray) -> int:
    """Words Tesseract reads confidently (>=70) that look like real words."""
    data = pytesseract.image_to_data(gray, config="--psm 3 --oem 1",
                                     output_type=pytesseract.Output.DICT)
    return sum(1 for text, conf in zip(data["text"], data["conf"], strict=False)
               if float(conf) >= 70 and len(text.strip()) >= 3 and text.strip().isalpha())


def detect_rotation(rgb: np.ndarray) -> int:
    """Clockwise rotation (0/90/180/270) that makes the page upright.

    Tesseract's orientation detector (OSD) is used when it is confident. On photos it is often
    unsure (or wrong), so otherwise each rotation is OCR'd at low resolution and the one with the
    most confidently-read words wins.
    """
    small, _ = _resize_long_side(rgb, 1600)
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    try:
        osd = pytesseract.image_to_osd(gray, config="--psm 0 -c min_characters_to_try=10")
        rotate_by = int(re.search(r"Rotate: (\d+)", osd).group(1))
        conf = float(re.search(r"Orientation confidence: ([\d.]+)", osd).group(1))
        if conf >= OSD_TRUST_CONFIDENCE:
            return rotate_by
    except pytesseract.TesseractError:
        pass  # too little text for OSD
    scores = {deg: _readable_words(rotate(gray, deg)) for deg in (0, 90, 180, 270)}
    best = max(scores, key=scores.get)
    return best if scores[best] > scores[0] * 1.2 else 0


def rotate(rgb: np.ndarray, degrees_cw: int) -> np.ndarray:
    return {
        0: rgb,
        90: cv2.rotate(rgb, cv2.ROTATE_90_CLOCKWISE),
        180: cv2.rotate(rgb, cv2.ROTATE_180),
        270: cv2.rotate(rgb, cv2.ROTATE_90_COUNTERCLOCKWISE),
    }[degrees_cw]


def _order_corners(pts: np.ndarray) -> np.ndarray:
    s, d = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]],
                    dtype=np.float32)


def crop_document(rgb: np.ndarray) -> tuple[np.ndarray, bool]:
    """Find the paper's four corners and flatten it. Leaves the image unchanged when unsure."""
    small, factor = _resize_long_side(rgb, 1000)
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    edges = cv2.dilate(cv2.Canny(gray, 50, 150), np.ones((5, 5), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    area = small.shape[0] * small.shape[1]
    for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        approx = cv2.approxPolyDP(cnt, 0.02 * cv2.arcLength(cnt, True), True)
        ratio = cv2.contourArea(approx) / area
        if len(approx) == 4 and 0.35 <= ratio <= 0.97:
            quad = _order_corners(approx.reshape(4, 2).astype(np.float32) / factor)
            tl, tr, br, bl = quad
            width = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
            height = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
            if width < 300 or height < 300:
                return rgb, False
            dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
                           dtype=np.float32)
            matrix = cv2.getPerspectiveTransform(quad, dst)
            return cv2.warpPerspective(rgb, matrix, (width, height),
                                       borderMode=cv2.BORDER_REPLICATE), True
    return rgb, False


def estimate_skew(gray: np.ndarray, max_angle: float = 5.0, step: float = 0.25) -> float:
    """Angle (degrees) that makes text lines most horizontal (projection-profile method)."""
    small, _ = _resize_long_side(gray, 1200)
    binary = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    h, w = binary.shape
    center = (w / 2, h / 2)
    best_angle, best_score = 0.0, -1.0
    for angle in np.arange(-max_angle, max_angle + step, step):
        m = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(binary, m, (w, h), flags=cv2.INTER_NEAREST)
        score = float(np.var(rotated.sum(axis=1)))
        if score > best_score:
            best_angle, best_score = float(angle), score
    return best_angle


def _rotate_small(img: np.ndarray, angle: float) -> np.ndarray:
    if abs(angle) < 0.2:
        return img
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


def grayscale(rgb: np.ndarray) -> np.ndarray:
    """Plain grayscale is the OCR input. Measured on real PH documents, denoising + contrast
    enhancement lost values (33/71 found vs 51/71), so none is applied."""
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


def ink_filter(gray: np.ndarray, percentile: float = 8.0) -> np.ndarray:
    """Keep only the darkest strokes. Removes light security backgrounds such as the repeated
    "BUREAU OF INTERNAL REVENUE" pattern on BIR 2303 (photo: 8/11 -> 10/11 values found)."""
    norm = cv2.divide(gray, cv2.medianBlur(gray, 31), scale=255)
    return np.where(norm < np.percentile(norm, percentile), 0, 255).astype(np.uint8)


def prepare(rgb: np.ndarray, *, is_photo: bool) -> Prepared:
    rotation = detect_rotation(rgb)
    img = rotate(rgb, rotation)
    cropped = False
    if is_photo:
        img, cropped = crop_document(img)
    skew = estimate_skew(cv2.cvtColor(img, cv2.COLOR_RGB2GRAY))
    img = _rotate_small(img, skew)
    img, scale = _resize_long_side(img, TARGET_LONG_SIDE)
    return Prepared(color=img, ocr=grayscale(img), rotation=rotation, cropped=cropped,
                    skew=skew, scale=scale)
