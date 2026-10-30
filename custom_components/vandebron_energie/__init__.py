"""The Vandebron Energie integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import VandebronEnergieCoordinator
from .oauth import VandebronOAuth2Implementation

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the built-in OAuth2 (PKCE) implementation."""
    config_entry_oauth2_flow.async_register_implementation(
        hass, DOMAIN, VandebronOAuth2Implementation(hass)
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Vandebron Energie from a config entry."""
    if "token" not in entry.data:
        # Created with a password before 2FA: needs a browser login
        raise ConfigEntryAuthFailed("Vandebron login required (2FA)")

    implementation = (
        await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    )
    oauth_session = config_entry_oauth2_flow.OAuth2Session(
        hass, entry, implementation
    )
    coordinator = VandebronEnergieCoordinator(hass, entry, oauth_session)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update options for a config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
