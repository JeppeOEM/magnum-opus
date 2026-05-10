"""Shared test fixtures and environment setup for bot-service tests."""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest

# Set required credential env vars before any module imports happen.
# Uses setdefault so real credentials already in the environment are preserved.
# These fakes only exist to satisfy pydantic-settings validation —
# no actual exchange calls are made in unit or L2 tests.
_REQUIRED_ENV = {
    "KUCOIN_API_KEY": "test-kucoin-key",
    "KUCOIN_API_SECRET": "test-kucoin-secret",
    "KUCOIN_API_PASSPHRASE": "test-kucoin-passphrase",
    "BYBIT_API_KEY": "test-bybit-key",
    "BYBIT_API_SECRET": "test-bybit-secret",
}

for _k, _v in _REQUIRED_ENV.items():
    os.environ.setdefault(_k, _v)


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None, None, None]:
    """Clear the get_settings() lru_cache before and after each test.

    Pre-test clear ensures env patches (monkeypatch.setenv) take effect.
    Post-test clear prevents a patched Settings from leaking into the next test.
    """
    from bot_service.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
