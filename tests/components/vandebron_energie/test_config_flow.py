"""Tests for the config flow."""
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.vandebron_energie.const import DOMAIN


async def test_form_success(hass: HomeAssistant) -> None:
    """Test the full config flow completes successfully."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    with patch(
        "custom_components.vandebron_energie.config_flow._validate_api_key",
        return_value=None,
    ), patch(
        "custom_components.vandebron_energie.async_setup_entry",
        return_value=True,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"api_key": "test-api-key-123"},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Vandebron Energie"
    assert result["data"] == {"api_key": "test-api-key-123"}


async def test_form_invalid_auth(hass: HomeAssistant) -> None:
    """Test that invalid auth shows the correct error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.vandebron_energie.config_flow._validate_api_key",
        side_effect=aiohttp.ClientResponseError(None, None, status=401),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"api_key": "wrong-key"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_form_cannot_connect(hass: HomeAssistant) -> None:
    """Test that a connection error shows the correct error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.vandebron_energie.config_flow._validate_api_key",
        side_effect=aiohttp.ClientError,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"api_key": "some-key"},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
