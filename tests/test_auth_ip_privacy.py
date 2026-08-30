from starlette.requests import Request

from backend.api.routes.auth import _redact_ip, _resolve_request_ip


def _request(headers=None):
    return Request(
        {
            "type": "http",
            "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
            "client": ("192.168.1.100", 1234),
        }
    )


def test_login_audit_ip_is_redacted():
    assert _redact_ip(_resolve_request_ip(_request())) == "192.168.x.x"


def test_forwarded_ip_requires_explicit_trust(monkeypatch):
    request = _request({"x-forwarded-for": "203.0.113.10"})
    monkeypatch.delenv("APP_TRUST_PROXY_HEADERS", raising=False)
    assert _resolve_request_ip(request) == "192.168.1.100"
    monkeypatch.setenv("APP_TRUST_PROXY_HEADERS", "1")
    assert _resolve_request_ip(request) == "203.0.113.10"
