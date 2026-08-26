"""JsonRestServices: JSON REST-авторизация Teamcenter (проверенный формат).

Формат запроса — 1:1 с рабочим PHP-клиентом заказчика:

    POST {tc_url}/JsonRestServices/Core-2011-06-Session/login
    Content-Type: application/json
    {
      "header": {"state": {}, "policy": {}},
      "body": {
        "credentials": {
          "user": "...", "password": "...", "role": "",
          "descrimator": "", "locale": "", "group": ""
        }
      }
    }

Сессия — cookie ASP.NET_SessionId из Set-Cookie ответа. Дальше эта сессия
используется операциями XML RestServices (getItemAndRelatedObjects и др.) —
ровно как в проде заказчика: PHP логинится здесь, а данные берёт через
RestServices с заголовком Cookie.

Примечание про «descrimator»: по документации Teamcenter поле называется
discriminator; в рабочем коде заказчика — опечатка «descrimator». Так как
поле пустое, TC одинаково принимает оба варианта. Оставлено как в
проверенном коде — чтобы формат был 1:1 с продом.
"""
from __future__ import annotations

import time

import httpx

from app.services.teamcenter import mapping as m
from app.services.teamcenter.soap import TcAuthError


def json_rest_login(base_url: str, user: str, password: str,
                    http: httpx.Client, timeout: float = 60.0,
                    retries: int = 2) -> str:
    """JSON REST login -> значение cookie ASP.NET_SessionId.

    Устойчивость как у остальных клиентов: ретраи транспортных ошибок с
    backoff; HTTP >= 400 — ошибка авторизации (TcAuthError), не ретраится.
    """
    url = f"{base_url.rstrip('/')}/{m.JSON_REST_PATH}/{m.JSON_REST_SVC_SESSION}/login"
    payload = {
        "header": {"state": {}, "policy": {}},
        "body": {"credentials": {
            "user": user, "password": password, "role": "",
            "descrimator": "", "locale": "", "group": "",
        }},
    }
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = http.post(url, json=payload, headers={"Content-Type": "application/json"})
            if resp.status_code >= 400:
                raise TcAuthError(f"JsonRestServices login: HTTP {resp.status_code}: "
                                  f"{_extract_error(resp)}")
            sid = resp.cookies.get(m.REST_SESSION_COOKIE) or ""
            if not sid:
                raise TcAuthError(f"JsonRestServices login не выдал cookie "
                                  f"{m.REST_SESSION_COOKIE} для пользователя {user}")
            return sid
        except httpx.TransportError as e:
            last_err = TcAuthError(f"JsonRestServices недоступен: {e}")
        except TcAuthError:
            raise
        if attempt < retries:
            time.sleep(0.5 * (2 ** attempt))
    raise last_err  # type: ignore[misc]


def _extract_error(resp: httpx.Response) -> str:
    """Достаёт текст ошибки из JSON-тела ответа TC."""
    try:
        data = resp.json()
        body = data.get("body") if isinstance(data, dict) else None
        if isinstance(body, dict):
            for key in ("error", "errors", "faultstring", "message", "ErrorStack"):
                if key in body:
                    value = body[key]
                    if isinstance(value, list) and value:
                        value = value[0]
                    if isinstance(value, dict):
                        value = value.get("message") or value.get("error") or str(value)
                    return str(value)[:300]
        return str(data)[:300]
    except Exception:
        return resp.text[:300]
