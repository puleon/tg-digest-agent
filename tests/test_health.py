from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from tgdigest.config import Settings
from tgdigest.health import _http_check, check_all


async def test_http_check_ok(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="http://llm/health", status_code=200)
    check = await _http_check("llm", "http://llm/health")
    assert check.ok and check.detail == "HTTP 200"


async def test_http_check_reports_connection_errors(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(Exception("boom"))
    # a non-httpx exception is a bug, not a health signal: it must propagate
    with pytest.raises(Exception, match="boom"):
        await _http_check("llm", "http://llm/health")


async def test_check_all_never_raises_on_unreachable_services(
    settings: Settings, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(url=f"{settings.qdrant_url}/healthz", status_code=200)
    httpx_mock.add_response(url="http://127.0.0.1:8080/health", status_code=503)
    httpx_mock.add_response(url=f"{settings.langfuse_host}/api/public/health", status_code=200)
    checks = await check_all(settings)  # postgres is not running -> reported, not raised
    by_name = {c.name: c for c in checks}
    assert by_name["qdrant"].ok
    assert not by_name["llm"].ok
    assert by_name["langfuse"].ok
    assert not by_name["postgres"].ok
