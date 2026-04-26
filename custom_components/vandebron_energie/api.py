"""Vandebron Energie async API client.

Auth flow: Keycloak OIDC with username/password, yields a Bearer access token.
All data calls use that token against the mijn.vandebron.nl API.
"""
from __future__ import annotations

import html as html_module
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp

_LOGGER = logging.getLogger(__name__)

_AUTH_URL = "https://vandebron.nl/auth/realms/vandebron/protocol/openid-connect/auth"
_TOKEN_URL = "https://vandebron.nl/auth/realms/vandebron/protocol/openid-connect/token"
_USER_INFO_URL = "https://mijn.vandebron.nl/api/authentication/userinfo"
_ENERGY_CONSUMERS_URL = "https://mijn.vandebron.nl/api/v1/energyConsumers/{org_id}"
_USAGE_URL = "https://mijn.vandebron.nl/api/consumers/{user_id}/connections/{conn_id}/usage"
_DASHBOARD_URL = "https://mijn.vandebron.nl/api/consumers/{user_id}/dashboard"

MARKET_ELECTRICITY = "electricity"
MARKET_GAS = "gas"

# Current NL energy VAT rate
_VAT = 0.09


@dataclass
class TariffRates:
    """Electricity tariff rates extracted from the Vandebron dashboard currentCosts."""

    # Per-kWh rates, excl. VAT
    peak_per_kwh: float = 0.0          # Energy component (piek)
    off_peak_per_kwh: float = 0.0      # Energy component (dal)
    premium_per_kwh: float = 0.0       # Vandebron renewable premium
    peak_odn_per_kwh: float = 0.0      # Grid transport (piek)
    off_peak_odn_per_kwh: float = 0.0  # Grid transport (dal)

    # Annual charges, excl. VAT
    fixed_fee_per_year: float = 0.0    # Standing charge (vastrecht)
    tax_credit_per_year: float = 0.0   # Energy tax rebate (belastingkorting), typically negative

    # Vandebron's own forward-looking monthly cost calculation
    advance_payment_monthly: float = 0.0

    # SJV — Standard Annual Usage profile
    annual_peak_kwh: float = 0.0
    annual_off_peak_kwh: float = 0.0

    @property
    def total_peak_rate(self) -> float:
        """All-in cost per peak kWh, including VAT."""
        return (self.peak_per_kwh + self.premium_per_kwh + self.peak_odn_per_kwh) * (1 + _VAT)

    @property
    def total_off_peak_rate(self) -> float:
        """All-in cost per off-peak kWh, including VAT."""
        return (self.off_peak_per_kwh + self.premium_per_kwh + self.off_peak_odn_per_kwh) * (1 + _VAT)

    @property
    def monthly_fixed_cost(self) -> float:
        """Monthly standing charge + tax rebate, including VAT."""
        return ((self.fixed_fee_per_year + self.tax_credit_per_year) / 12) * (1 + _VAT)

    @property
    def annual_expected_kwh(self) -> float:
        return self.annual_peak_kwh + self.annual_off_peak_kwh

    def variable_cost(self, peak_kwh: float, off_peak_kwh: float) -> float:
        """Variable cost for given kWh, including VAT."""
        return (
            peak_kwh * self.total_peak_rate
            + off_peak_kwh * self.total_off_peak_rate
        )

    def total_monthly_cost(self, peak_kwh: float, off_peak_kwh: float) -> float:
        """Variable cost + proportional fixed charges, including VAT."""
        return self.variable_cost(peak_kwh, off_peak_kwh) + self.monthly_fixed_cost


@dataclass
class Connection:
    """A single energy connection (electricity or gas)."""

    market_segment: str  # MARKET_ELECTRICITY or MARKET_GAS
    conn_id: str


@dataclass
class UsageData:
    """Aggregated usage, costs and forecasts for the most recent available data."""

    # ---------- Daily ----------
    electricity_peak_kwh: float = 0.0
    electricity_off_peak_kwh: float = 0.0
    gas_m3: float | None = None
    has_electricity: bool = False
    has_gas: bool = False
    # Date the daily figures come from (may be yesterday due to API lag)
    data_date: date | None = None

    # ---------- Month-to-date ----------
    electricity_month_peak_kwh: float = 0.0
    electricity_month_off_peak_kwh: float = 0.0

    # ---------- Costs (incl. VAT) ----------
    electricity_today_cost_eur: float | None = None      # variable cost for the daily reading
    electricity_month_cost_eur: float | None = None      # variable + fixed for month-to-date
    electricity_month_expected_cost_eur: float | None = None  # Vandebron's advance payment calc

    # ---------- Expected (SJV) ----------
    electricity_month_expected_kwh: float | None = None  # annual SJV ÷ 12


class VandebronApiError(Exception):
    """Base exception for Vandebron API errors."""


class VandebronAuthError(VandebronApiError):
    """Authentication failed (bad credentials or unexpected auth response)."""


class VandebronApi:
    """Async client for the Vandebron mijn.vandebron.nl API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._token: str | None = None
        self._user_id: str | None = None
        self._org_id: str | None = None

    @property
    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def authenticate(self) -> None:
        """Full OIDC code-flow login. Populates self._token / user info."""
        async with aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(unsafe=True)
        ) as auth_session:
            login_url = await self._get_login_url(auth_session)
            auth_code = await self._get_auth_code(auth_session, login_url)
            self._token = await self._exchange_code(auth_session, auth_code)

        await self._fetch_user_info()

    async def _get_login_url(self, session: aiohttp.ClientSession) -> str:
        params = {
            "client_id": "website",
            "redirect_uri": "https://mijn.vandebron.nl/",
            "state": str(uuid.uuid4()),
            "response_mode": "fragment",
            "response_type": "code",
            "scope": "openid",
            "nonce": str(uuid.uuid4()),
        }
        async with session.get(_AUTH_URL, params=params) as resp:
            resp.raise_for_status()
            text = await resp.text()

        match = re.search(r'<form[^>]+action="([^"]+)"', text)
        if not match:
            raise VandebronAuthError("Could not find login form on Keycloak auth page")
        return html_module.unescape(match.group(1))

    async def _get_auth_code(
        self, session: aiohttp.ClientSession, login_url: str
    ) -> str:
        async with session.post(
            login_url,
            data={
                "username": self._username,
                "password": self._password,
                "login": "Log in",
            },
            allow_redirects=False,
        ) as resp:
            if resp.status not in (301, 302):
                raise VandebronAuthError(
                    f"Login failed — expected redirect, got {resp.status}. "
                    "Check your username and password."
                )
            location = resp.headers.get("Location", "")

        parsed = urlparse(location)
        params = parse_qs(parsed.fragment)
        if "code" not in params:
            raise VandebronAuthError(
                "No authorization code in redirect after login."
            )
        return params["code"][0]

    async def _exchange_code(
        self, session: aiohttp.ClientSession, auth_code: str
    ) -> str:
        async with session.post(
            _TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": "website",
                "code": auth_code,
                "redirect_uri": "https://mijn.vandebron.nl/",
            },
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

        if "access_token" not in data:
            raise VandebronAuthError("Token endpoint did not return an access_token")
        return str(data["access_token"])

    async def _fetch_user_info(self) -> None:
        async with self._session.get(
            _USER_INFO_URL, headers=self._auth_headers
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

        self._user_id = data["id"]
        self._org_id = data["organizationId"]

    # ------------------------------------------------------------------
    # Dashboard / tariffs
    # ------------------------------------------------------------------

    async def get_dashboard(self) -> dict[str, Any]:
        """Fetch the user dashboard (contains tariffs, SJV usage, advance payment)."""
        url = _DASHBOARD_URL.format(user_id=self._user_id)
        async with self._session.get(url, headers=self._auth_headers) as resp:
            resp.raise_for_status()
            return await resp.json()

    def _parse_tariff_rates(self, dashboard: dict[str, Any]) -> TariffRates | None:
        """Extract tariff rates and SJV from the dashboard connection data."""
        try:
            addrs = dashboard.get("shippingAddresses", [])
            if not addrs:
                return None

            addr = addrs[0]
            conns = addr.get("connections", [])
            electricity_conn = next(
                (c for c in conns if c.get("marketSegment", "").lower() == MARKET_ELECTRICITY),
                None,
            )
            if not electricity_conn:
                return None

            costs: list[dict[str, Any]] = electricity_conn.get("currentCosts", [])
            sjv = electricity_conn.get("annualStandardUsage", {})
            adv = addr.get("advancePayment", {}).get("currentAdvancePayment", {})

            rates = TariffRates(
                annual_peak_kwh=float(sjv.get("peakUsage") or 0),
                annual_off_peak_kwh=float(sjv.get("offPeakUsage") or 0),
                advance_payment_monthly=float(adv.get("calculatedAmount") or 0),
            )

            # De-duplicate by component type and accumulate per-kWh rates
            seen: set[str] = set()
            for item in costs:
                ptype = item.get("priceComponentType", "")
                price = float(item.get("price") or 0)
                unit = item.get("priceUnit", "")
                if ptype in seen:
                    continue
                seen.add(ptype)

                if ptype == "Peak" and unit == "KWh":
                    rates.peak_per_kwh = price
                elif ptype == "OffPeak" and unit == "KWh":
                    rates.off_peak_per_kwh = price
                elif ptype == "Premium" and unit == "KWh":
                    rates.premium_per_kwh = price
                elif ptype == "PeakODN" and unit == "KWh":
                    rates.peak_odn_per_kwh = price
                elif ptype == "OffPeakODN" and unit == "KWh":
                    rates.off_peak_odn_per_kwh = price
                elif ptype == "FixedFee" and unit == "Year":
                    rates.fixed_fee_per_year = price
                elif ptype == "TaxCredit" and unit == "Year":
                    rates.tax_credit_per_year = price  # negative value

            _LOGGER.debug(
                "Tariff rates: peak=%.5f off_peak=%.5f premium=%.5f "
                "peak_odn=%.5f off_peak_odn=%.5f fixed=%.2f/yr credit=%.2f/yr "
                "advance=%.2f/mo SJV=%.0f+%.0f kWh/yr",
                rates.peak_per_kwh, rates.off_peak_per_kwh, rates.premium_per_kwh,
                rates.peak_odn_per_kwh, rates.off_peak_odn_per_kwh,
                rates.fixed_fee_per_year, rates.tax_credit_per_year,
                rates.advance_payment_monthly,
                rates.annual_peak_kwh, rates.annual_off_peak_kwh,
            )
            return rates

        except Exception:
            _LOGGER.warning("Could not parse tariff rates from dashboard", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Usage data
    # ------------------------------------------------------------------

    async def get_connections(self) -> list[Connection]:
        """Return all energy connections for this account."""
        url = _ENERGY_CONSUMERS_URL.format(org_id=self._org_id)
        async with self._session.get(url, headers=self._auth_headers) as resp:
            resp.raise_for_status()
            data = await resp.json()

        connections: list[Connection] = []
        for addr in data.get("shippingAddresses", []):
            for con in addr.get("connections", []):
                connections.append(
                    Connection(
                        market_segment=con["marketSegment"],
                        conn_id=con["connectionId"],
                    )
                )
        return connections

    async def _get_usage_range(
        self,
        connection: Connection,
        start: date,
        end: date,
    ) -> dict[str, Any]:
        """Fetch 15-min interval usage data for [start, end).

        The server interprets timestamps as NL local time regardless of the
        format, so we send naive local timestamps (no Z / offset).
        """
        url = _USAGE_URL.format(
            user_id=self._user_id, conn_id=connection.conn_id
        )
        async with self._session.get(
            url,
            params={
                "resolution": "Hours",
                "startDateTime": f"{start.isoformat()}T00:15:00.000",
                "endDateTime": f"{end.isoformat()}T00:00:00.000",
            },
            headers=self._auth_headers,
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

        values = data.get("values", [])
        _LOGGER.debug(
            "Usage %s→%s for %s (%s): unit=%s %d intervals",
            start, end, connection.conn_id, connection.market_segment,
            data.get("unit"), len(values),
        )
        return data

    def _aggregate_consumption(
        self, values: list[dict[str, Any]], unit: str
    ) -> tuple[float, float]:
        """Return (peak_kwh, off_peak_kwh) summed across intervals, converted to kWh."""
        divisor = 1000.0 if unit.upper() == "WH" else 1.0
        peak = sum(float(v.get("consumptionPeak") or 0.0) for v in values) / divisor
        off_peak = sum(float(v.get("consumptionOffPeak") or 0.0) for v in values) / divisor
        return round(peak, 4), round(off_peak, 4)

    def _aggregate_gas(self, values: list[dict[str, Any]], unit: str) -> float:
        divisor = 1000.0 if unit.upper() == "WH" else 1.0
        return round(
            sum(
                float(v.get("consumptionPeak") or 0.0)
                + float(v.get("consumptionOffPeak") or 0.0)
                for v in values
            ) / divisor,
            4,
        )

    # ------------------------------------------------------------------
    # Main data fetch
    # ------------------------------------------------------------------

    async def fetch_all_data(self) -> UsageData:
        """Fetch daily usage, monthly usage, tariffs and derived costs.

        Vandebron has ~1 day lag on smart meter readings: today's consumptionPeak
        and consumptionOffPeak are often zero until the next day. We try today
        first and fall back to yesterday for the daily sensor values.
        """
        today = date.today()
        yesterday = today - timedelta(days=1)
        month_start = today.replace(day=1)

        connections = await self.get_connections()
        _LOGGER.debug("Found %d connection(s): %s", len(connections), connections)

        dashboard = await self.get_dashboard()
        tariff = self._parse_tariff_rates(dashboard)

        result = UsageData()

        # --- Daily: try today, fall back to yesterday ---
        for target_date in (today, yesterday):
            candidate = UsageData(data_date=target_date)
            has_real = False

            for conn in connections:
                raw = await self._get_usage_range(conn, target_date, target_date + timedelta(days=1))
                values = raw.get("values", [])
                unit = raw.get("unit", "WH")
                market = conn.market_segment.lower()

                if market == MARKET_ELECTRICITY:
                    candidate.has_electricity = True
                    peak, off_peak = self._aggregate_consumption(values, unit)
                    candidate.electricity_peak_kwh += peak
                    candidate.electricity_off_peak_kwh += off_peak
                    if peak + off_peak > 0:
                        has_real = True
                elif market == MARKET_GAS:
                    candidate.has_gas = True
                    gas = self._aggregate_gas(values, unit)
                    if candidate.gas_m3 is None:
                        candidate.gas_m3 = 0.0
                    candidate.gas_m3 += gas
                    if gas > 0:
                        has_real = True

            if has_real:
                result = candidate
                break

        # If no real data found for either day, use yesterday's zeros
        if result.data_date is None:
            result.data_date = yesterday

        _LOGGER.debug(
            "Daily data (%s): elec %.3f+%.3f kWh, gas %s m³",
            result.data_date,
            result.electricity_peak_kwh,
            result.electricity_off_peak_kwh,
            result.gas_m3,
        )

        # --- Month-to-date: fetch from start of month to yesterday (real data) ---
        # We fetch up through yesterday since today is often not yet available.
        # If month started today (1st), there's nothing to fetch yet.
        if month_start < yesterday:
            for conn in connections:
                raw = await self._get_usage_range(conn, month_start, yesterday + timedelta(days=1))
                values = raw.get("values", [])
                unit = raw.get("unit", "WH")
                market = conn.market_segment.lower()

                if market == MARKET_ELECTRICITY:
                    peak, off_peak = self._aggregate_consumption(values, unit)
                    result.electricity_month_peak_kwh += peak
                    result.electricity_month_off_peak_kwh += off_peak
                elif market == MARKET_GAS:
                    pass  # gas monthly to-do

        _LOGGER.debug(
            "Month-to-date (%s→%s): elec %.3f+%.3f kWh",
            month_start, yesterday,
            result.electricity_month_peak_kwh,
            result.electricity_month_off_peak_kwh,
        )

        # --- Costs and forecasts ---
        if tariff is not None:
            # Daily variable cost (for the daily reading date)
            result.electricity_today_cost_eur = round(
                tariff.variable_cost(
                    result.electricity_peak_kwh,
                    result.electricity_off_peak_kwh,
                ),
                2,
            )

            # Month-to-date cost (variable + proportional fixed charges)
            result.electricity_month_cost_eur = round(
                tariff.total_monthly_cost(
                    result.electricity_month_peak_kwh,
                    result.electricity_month_off_peak_kwh,
                ),
                2,
            )

            # Expected full-month cost: Vandebron's own advance payment calculation
            result.electricity_month_expected_cost_eur = round(
                tariff.advance_payment_monthly, 2
            )

            # Expected monthly kWh from SJV annual profile ÷ 12
            if tariff.annual_expected_kwh > 0:
                result.electricity_month_expected_kwh = round(
                    tariff.annual_expected_kwh / 12, 1
                )

        return result
