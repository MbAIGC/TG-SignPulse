import logging

from backend.main import AccessLogPrivacyFilter, _redact_client_address


def test_redact_ipv4_address_keeps_only_network_prefix():
    assert _redact_client_address("192.168.1.100") == "192.168.x.x"
    assert _redact_client_address("192.168.1.100:33108") == "192.168.x.x:33108"


def test_redact_ipv6_address():
    assert _redact_client_address("2001:db8::1") == "2001:0db8:x:x:x:x:x:x"


def test_access_log_filter_redacts_client_address():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("192.168.1.100:33108", "GET", "/api/accounts", "1.1", 200),
        None,
    )

    assert AccessLogPrivacyFilter().filter(record) is True
    assert record.args[0] == "192.168.x.x:33108"


def test_access_log_filter_drops_health_checks():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("192.168.1.100:33108", "GET", "/healthz", "1.1", 200),
        None,
    )

    assert AccessLogPrivacyFilter().filter(record) is False
