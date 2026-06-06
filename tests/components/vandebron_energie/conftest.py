"""Test fixtures for Vandebron Energie."""
import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow custom integrations to load in all tests in this package."""
    yield
