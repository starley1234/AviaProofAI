FROM python:3.11-slim

WORKDIR /app

# OCR-движки (опционально): tesseract
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
COPY drawing_check/requirements.txt /app/drawing_check/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt -r /app/drawing_check/requirements.txt

COPY app /app/app
COPY drawing_check /app/drawing_check
COPY stub_tc /app/stub_tc

EXPOSE 8501 8502
