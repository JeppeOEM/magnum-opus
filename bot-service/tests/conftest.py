"""Shared test fixtures for bot-service tests."""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True, scope="session")
def _add_strategies_path() -> None:
    """Insert strategies/active into sys.path once per session.

    Allows test functions to import strategy classes (e.g. MACrossBot, OFIBot)
    without repeating the path hack in each test.
    """
    strategies_dir = str(pathlib.Path(__file__).parent.parent / "strategies" / "active")
    if strategies_dir not in sys.path:
        sys.path.insert(0, strategies_dir)


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
