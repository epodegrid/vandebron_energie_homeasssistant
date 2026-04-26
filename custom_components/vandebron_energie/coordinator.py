"""DataUpdateCoordinator for Vandebron Energie."""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import UsageData, VandebronApi, VandebronApiError, VandebronAuthError
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class VandebronEnergieCoordinator(DataUpdateCoordinator[UsageData]):
    """Coordinator that authenticates and polls Vandebron for today's usage."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._api = VandebronApi(
            async_get_clientsession(hass),
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )

    async def _async_setup(self) -> None:
        """Authenticate once before the first data fetch."""
        try:
            await self._api.authenticate()
        except VandebronAuthError as err:
            raise ConfigEntryAuthFailed(
                "Invalid Vandebron credentials"
            ) from err
        except (VandebronApiError, aiohttp.ClientError) as err:
            raise UpdateFailed(f"Could not connect to Vandebron: {err}") from err

    async def _async_update_data(self) -> UsageData:
        """Fetch today's usage. Re-authenticates automatically on 401."""
        try:
            return await self._api.fetch_all_data()
        except aiohttp.ClientResponseError as err:
            if err.status == 401:
                _LOGGER.debug("Token expired, re-authenticating")
                try:
                    await self._api.authenticate()
                    return await self._api.fetch_all_data()
                except VandebronAuthError as auth_err:
                    raise ConfigEntryAuthFailed(
                        "Vandebron credentials no longer valid"
                    ) from auth_err
            raise UpdateFailed(
                f"Vandebron API returned {err.status}: {err.message}"
            ) from err
        except (aiohttp.ClientError, VandebronApiError) as err:
            raise UpdateFailed(f"Could not fetch Vandebron data: {err}") from err
