"""Tests for entry setup with OAuth2 tokens."""
import time
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.vandebron_energie.api import UsageData
from custom_components.vandebron_energie.const import DOMAIN, OAUTH2_TOKEN

USER_INFO_URL = "https://mijn.vandebron.nl/api/authentication/userinfo"


def _entry(expires_at: float) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "access-1",
                "refresh_token": "refresh-1",
                "expires_at": expires_at,
                "expires_in": 7200,
            },
        },
    )


async def test_setup_uses_bearer_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A valid token is sent as a Bearer header and the entry loads."""
    assert await async_setup_component(hass, DOMAIN, {})
    aioclient_mock.get(
        USER_INFO_URL, json={"id": "u1", "organizationId": "o1", "email": "u@e.com"}
    )
    entry = _entry(time.time() + 3600)
    entry.add_to_hass(hass)

    with patch(
        "custom_components.vandebron_energie.api.VandebronApi.fetch_all_data",
        return_value=UsageData(),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == "Bearer access-1"


async def test_revoked_refresh_token_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """If the refresh token is rejected, the user is asked to log in again."""
    assert await async_setup_component(hass, DOMAIN, {})
    aioclient_mock.post(OAUTH2_TOKEN, status=400, json={"error": "invalid_grant"})
    entry = _entry(time.time() - 60)  # expired -> triggers a refresh
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1 and flows[0]["context"]["source"] == "reauth"
