"""Фабрика клиента Teamcenter: выбор протокола (REST | SOAP) по настройкам.

TC_PROTOCOL=rest — REST-сервисы (RestServices, cookie ASP.NET_SessionId) —
проверенный рабочий протокол заказчика (см. koseven-клиент).
TC_PROTOCOL=soap — классический SOAP (AuthenticationToken в SOAP-Header).
"""
from __future__ import annotations

from app.config import Settings


def build_tc_client(settings: Settings | None = None, session_id: str | None = None):
    """Возвращает TeamcenterRestClient или TeamcenterSoapClient — у обоих
    одинаковый интерфейс, TeamcenterSync работает с любым."""
    from app.config import get_settings
    s = settings or get_settings()
    common = dict(
        base_url=s.tc_url, timeout=s.tc_timeout, connect_timeout=s.tc_connect_timeout,
        retries=s.tc_retries, page_size=s.tc_page_size,
        verify=s.tc_verify_ssl, max_content_bytes=s.tc_max_content_bytes,
        allow_external_files=s.tc_allow_external_files,
    )
    if s.tc_protocol == "rest":
        from app.services.teamcenter.rest_client import TeamcenterRestClient
        return TeamcenterRestClient(session_id=session_id or s.tc_session_id, **common)
    from app.services.teamcenter.client import TeamcenterSoapClient
    return TeamcenterSoapClient(**common)
