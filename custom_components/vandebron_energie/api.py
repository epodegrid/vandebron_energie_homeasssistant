"""Vandebron Energie async API client.

Auth flow: Keycloak OIDC with username/password, yields a Bearer access token.
All data calls use that token against the mijn.vandebron.nl API.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

_LOGGER = logging.getLogger(__name__)
_NL_TZ = ZoneInfo("Europe/Amsterdam")


def is_nl_peak_hour() -> bool:
    """Return True during peak tariff hours: Mon–Fri 07:00–23:00 Amsterdam time."""
    now = datetime.now(_NL_TZ)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return 7 <= now.hour < 23

_TOKEN_URL = "https://vandebron.nl/auth/realms/vandebron/protocol/openid-connect/token"
_USER_INFO_URL = "https://mijn.vandebron.nl/api/authentication/userinfo"
_ENERGY_CONSUMERS_URL = "https://mijn.vandebron.nl/api/v1/energyConsumers/{org_id}"
_USAGE_URL = "https://mijn.vandebron.nl/api/consumers/{user_id}/connections/{conn_id}/usage"
# org_id-scoped endpoint that returns per-day variable + fixed costs
_COSTS_V2_URL = "https://mijn.vandebron.nl/api/v2/consumers/{org_id}/connections/costs"
# Projects variable + fixed costs for the full billing period
_EXPECTED_COSTS_URL = "https://mijn.vandebron.nl/api/consumers/{user_id}/connections/{conn_id}/expectedcosts"
# Per-component contract prices (energy + ODN + tax) as of a given date
_CONTRACT_PRICES_URL = "https://mijn.vandebron.nl/api/v1/energyConsumers/{org_id}/contracts/{contract_id}/prices"

MARKET_ELECTRICITY = "electricity"
MARKET_GAS = "gas"


@dataclass
class Connection:
    """A single energy connection (electricity or gas)."""

    market_segment: str  # MARKET_ELECTRICITY or MARKET_GAS
    conn_id: str
    contract_id: str | None = None


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

    # ---------- Real-time ----------
    electricity_current_rate_eur: float | None = None   # all-in rate right now (unavailable when dashboard is broken)


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
        """Authenticate via OIDC direct password grant. Populates self._token."""
        async with self._session.post(
            _TOKEN_URL,
            data={
                "grant_type": "password",
                "client_id": "website",
                "username": self._username,
                "password": self._password,
                "scope": "openid",
            },
        ) as resp:
            if resp.status in (401, 403):
                raise VandebronAuthError("Invalid Vandebron credentials")
            resp.raise_for_status()
            data = await resp.json()

        if "access_token" not in data:
            raise VandebronAuthError("Token endpoint did not return an access_token")
        self._token = str(data["access_token"])
        await self._fetch_user_info()

    async def _fetch_user_info(self) -> None:
        async with self._session.get(
            _USER_INFO_URL, headers=self._auth_headers
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

        self._user_id = data["id"]
        self._org_id = data["organizationId"]

    # ------------------------------------------------------------------
    # Costs
    # ------------------------------------------------------------------

    async def get_costs(
        self, conn_ids: list[str], start: date, end: date
    ) -> dict[str, Any]:
        """Fetch per-day variable + fixed costs for the given connections and date range."""
        url = _COSTS_V2_URL.format(org_id=self._org_id)
        async with self._session.get(
            url,
            params={
                "connectionIds": ",".join(conn_ids),
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
            },
            headers=self._auth_headers,
        ) as resp:
            if resp.status != 200:
                _LOGGER.warning("Costs v2 returned %s — cost sensors will be unavailable", resp.status)
                return {}
            return await resp.json()

    async def get_expected_costs(
        self, conn_id: str, start: date, end: date
    ) -> dict[str, Any]:
        """Fetch projected variable + fixed costs for the full billing period."""
        url = _EXPECTED_COSTS_URL.format(user_id=self._user_id, conn_id=conn_id)
        async with self._session.get(
            url,
            params={
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "billAsSingle": "true",
            },
            headers=self._auth_headers,
        ) as resp:
            if resp.status != 200:
                _LOGGER.warning("Expected costs returned %s", resp.status)
                return {}
            return await resp.json()

    async def get_monthly_sjv_kwh(
        self, conn_id: str, month_start: date, next_month_start: date
    ) -> float | None:
        """Sum sjvEstimatedConsumption for every day in the current month.

        The ff=true flag returns SJV-profile estimates for future days alongside
        actuals for past days. Summing all sjvEstimatedConsumption values gives
        the expected monthly consumption the Vandebron frontend displays.
        """
        url = _USAGE_URL.format(user_id=self._user_id, conn_id=conn_id)
        async with self._session.get(
            url,
            params={
                "resolution": "Days",
                "startDate": month_start.isoformat(),
                "endDate": next_month_start.isoformat(),
                "ff": "true",
            },
            headers=self._auth_headers,
        ) as resp:
            if resp.status != 200:
                _LOGGER.warning("SJV forecast returned %s", resp.status)
                return None
            data = await resp.json()

        unit = data.get("unit", "WH")
        divisor = 1000.0 if unit.upper() == "WH" else 1.0
        total = sum(
            float(v.get("sjvEstimatedConsumption") or 0)
            for v in data.get("values", [])
        ) / divisor
        return round(total, 1) if total > 0 else None

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
                        contract_id=(con.get("contract") or {}).get("contractId"),
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

    async def get_contract_prices(self, contract_id: str) -> list[dict[str, Any]]:
        """Fetch price components for the given contract as of today."""
        url = _CONTRACT_PRICES_URL.format(org_id=self._org_id, contract_id=contract_id)
        async with self._session.get(
            url,
            params={"priceDate": date.today().isoformat()},
            headers=self._auth_headers,
        ) as resp:
            if resp.status != 200:
                _LOGGER.warning("Contract prices returned %s", resp.status)
                return []
            return await resp.json()

    def _parse_current_rates(
        self, prices: list[dict[str, Any]]
    ) -> tuple[float | None, float | None]:
        """Return (peak_rate_eur, off_peak_rate_eur) incl. VAT from contract prices.

        Rate = energy_component.priceTaxed + lowest-range EnergyTax.priceTaxed.
        Matches the "total delivery price" shown in the Vandebron frontend.
        Falls back to Base when Peak/OffPeak are absent (single-tariff meters).
        """
        peak_taxed: float | None = None
        off_peak_taxed: float | None = None
        base_taxed: float | None = None
        energy_tax_taxed: float | None = None
        energy_tax_min_range: int = 999_999_999

        for item in prices:
            ptype = item.get("priceComponentType", "")
            if item.get("priceUnit") != "KWh":
                continue
            price_taxed = float(item.get("priceTaxed") or 0)
            if ptype == "Peak":
                peak_taxed = price_taxed
            elif ptype == "OffPeak":
                off_peak_taxed = price_taxed
            elif ptype == "Base":
                base_taxed = price_taxed
            elif ptype == "EnergyTax":
                range_begin = int(item.get("rangeBegin") or 0)
                if range_begin < energy_tax_min_range:
                    energy_tax_taxed = price_taxed
                    energy_tax_min_range = range_begin

        tax = energy_tax_taxed or 0.0
        peak = peak_taxed if peak_taxed is not None else base_taxed
        off_peak = off_peak_taxed if off_peak_taxed is not None else base_taxed

        return (
            round(peak + tax, 5) if peak is not None else None,
            round(off_peak + tax, 5) if off_peak is not None else None,
        )

    # ------------------------------------------------------------------
    # Main data fetch
    # ------------------------------------------------------------------

    async def fetch_all_data(self) -> UsageData:
        """Fetch daily usage, monthly usage and costs.

        Vandebron has ~1 day lag on smart meter readings: today's consumptionPeak
        and consumptionOffPeak are often zero until the next day. We try today
        first and fall back to yesterday for the daily sensor values.
        """
        today = date.today()
        yesterday = today - timedelta(days=1)
        month_start = today.replace(day=1)
        next_month_start = (month_start + timedelta(days=32)).replace(day=1)

        connections = await self.get_connections()
        _LOGGER.debug("Found %d connection(s): %s", len(connections), connections)

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

        # --- Costs from the v2 costs API ---
        elec_conns = [c for c in connections if c.market_segment.lower() == MARKET_ELECTRICITY]
        if elec_conns:
            elec_conn_ids = [c.conn_id for c in elec_conns]
            costs_data = await self.get_costs(elec_conn_ids, month_start, today)

            total_variable = float(costs_data.get("totalVariable") or 0.0)
            total_fixed = float(costs_data.get("totalFixed") or 0.0)

            if costs_data:
                # Month-to-date cost = sum of all daily entries
                result.electricity_month_cost_eur = round(total_variable + total_fixed, 2)

                # Today's cost: find the per-day entry matching data_date
                if result.data_date:
                    for conn_cost in costs_data.get("costs", []):
                        for day_val in conn_cost.get("values", []):
                            try:
                                reading_date = date.fromisoformat(day_val["readingDate"][:10])
                            except (KeyError, ValueError):
                                continue
                            if reading_date == result.data_date:
                                result.electricity_today_cost_eur = round(
                                    float(day_val.get("variableCosts") or 0)
                                    + float(day_val.get("fixedCosts") or 0),
                                    2,
                                )
                                break

            # Expected full-month cost and SJV kWh forecast
            expected_data = await self.get_expected_costs(
                elec_conns[0].conn_id, month_start, next_month_start
            )
            if expected_data:
                result.electricity_month_expected_cost_eur = round(
                    float(expected_data.get("expectedVariableCosts") or 0)
                    + float(expected_data.get("expectedFixedCosts") or 0),
                    2,
                )

            result.electricity_month_expected_kwh = await self.get_monthly_sjv_kwh(
                elec_conns[0].conn_id, month_start, next_month_start
            )

            # Real-time tariff rate
            if elec_conns[0].contract_id:
                prices = await self.get_contract_prices(elec_conns[0].contract_id)
                peak_rate, off_peak_rate = self._parse_current_rates(prices)
                result.electricity_current_rate_eur = (
                    peak_rate if is_nl_peak_hour() else off_peak_rate
                )

            _LOGGER.debug(
                "Costs: today=%.2f month=%.2f expected=%.2f",
                result.electricity_today_cost_eur or 0,
                result.electricity_month_cost_eur or 0,
                result.electricity_month_expected_cost_eur or 0,
            )

        return result
