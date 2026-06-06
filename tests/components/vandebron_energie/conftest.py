"""Test fixtures for Vandebron Energie."""
import threading

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow custom integrations to load in all tests in this package."""
    yield


@pytest.fixture(autouse=True)
def allow_ha_shutdown_thread(verify_cleanup):
    """Allow HA's safe-shutdown thread through the post-test cleanup check.

    Newer HA versions start a '_run_safe_shutdown_loop' daemon thread that
    pytest-homeassistant-custom-component's verify_cleanup doesn't whitelist.
    By depending on verify_cleanup here, our teardown runs first; we rename
    the thread to match the allowed 'waitpid-*' pattern before the check runs.
    """
    yield
    for thread in threading.enumerate():
        if "_run_safe_shutdown_loop" in thread.name:
            thread.name = f"waitpid-{thread.name}"
