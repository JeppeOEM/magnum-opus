"""Shared test fixtures for bot-service tests."""

from __future__ import annotations

from collections.abc import Generator

import pytest


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
