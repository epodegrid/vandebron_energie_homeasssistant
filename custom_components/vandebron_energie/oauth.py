"""OAuth2 implementation for Vandebron: public client, authorization code + PKCE."""
from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any

from yarl import URL

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow

from .const import DOMAIN, OAUTH2_AUTHORIZE, OAUTH2_CLIENT_ID, OAUTH2_TOKEN


class VandebronOAuth2Implementation(
    config_entry_oauth2_flow.LocalOAuth2Implementation
):
    """Public Keycloak client (no secret) using PKCE S256."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass, DOMAIN, OAUTH2_CLIENT_ID, "", OAUTH2_AUTHORIZE, OAUTH2_TOKEN
        )
        self._verifiers: dict[str, str] = {}  # flow_id -> PKCE code_verifier

    @property
    def name(self) -> str:
        return "Vandebron"

    @property
    def redirect_uri(self) -> str:
        # The only redirect URI registered on the Keycloak client
        return config_entry_oauth2_flow.MY_AUTH_CALLBACK_PATH

    @property
    def extra_authorize_data(self) -> dict:
        # offline_access: refresh token outlives the SSO session
        return {"scope": "openid offline_access"}

    async def async_generate_authorize_url(self, flow_id: str) -> str:
        verifier = secrets.token_urlsafe(64)
        self._verifiers[flow_id] = verifier
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        url = await super().async_generate_authorize_url(flow_id)
        return str(
            URL(url).update_query(
                {"code_challenge": challenge, "code_challenge_method": "S256"}
            )
        )

    async def async_resolve_external_data(self, external_data: Any) -> dict:
        verifier = self._verifiers.pop(external_data["state"]["flow_id"])
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": external_data["code"],
                "redirect_uri": external_data["state"]["redirect_uri"],
                "code_verifier": verifier,
            }
        )
