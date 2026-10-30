"""Constants for the Vandebron Energie integration."""

DOMAIN = "vandebron_energie"

OAUTH2_CLIENT_ID = "home-assistant"
OAUTH2_AUTHORIZE = "https://vandebron.nl/auth/realms/vandebron/protocol/openid-connect/auth"
OAUTH2_TOKEN = "https://vandebron.nl/auth/realms/vandebron/protocol/openid-connect/token"

CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 1800  # 30 minutes — matches hourly data resolution
