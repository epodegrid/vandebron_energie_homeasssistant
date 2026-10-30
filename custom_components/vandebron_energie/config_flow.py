"""Config flow for Vandebron Energie."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol

try:
    from homeassistant.config_entries import ConfigFlowResult, OptionsFlow
except ImportError:  # HA < 2024.4 (Python 3.11 dev environments)
    from homeassistant.config_entries import OptionsFlow  # type: ignore[assignment]
    from homeassistant.data_entry_flow import FlowResult as ConfigFlowResult  # type: ignore[assignment]
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import VandebronApi, VandebronApiError, VandebronAuthError
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN
from .oauth import VandebronOAuth2Implementation

_LOGGER = logging.getLogger(__name__)


class VandebronEnergieConfigFlow(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Handle a config flow for Vandebron Energie (OAuth2 + PKCE, 2FA-capable)."""

    DOMAIN = DOMAIN
    VERSION = 1

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Register the implementation: async_setup has not run before the first flow."""
        implementations = await config_entry_oauth2_flow.async_get_implementations(
            self.hass, DOMAIN
        )
        if DOMAIN not in implementations:
            config_entry_oauth2_flow.async_register_implementation(
                self.hass, DOMAIN, VandebronOAuth2Implementation(self.hass)
            )
        return await super().async_step_user(user_input)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Re-login, e.g. for entries created with a password before 2FA."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_user()

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Create or update the entry once the user has logged in."""
        api = VandebronApi(async_get_clientsession(self.hass))
        api.set_token(data["token"]["access_token"])
        try:
            info = await api.fetch_user_info()
        except VandebronAuthError:
            return self.async_abort(reason="invalid_auth")
        except (VandebronApiError, aiohttp.ClientError):
            return self.async_abort(reason="cannot_connect")

        email = info["email"]
        await self.async_set_unique_id(email.lower())

        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
            return self.async_update_reload_and_abort(entry, data=data)

        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=f"Vandebron ({email})", data=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> VandebronEnergieOptionsFlow:
        """Return the options flow."""
        return VandebronEnergieOptionsFlow()


class VandebronEnergieOptionsFlow(OptionsFlow):
    """Options flow for Vandebron Energie."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(int, vol.Range(min=300)),
                }
            ),
        )
