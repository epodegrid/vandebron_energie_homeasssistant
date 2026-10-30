"""DataUpdateCoordinator for Vandebron Energie."""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import UsageData, VandebronApi, VandebronApiError, VandebronAuthError
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class VandebronEnergieCoordinator(DataUpdateCoordinator[UsageData]):
    """Coordinator that polls Vandebron for today's usage."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, oauth_session: OAuth2Session
    ) -> None:
        self._oauth_session = oauth_session
        self._api = VandebronApi(async_get_clientsession(hass))
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )

    async def _async_ensure_token(self) -> None:
        """Refresh the access token if needed and hand it to the API client."""
        try:
            await self._oauth_session.async_ensure_token_valid()
        except aiohttp.ClientResponseError as err:
            if err.status in (400, 401, 403):  # refresh token revoked or expired
                raise ConfigEntryAuthFailed("Vandebron login expired") from err
            raise UpdateFailed(f"Could not refresh Vandebron token: {err}") from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Could not connect to Vandebron: {err}") from err
        self._api.set_token(self._oauth_session.token["access_token"])

    async def _async_setup(self) -> None:
        """Resolve the user and organisation before the first data fetch."""
        await self._async_ensure_token()
        try:
            await self._api.fetch_user_info()
        except VandebronAuthError as err:
            raise ConfigEntryAuthFailed("Vandebron login expired") from err
        except (VandebronApiError, aiohttp.ClientError) as err:
            raise UpdateFailed(f"Could not connect to Vandebron: {err}") from err

    async def _async_update_data(self) -> UsageData:
        """Fetch today's usage."""
        await self._async_ensure_token()
        try:
            return await self._api.fetch_all_data()
        except aiohttp.ClientResponseError as err:
            raise UpdateFailed(
                f"Vandebron API returned {err.status}: {err.message}"
            ) from err
        except (aiohttp.ClientError, VandebronApiError) as err:
            raise UpdateFailed(f"Could not fetch Vandebron data: {err}") from err
