"""Tests for the OAuth2 (PKCE) config flow."""
import base64
import hashlib
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_entry_oauth2_flow

from custom_components.vandebron_energie.const import (
    DOMAIN,
    OAUTH2_CLIENT_ID,
    OAUTH2_TOKEN,
)

USER_INFO_URL = "https://mijn.vandebron.nl/api/authentication/userinfo"
REDIRECT_URI = "https://my.home-assistant.io/redirect/oauth"
TOKEN = {
    "access_token": "access-1",
    "refresh_token": "refresh-1",
    "token_type": "Bearer",
    "expires_in": 7200,
}


def _mock_vandebron(aioclient_mock: AiohttpClientMocker, email: str) -> None:
    aioclient_mock.post(OAUTH2_TOKEN, json=TOKEN)
    aioclient_mock.get(
        USER_INFO_URL, json={"id": "u1", "organizationId": "o1", "email": email}
    )


async def _login(hass, hass_client_no_auth, flow_id, authorize_url):
    """Simulate the browser returning to Home Assistant with a code."""
    state = config_entry_oauth2_flow._encode_jwt(
        hass, {"flow_id": flow_id, "redirect_uri": REDIRECT_URI}
    )
    client = await hass_client_no_auth()
    resp = await client.get(f"/auth/external/callback?code=abcd&state={state}")
    assert resp.status == 200


@pytest.mark.usefixtures("current_request_with_host")
async def test_full_flow_uses_pkce_and_no_password(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """The flow sends a PKCE challenge, then the matching verifier, never a password."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.EXTERNAL_STEP

    query = parse_qs(urlparse(result["url"]).query)
    assert query["client_id"] == [OAUTH2_CLIENT_ID]
    assert query["redirect_uri"] == [REDIRECT_URI]
    assert query["scope"] == ["openid offline_access"]
    assert query["code_challenge_method"] == ["S256"]

    _mock_vandebron(aioclient_mock, "User@Example.com")
    await _login(hass, hass_client_no_auth, result["flow_id"], result["url"])

    with patch(
        "custom_components.vandebron_energie.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Vandebron (User@Example.com)"
    assert result["data"]["token"]["access_token"] == "access-1"
    assert result["data"]["token"]["refresh_token"] == "refresh-1"
    assert "password" not in result["data"]
    assert result["result"].unique_id == "user@example.com"

    token_request = aioclient_mock.mock_calls[0][2]
    assert token_request["grant_type"] == "authorization_code"
    assert "password" not in token_request
    expected_challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(token_request["code_verifier"].encode()).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    assert query["code_challenge"] == [expected_challenge]


@pytest.mark.usefixtures("current_request_with_host")
async def test_legacy_password_entry_is_reauthed(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """An entry created with a password asks for a browser login and drops the password."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"username": "user@example.com", "password": "old"},
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.SETUP_ERROR

    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1 and flows[0]["context"]["source"] == "reauth"

    result = await hass.config_entries.flow.async_configure(flows[0]["flow_id"], {})
    assert result["type"] is FlowResultType.EXTERNAL_STEP

    _mock_vandebron(aioclient_mock, "user@example.com")
    await _login(hass, hass_client_no_auth, result["flow_id"], result["url"])

    with patch(
        "custom_components.vandebron_energie.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["token"]["refresh_token"] == "refresh-1"
    assert "password" not in entry.data


@pytest.mark.usefixtures("current_request_with_host")
async def test_reauth_with_other_account_aborts(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Logging in as a different account during reauth is refused."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"username": "user@example.com", "password": "old"},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    flow_id = hass.config_entries.flow.async_progress()[0]["flow_id"]
    result = await hass.config_entries.flow.async_configure(flow_id, {})

    _mock_vandebron(aioclient_mock, "someone-else@example.com")
    await _login(hass, hass_client_no_auth, result["flow_id"], result["url"])
    result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert "token" not in entry.data
