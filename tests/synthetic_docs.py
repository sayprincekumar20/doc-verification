"""Synthetic Philippine-style documents for tests (no real customer data in the repo)."""

import io

import pymupdf
from PIL import Image, ImageDraw, ImageFont

DTI_LINES = [
    "This certifies that",
    "EXAMPLE MARKET",
    "(CITY/MUNICIPALITY)",
    "is a business name registered in this office pursuant to the",
    "provisions of Act 3883, as amended by Act 4147 and Republic Act No. 863",
    "This certificate issued to",
    "JUAN DELA CRUZ",
    "is valid from 11 May 2021 to 11 May 2026",
    "Certificate of Business Name Registration",
    "Business Name No. 1234567",
    "This certificate is not a license to engage in any kind of business",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                 "C:/Windows/Fonts/arial.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def page_image(lines: list[str] = DTI_LINES, width: int = 1700, height: int = 2200) -> Image.Image:
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    font = _font(40)
    y = 200
    for line in lines:
        draw.text((120, y), line, fill="black", font=font)
        y += 120
    return img


def to_bytes(img: Image.Image, fmt: str = "PNG", **kwargs) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def sideways_jpeg_with_exif() -> bytes:
    """Stored rotated, with EXIF orientation 6 telling viewers to rotate it back (like phones)."""
    img = page_image().rotate(90, expand=True)  # stored sideways
    exif = Image.Exif()
    exif[0x0112] = 6
    return to_bytes(img, "JPEG", exif=exif, quality=90)


def sideways_png_no_exif() -> bytes:
    """Photo taken sideways with no EXIF hint (the WINNER permit case)."""
    return to_bytes(page_image().rotate(90, expand=True))


def digital_pdf(lines: list[str] = DTI_LINES) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for line in lines:
        page.insert_text((60, y), line, fontsize=12)
        y += 24
    return doc.tobytes()


def scanned_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=to_bytes(page_image()))
    return doc.tobytes()


def encrypted_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((60, 72), "secret")
    return doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
