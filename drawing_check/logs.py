"""Хранилище логов сервиса «Проверка чертежа».

Файловое кольцевое хранилище (JSON-строки) с защитой от разрастания:
    log()   — дописать запись (уровень, сообщение, доп. поля)
    tail()  — последние N записей (для страницы логов)
    clear() — стереть ВСЕ логи (кнопка «Стереть логи» в UI)

Потокобезопасно; запись атомарная (append + flush).
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone

from drawing_check.config import settings

LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")


class LogStore:
    def __init__(self, path: str | None = None, max_bytes: int | None = None):
        self.path = path or settings.log_dir
        self.max_bytes = max_bytes or settings.log_max_bytes
        self._lock = threading.Lock()
        os.makedirs(self.path, exist_ok=True)

    # ─────────────── запись ───────────────
    def log(self, level: str, message: str, **extra) -> dict:
        level = level.upper() if level.upper() in LEVELS else "INFO"
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": level,
            "msg": message,
            **extra,
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            file_path = self._current_file()
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
            self._rotate_if_needed(file_path)
        return record

    def info(self, message: str, **extra) -> dict:
        return self.log("INFO", message, **extra)

    def warn(self, message: str, **extra) -> dict:
        return self.log("WARN", message, **extra)

    def error(self, message: str, **extra) -> dict:
        return self.log("ERROR", message, **extra)

    # ─────────────── чтение ───────────────
    def tail(self, limit: int | None = None) -> list[dict]:
        limit = limit or settings.log_tail_default
        file_path = self._current_file()
        if not os.path.exists(file_path):
            return []
        with self._lock:
            with open(file_path, encoding="utf-8") as f:
                lines = f.readlines()
        records: list[dict] = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append({"ts": "", "level": "WARN", "msg": line[:500]})
        return records

    # ─────────────── очистка ───────────────
    def clear(self, reason: str = "вручную") -> int:
        """Стирает логи. Возвращает число стёртых записей."""
        file_path = self._current_file()
        with self._lock:
            count = 0
            if os.path.exists(file_path):
                with open(file_path, encoding="utf-8") as f:
                    count = sum(1 for ln in f if ln.strip())
                os.remove(file_path)
        self.info("Логи стёрты", by=reason, erased=count)
        return count

    # ─────────────── служебное ───────────────
    def _current_file(self) -> str:
        return os.path.join(self.path, "drawing_check.log")

    def _rotate_if_needed(self, file_path: str) -> None:
        """Кольцевое усечение: если файл больше лимита — оставить последнюю половину."""
        size = os.path.getsize(file_path)
        if size <= self.max_bytes:
            return
        with open(file_path, encoding="utf-8") as f:
            lines = f.readlines()
        half = lines[len(lines) // 2:]
        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(half)
        self._write_now(file_path, json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": "WARN",
            "msg": f"Лог усечён (лимит {self.max_bytes} байт), удалено {len(lines) - len(half)} записей",
        }, ensure_ascii=False))

    @staticmethod
    def _write_now(file_path: str, line: str) -> None:
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


# единый экземпляр на процесс
log_store = LogStore()
