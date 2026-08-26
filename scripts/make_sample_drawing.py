#!/usr/bin/env python3
"""Генерация эталонного демо-чертежа (fixtures/sample_drawing.png).

Рисует «чертёж» с текстом штампа; sha256 файла подставляется в
drawing_check/ocr.py (SAMPLE_DRAWING_SHA), чтобы заглушка OCR распознавала
именно этот файл в демо-режиме без установленных OCR-движков.

Запуск:  python scripts/make_sample_drawing.py
"""
import hashlib
import os

from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    out_dir = os.path.join(os.path.dirname(__file__), "..", "drawing_check", "fixtures")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "sample_drawing.png")

    w, h = 1024, 768
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)

    def font(size: int):
        for name in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "arial.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    # рамка чертежа
    d.rectangle([30, 30, w - 30, h - 30], outline="black", width=3)
    # схематичное «изделие»
    d.rectangle([120, 120, 620, 420], outline="black", width=2)
    d.line([200, 120, 540, 420], fill="black", width=2)
    d.line([540, 120, 200, 420], fill="black", width=2)
    d.ellipse([330, 240, 410, 320], outline="black", width=2)
    d.text((350, 260), "A", fill="black", font=font(20))
    # выносные линии и размеры
    d.line([620, 270, 780, 270], fill="black", width=1)
    d.line([780, 240, 780, 300], fill="black", width=1)
    d.text((640, 240), "120 Н", fill="black", font=font(24))
    d.text((160, 450), "Тормозной путь: 1400 м", fill="black", font=font(22))
    # штамп
    d.rectangle([w - 420, h - 160, w - 30, h - 30], outline="black", width=2)
    d.text((w - 400, h - 145), "ЧЕРТЁЖ СБОРОЧНОЙ ЕДИНИЦЫ", fill="black", font=font(20))
    d.text((w - 400, h - 115), "ТОРМОЗНОЙ СИСТЕМЫ", fill="black", font=font(20))
    d.text((w - 400, h - 85), "Материал дисков: АК4-1", fill="black", font=font(18))
    d.text((w - 400, h - 58), "Масса изделия: 12,5 кг", fill="black", font=font(18))

    img.save(out_path)
    digest = hashlib.sha256(open(out_path, "rb").read()).hexdigest()
    print(f"Файл: {os.path.abspath(out_path)}")
    print(f"SAMPLE_DRAWING_SHA = {digest!r}")
    print("Вставьте этот sha256 в drawing_check/ocr.py")


if __name__ == "__main__":
    main()
