FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# OCR (English + Filipino), LibreOffice to convert DOCX/XLSX/DOC/XLS to PDF.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-fil \
        libreoffice-writer-nogui libreoffice-calc-nogui \
    && rm -rf /var/lib/apt/lists/*
# One OCR thread per worker process: much faster than OpenMP oversubscription on small VPSs.
ENV OMP_THREAD_LIMIT=1

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
RUN pip install ".[monitoring]"

COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts

RUN mkdir -p /data/files && chown -R app:app /data /app
USER app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
