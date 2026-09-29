"""Home Assistant integration test setup."""

import pytest


@pytest.fixture(autouse=True)
def enable_custom_integrations_fixture(enable_custom_integrations):
    """Load this checkout as a real custom integration in HA tests."""
    yield
