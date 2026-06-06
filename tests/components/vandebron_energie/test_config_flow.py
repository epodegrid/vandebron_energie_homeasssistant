"""Tests for the config flow."""
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.vandebron_energie.api import VandebronAuthError
from custom_components.vandebron_energie.const import DOMAIN

_CREDENTIALS = {CONF_USERNAME: "user@example.com", CONF_PASSWORD: "s3cr3t"}


def _mock_api(authenticate_side_effect=None):
    """Return a patched VandebronApi class whose authenticate() can be controlled."""
    mock = AsyncMock()
    if authenticate_side_effect:
        mock.authenticate.side_effect = authenticate_side_effect
    else:
        mock.authenticate.return_value = None
    return mock


async def test_form_success(hass: HomeAssistant) -> None:
    """Test the full config flow completes successfully."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    with patch(
        "custom_components.vandebron_energie.config_flow.VandebronApi",
        return_value=_mock_api(),
    ), patch(
        "custom_components.vandebron_energie.async_setup_entry",
        return_value=True,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _CREDENTIALS
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Vandebron ({_CREDENTIALS[CONF_USERNAME]})"
    assert result["data"] == _CREDENTIALS


async def test_form_invalid_auth(hass: HomeAssistant) -> None:
    """Test that invalid credentials show the correct error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.vandebron_energie.config_flow.VandebronApi",
        return_value=_mock_api(authenticate_side_effect=VandebronAuthError("bad creds")),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _CREDENTIALS
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_form_cannot_connect(hass: HomeAssistant) -> None:
    """Test that a connection error shows the correct error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.vandebron_energie.config_flow.VandebronApi",
        return_value=_mock_api(authenticate_side_effect=aiohttp.ClientError),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _CREDENTIALS
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
