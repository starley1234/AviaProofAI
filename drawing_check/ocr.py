"""OCR-ансамбль для чертежей.

Режимы (OCR_MODE):
    auto — использовать все доступные движки по очереди и объединять текст
           (tesseract -> easyocr -> paddleocr); какие именно установлены —
           видно в логах и в /api/info;
    none — заглушка: реальных движков нет (песочница без GPU/системных пакетов).

Предобработка под конфигурацию VRAM 16GB: конвертация в RGB, уменьшение до
OCR_MAX_SIDE (768px). «4-bit» — подсказка квантизации при работе с реальными
движками (easyocr int8 / paddle fp16) — см. документацию.

Заглушка (none) умеет «читать» только эталонный демо-чертёж
(fixtures/sample_drawing.png, сверка по sha256) — чтобы полный цикл
«Загрузка чертежа -> Проверка» работал даже без OCR-движков.
"""
from __future__ import annotations

import hashlib
import io
import shutil
import time
from dataclasses import dataclass, field

from drawing_check.config import settings

# sha256 эталонного демо-чертежа -> текст (для режима заглушки)
# генерируется scripts/make_sample_drawing.py
SAMPLE_DRAWING_SHA = "a071e9cfc46457f78a45004878ca5f868f3da95de68aaa5e34f9f8064cfa50e9"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class OCRResult:
    text: str = ""
    engines_used: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duration_ms: int = 0
    source: str = "ocr"          # ocr | stub | user_override


def _detect_engines() -> dict[str, bool]:
    """Какие OCR-движки доступны (без тяжёлых импортов, где можно)."""
    engines: dict[str, bool] = {}
    engines["tesseract"] = shutil.which("tesseract") is not None
    try:
        import easyocr  # noqa: F401
        engines["easyocr"] = True
    except Exception:
        engines["easyocr"] = False
    try:
        import paddleocr  # noqa: F401
        engines["paddleocr"] = True
    except Exception:
        engines["paddleocr"] = False
    return engines


class OCREnsemble:
    def __init__(self, mode: str | None = None, ensemble: bool | None = None):
        self.mode = mode or settings.ocr_mode
        self.ensemble = settings.ocr_ensemble if ensemble is None else ensemble
        self.available = _detect_engines()

    @property
    def engines_report(self) -> dict:
        return {"mode": self.mode, "ensemble": self.ensemble, "available": self.available}

    def extract(self, image_bytes: bytes, image_name: str = "") -> OCRResult:
        started = time.monotonic()
        result = OCRResult()
        try:
            img = self._preprocess(image_bytes)
        except Exception as e:
            result.warnings.append(f"Не удалось открыть изображение: {e}")
            return result

        if self.mode == "none" or not any(self.available.values()):
            return self._stub(img, image_bytes, image_name, started)

        engines = [name for name, ok in self.available.items() if ok]
        texts: list[str] = []
        for name in engines:
            try:
                t0 = time.monotonic()
                text = self._run_engine(name, img)
                texts.append(text)
                result.engines_used.append(name)
                result.warnings.append(f"{name}: {len(text)} симв. за {int((time.monotonic()-t0)*1000)} мс")
            except Exception as e:  # движок упал — не роняем весь ансамбль
                result.warnings.append(f"{name}: ошибка: {e}")
        if not texts:
            return self._stub(img, image_bytes, image_name, started)

        result.text = self._merge(texts)
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result

    # ─────────────── предобработка (768px, RGB) ───────────────
    def _preprocess(self, image_bytes: bytes):
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        w, h = img.size
        max_side = max(w, h)
        if max_side > settings.ocr_max_side:
            scale = settings.ocr_max_side / max_side
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        return img

    # ─────────────── движки ───────────────
    def _run_engine(self, name: str, img) -> str:
        if name == "tesseract":
            import subprocess
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            proc = subprocess.run(
                ["tesseract", "-", "stdout", "-l", "rus+eng"],
                input=buf.getvalue(), capture_output=True, timeout=60)
            return proc.stdout.decode("utf-8", errors="replace").strip()
        if name == "easyocr":
            import easyocr
            reader = easyocr.Reader(["ru", "en"], gpu=False)
            rows = reader.readtext(img, detail=0)
            return "\n".join(rows).strip()
        if name == "paddleocr":
            from paddleocr import PaddleOCR
            ocr = PaddleOCR(use_angle_cls=True, lang="ru")
            result = ocr.ocr(img, cls=True)
            lines = []
            for page in result or []:
                for row in page or []:
                    lines.append(str(row[1][0]))
            return "\n".join(lines).strip()
        return ""

    @staticmethod
    def _merge(texts: list[str]) -> str:
        """Ансамбль: объединяем строки всех движков, убираем дубликаты."""
        seen: set[str] = set()
        lines: list[str] = []
        for text in texts:
            for line in text.splitlines():
                norm = " ".join(line.split()).lower()
                if not norm or norm in seen:
                    continue
                seen.add(norm)
                lines.append(line.strip())
        return "\n".join(lines)

    # ─────────────── заглушка ───────────────
    def _stub(self, img, image_bytes: bytes, image_name: str, started: float) -> OCRResult:
        digest = sha256_bytes(image_bytes)
        result = OCRResult()
        if digest == SAMPLE_DRAWING_SHA:
            result.text = (
                "ЧЕРТЁЖ СБОРОЧНОЙ ЕДИНИЦЫ ТОРМОЗНОЙ СИСТЕМЫ\n"
                "Усилие на педали тормоза: 120 Н\n"
                "Тормозной путь: 1400 м\n"
                "Материал дисков: АК4-1\n"
                "Масса изделия: 12,5 кг\n"
            )
            result.source = "stub"
            result.warnings.append("Заглушка OCR: распознан эталонный демо-чертёж (sha256 совпал)")
        else:
            result.warnings.append(
                "OCR-движки не установлены (tesseract/easyocr/paddleocr) — текст не распознан. "
                "Вставьте текст чертежа вручную или установите движок (см. README).")
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result


ocr_ensemble = OCREnsemble()
