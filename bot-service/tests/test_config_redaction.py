from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from bot_service.config import redact_credentials, Settings


def _make_settings(
    kucoin_key: str = "kc_key_abc123",
    kucoin_secret: str = "kc_secret_xyz789",
    kucoin_passphrase: str = "kc_pass_qwerty",
    bybit_key: str = "bb_key_def456",
    bybit_secret: str = "bb_secret_uvw321",
    redis_url: str = "redis://localhost:6379",
) -> Settings:
    """Construct a Settings instance with known credential values for testing."""
    return Settings(
        kucoin_api_key=kucoin_key,
        kucoin_api_secret=kucoin_secret,
        kucoin_api_passphrase=kucoin_passphrase,
        bybit_api_key=bybit_key,
        bybit_api_secret=bybit_secret,
        redis_url=redis_url,
    )


@pytest.mark.l1
def test_raw_secret_value_is_redacted() -> None:
    """A credential value appearing directly in a log field is replaced with [REDACTED]."""
    settings = _make_settings()
    mock_logger: MagicMock = MagicMock()

    with patch("bot_service.config.get_settings", return_value=settings):
        result = redact_credentials(
            mock_logger,
            "info",
            {"event": "api call", "key": "kc_key_abc123"},
        )

    assert result["key"] == "[REDACTED]"
    assert result["event"] == "api call"


@pytest.mark.l1
def test_secret_embedded_in_exception_message_is_redacted() -> None:
    """A credential value embedded inside a longer string (e.g. traceback) is replaced."""
    settings = _make_settings()
    mock_logger: MagicMock = MagicMock()

    traceback_str = (
        "Traceback (most recent call last):\n"
        "  File 'exchange.py', line 42, in post\n"
        "    headers={'X-API-KEY': 'kc_key_abc123', 'X-API-SECRET': 'kc_secret_xyz789'}\n"
        "ValueError: authentication failed"
    )

    with patch("bot_service.config.get_settings", return_value=settings):
        result = redact_credentials(
            mock_logger,
            "error",
            {"event": "request failed", "exc_info": traceback_str},
        )

    assert "kc_key_abc123" not in result["exc_info"]
    assert "kc_secret_xyz789" not in result["exc_info"]
    assert "[REDACTED]" in result["exc_info"]


@pytest.mark.l1
def test_all_five_credentials_are_redacted() -> None:
    """All five exchange credentials are redacted from the event dict."""
    settings = _make_settings()
    mock_logger: MagicMock = MagicMock()

    event_dict = {
        "event": "credential dump",
        "kucoin_key": "kc_key_abc123",
        "kucoin_secret": "kc_secret_xyz789",
        "kucoin_pass": "kc_pass_qwerty",
        "bybit_key": "bb_key_def456",
        "bybit_secret": "bb_secret_uvw321",
    }

    with patch("bot_service.config.get_settings", return_value=settings):
        result = redact_credentials(mock_logger, "info", event_dict)

    assert result["kucoin_key"] == "[REDACTED]"
    assert result["kucoin_secret"] == "[REDACTED]"
    assert result["kucoin_pass"] == "[REDACTED]"
    assert result["bybit_key"] == "[REDACTED]"
    assert result["bybit_secret"] == "[REDACTED]"


@pytest.mark.l1
def test_redis_url_password_component_is_redacted() -> None:
    """Redis URL password component (redis://:password@host) is replaced with [REDACTED]."""
    settings = _make_settings(redis_url="redis://:mysecretpass@redis-host:6379")
    mock_logger: MagicMock = MagicMock()

    with patch("bot_service.config.get_settings", return_value=settings):
        result = redact_credentials(
            mock_logger,
            "info",
            {"event": "connecting", "url": "redis://:mysecretpass@redis-host:6379"},
        )

    assert "mysecretpass" not in result["url"]
    assert "redis://:[REDACTED]@redis-host:6379" == result["url"]


@pytest.mark.l1
def test_non_credential_value_is_unchanged() -> None:
    """A value that does not match any credential is passed through unchanged."""
    settings = _make_settings()
    mock_logger: MagicMock = MagicMock()

    with patch("bot_service.config.get_settings", return_value=settings):
        result = redact_credentials(
            mock_logger,
            "info",
            {"event": "order placed", "symbol": "BTCUSDT", "price": "50000.0"},
        )

    assert result["symbol"] == "BTCUSDT"
    assert result["price"] == "50000.0"
    assert result["event"] == "order placed"


@pytest.mark.l1
def test_nested_dict_values_are_redacted() -> None:
    """Credential values inside nested dict structures are also redacted."""
    settings = _make_settings()
    mock_logger: MagicMock = MagicMock()

    with patch("bot_service.config.get_settings", return_value=settings):
        result = redact_credentials(
            mock_logger,
            "info",
            {
                "event": "request",
                "headers": {
                    "X-API-KEY": "kc_key_abc123",
                    "Content-Type": "application/json",
                },
            },
        )

    assert result["headers"]["X-API-KEY"] == "[REDACTED]"
    assert result["headers"]["Content-Type"] == "application/json"


@pytest.mark.l1
def test_list_values_with_secrets_are_redacted() -> None:
    """Credential values inside list fields are also redacted."""
    settings = _make_settings()
    mock_logger: MagicMock = MagicMock()

    with patch("bot_service.config.get_settings", return_value=settings):
        result = redact_credentials(
            mock_logger,
            "info",
            {"event": "dump", "items": ["kc_key_abc123", "safe_value", "bb_key_def456"]},
        )

    assert result["items"][0] == "[REDACTED]"
    assert result["items"][1] == "safe_value"
    assert result["items"][2] == "[REDACTED]"


@pytest.mark.l1
def test_empty_secret_value_does_not_cause_full_redaction() -> None:
    """Empty string credentials are skipped so the processor does not blank all strings."""
    settings = _make_settings(
        kucoin_key="",
        kucoin_secret="",
        kucoin_passphrase="",
        bybit_key="",
        bybit_secret="",
    )
    mock_logger: MagicMock = MagicMock()

    with patch("bot_service.config.get_settings", return_value=settings):
        result = redact_credentials(
            mock_logger,
            "info",
            {"event": "normal log message with content"},
        )

    assert result["event"] == "normal log message with content"
