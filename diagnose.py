"""Standalone diagnostic script — run with: python diagnose.py EMAIL PASSWORD

Walks through every API call step by step and prints the raw response so you
can see exactly which one returns 400 (or whatever error Vandebron now gives).
"""
import asyncio
import json
import sys
from datetime import date, timedelta

import aiohttp

EMAIL = sys.argv[1] if len(sys.argv) > 1 else input("Email: ")
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else input("Password: ")

# Copy the same URLs from api.py
_AUTH_URL = "https://vandebron.nl/auth/realms/vandebron/protocol/openid-connect/auth"
_TOKEN_URL = "https://vandebron.nl/auth/realms/vandebron/protocol/openid-connect/token"
_USER_INFO_URL = "https://mijn.vandebron.nl/api/authentication/userinfo"
_ENERGY_CONSUMERS_URL = "https://mijn.vandebron.nl/api/v1/energyConsumers/{org_id}"
_USAGE_URL = "https://mijn.vandebron.nl/api/consumers/{user_id}/connections/{conn_id}/usage"
_DASHBOARD_URL = "https://mijn.vandebron.nl/api/consumers/{user_id}/dashboard"

import html as html_module
import re
import uuid
from urllib.parse import parse_qs, urlparse


async def main():
    # ------------------------------------------------------------------ auth
    # Try direct password grant first (simpler, no form scraping needed).
    # Some Keycloak clients allow this; if Vandebron restricted it we fall back
    # to the browser code-flow below.
    print("\n[1] Trying direct password grant …")
    token: str | None = None
    async with aiohttp.ClientSession() as s:
        async with s.post(
            _TOKEN_URL,
            data={
                "grant_type": "password",
                "client_id": "website",
                "username": EMAIL,
                "password": PASSWORD,
                "scope": "openid",
            },
        ) as resp:
            print(f"  → status {resp.status}")
            data = await resp.json(content_type=None)
            if resp.status == 200 and "access_token" in data:
                token = str(data["access_token"])
                print(f"  Password grant WORKED. Token: {token[:30]}…")
            else:
                print(f"  Password grant failed: {data.get('error_description') or data}")

    if token is None:
        # Fall back to browser-emulating code-flow with a proper User-Agent.
        print("\n[2] Falling back to browser code-flow …")
        browser_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
        }
        async with aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(unsafe=True),
            headers=browser_headers,
        ) as auth_session:
            params = {
                "client_id": "website",
                "redirect_uri": "https://mijn.vandebron.nl/",
                "state": str(uuid.uuid4()),
                "response_mode": "fragment",
                "response_type": "code",
                "scope": "openid",
                "nonce": str(uuid.uuid4()),
            }
            async with auth_session.get(_AUTH_URL, params=params) as resp:
                print(f"  [2a] Login page → status {resp.status}")
                text = await resp.text()
                if resp.status != 200:
                    print("  BODY:", text[:500])
                    return

            match = re.search(r'<form[^>]+action="([^"]+)"', text)
            if not match:
                print("  ERROR: no login form found")
                return
            login_url = html_module.unescape(match.group(1))
            print(f"  Form action: {login_url[:80]}…")

            async with auth_session.post(
                login_url,
                data={"username": EMAIL, "password": PASSWORD, "login": "Log in"},
                allow_redirects=False,
                headers={
                    **browser_headers,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "https://vandebron.nl",
                    "Referer": str(resp.url),
                },
            ) as resp2:
                print(f"  [2b] Login submit → status {resp2.status}")
                location = resp2.headers.get("Location", "")
                print(f"  Location: {location[:120]}")
                if resp2.status not in (301, 302):
                    body2 = await resp2.text()
                    errors = re.findall(r'message--error[^>]*>(.*?)</p>', body2, re.S)
                    print(f"  Errors: {[e.strip() for e in errors]}")
                    print("  Code-flow also failed — Vandebron may block automated logins entirely.")
                    return

            parsed = urlparse(location)
            params_qs = parse_qs(parsed.fragment)
            if "code" not in params_qs:
                print("  ERROR: no auth code in redirect fragment")
                print("  Fragment:", parsed.fragment[:300])
                return
            auth_code = params_qs["code"][0]
            print(f"  Auth code: {auth_code[:20]}…")

            async with auth_session.post(
                _TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": "website",
                    "code": auth_code,
                    "redirect_uri": "https://mijn.vandebron.nl/",
                },
            ) as resp3:
                print(f"  [2c] Token exchange → status {resp3.status}")
                data = await resp3.json(content_type=None)
                if "access_token" not in data:
                    print("  BODY:", data)
                    return
                token = str(data["access_token"])
                print(f"  Token: {token[:30]}…")

    headers = {"Authorization": f"Bearer {token}"}

    # ------------------------------------------------------------------ user info
    print("\n[4] Fetching user info …")
    async with aiohttp.ClientSession() as session:
        async with session.get(_USER_INFO_URL, headers=headers) as resp:
            print(f"  → status {resp.status}")
            data = await resp.json(content_type=None)
            if resp.status != 200:
                print("  BODY:", data)
                return
            user_id = data.get("id")
            org_id = data.get("organizationId")
            print(f"  user_id={user_id}  org_id={org_id}")
            print(f"  Full userinfo: {json.dumps(data, indent=2)}")

        # ------------------------------------------------------------------ connections
        print("\n[5] Fetching energy consumers (connections) …")
        url = _ENERGY_CONSUMERS_URL.format(org_id=org_id)
        async with session.get(url, headers=headers) as resp:
            print(f"  → status {resp.status}  url={url}")
            data = await resp.json(content_type=None)
            if resp.status != 200:
                print("  BODY:", data)
                return
            connections = [
                (c["marketSegment"], c["connectionId"])
                for addr in data.get("shippingAddresses", [])
                for c in addr.get("connections", [])
            ]
            print(f"  connections: {connections}")
            print(f"  Full energyConsumers: {json.dumps(data, indent=2)}")

        today = date.today()
        yesterday = today - timedelta(days=1)
        month_start = today.replace(day=1)
        conn_ids = [conn_id for _, conn_id in connections]

        # ------------------------------------------------------------------ dashboard (old, expected broken)
        print("\n[6] Fetching dashboard (old endpoint — expected broken) …")
        url = _DASHBOARD_URL.format(user_id=user_id)
        async with session.get(url, headers=headers) as resp:
            print(f"  → status {resp.status}  url={url}")
            if resp.status != 200:
                print(f"  BODY: {(await resp.text())[:300]}")

        # ------------------------------------------------------------------ new: v2 costs
        # The browser uses org_id here, not user_id
        for label, consumer_id in [("user_id", user_id), ("org_id", org_id)]:
            print(f"\n[7] Fetching v2 connection costs with {label} …")
            url = f"https://mijn.vandebron.nl/api/v2/consumers/{consumer_id}/connections/costs"
            params = {
                "connectionIds": ",".join(conn_ids),
                "startDate": month_start.isoformat(),
                "endDate": today.isoformat(),
            }
            async with session.get(url, params=params, headers=headers) as resp:
                print(f"  → status {resp.status}  url={resp.url}")
                body = await resp.text()
                if resp.status != 200:
                    print(f"  BODY: {body[:300]}")
                else:
                    print(f"  BODY:\n{json.dumps(json.loads(body), indent=2)}")

        # ------------------------------------------------------------------ new: expectedcosts
        for market, conn_id in connections:
            print(f"\n[8] Fetching expectedcosts for {market} {conn_id} …")
            url = (
                f"https://mijn.vandebron.nl/api/consumers/{user_id}"
                f"/connections/{conn_id}/expectedcosts"
            )
            params = {
                "startDate": month_start.isoformat(),
                "endDate": (today.replace(day=1).replace(month=today.month % 12 + 1)
                            if today.month < 12
                            else today.replace(year=today.year + 1, month=1, day=1)).isoformat(),
                "billAsSingle": "true",
            }
            async with session.get(url, params=params, headers=headers) as resp:
                print(f"  → status {resp.status}  url={resp.url}")
                body = await resp.text()
                if resp.status != 200:
                    print(f"  BODY: {body[:600]}")
                else:
                    print(f"  BODY:\n{json.dumps(json.loads(body), indent=2)}")

        # ------------------------------------------------------------------ usage (existing)
        for market, conn_id in connections:
            print(f"\n[9] Fetching hourly usage for {market} {conn_id} …")
            url = _USAGE_URL.format(user_id=user_id, conn_id=conn_id)
            params = {
                "resolution": "Hours",
                "startDateTime": f"{yesterday.isoformat()}T00:15:00.000",
                "endDateTime": f"{today.isoformat()}T00:00:00.000",
            }
            async with session.get(url, params=params, headers=headers) as resp:
                print(f"  → status {resp.status}")
                body = await resp.text()
                if resp.status != 200:
                    print(f"  BODY: {body[:600]}")
                else:
                    d = json.loads(body)
                    print(f"  unit={d.get('unit')}  intervals={len(d.get('values', []))}")

        # ------------------------------------------------------------------ forecast usage (ff=true)
        year_start = today.replace(month=1, day=1)
        year_end = today.replace(year=today.year + 1, month=1, day=1)
        for market, conn_id in connections:
            for label, cid in [("user_id", user_id), ("org_id", org_id)]:
                print(f"\n[10] Forecast usage (ff=true) for {market} with {label} …")
                url = f"https://mijn.vandebron.nl/api/consumers/{cid}/connections/{conn_id}/usage"
                params = {
                    "resolution": "Days",
                    "startDate": year_start.isoformat(),
                    "endDate": year_end.isoformat(),
                    "ff": "true",
                }
                async with session.get(url, params=params, headers=headers) as resp:
                    print(f"  → status {resp.status}  url={resp.url}")
                    body = await resp.text()
                    if resp.status != 200:
                        print(f"  BODY: {body[:400]}")
                    else:
                        d = json.loads(body)
                        vals = d.get("values", [])
                        print(f"  unit={d.get('unit')}  total intervals={len(vals)}")
                        if vals:
                            print(f"  First value keys: {list(vals[0].keys())}")
                            print(f"  First value: {vals[0]}")
                            print(f"  Last value:  {vals[-1]}")
                        # Try all plausible date field names
                        date_key = next(
                            (k for k in (vals[0] if vals else {})
                             if "date" in k.lower() or "time" in k.lower()),
                            None,
                        )
                        print(f"  Date key detected: {date_key!r}")
                        if date_key:
                            june_vals = [
                                v for v in vals
                                if str(v.get(date_key, "")).startswith(f"{today.year}-{today.month:02d}")
                            ]
                            print(f"  {today.strftime('%B')} intervals={len(june_vals)}")
                            if june_vals:
                                divisor = 1000.0 if (d.get("unit") or "").upper() == "WH" else 1.0
                                total_peak = sum(float(v.get("consumptionPeak") or 0) for v in june_vals) / divisor
                                total_off = sum(float(v.get("consumptionOffPeak") or 0) for v in june_vals) / divisor
                                print(f"  {today.strftime('%B')} total kWh = {round(total_peak + total_off, 2)} (peak={round(total_peak,2)} off={round(total_off,2)})")
                        break  # stop at first that works

        # grab contractId from connections data fetched in step 5
        contract_id = None
        async with session.get(
            f"https://mijn.vandebron.nl/api/v1/energyConsumers/{org_id}", headers=headers
        ) as r:
            ec = await r.json(content_type=None)
        for addr in ec.get("shippingAddresses", []):
            for c in addr.get("connections", []):
                if c.get("connectionId", "").lower() == connections[0][1].lower():
                    contract_id = (c.get("contract") or {}).get("contractId")
        print(f"\n  contractId={contract_id}")

        for market, conn_id in connections:
            if market.lower() != "electricity":
                continue
            print(f"\n[11] Trying tariff/contract price endpoints …")
            candidates = [
                # contract-scoped
                f"https://mijn.vandebron.nl/api/consumers/{user_id}/contracts",
                f"https://mijn.vandebron.nl/api/v2/consumers/{org_id}/contracts",
                f"https://mijn.vandebron.nl/api/consumers/{user_id}/contracts/{contract_id}",
                f"https://mijn.vandebron.nl/api/consumers/{user_id}/contracts/{contract_id}/prices",
                f"https://mijn.vandebron.nl/api/v2/consumers/{org_id}/contracts/{contract_id}/prices",
                # connection-scoped variants
                f"https://mijn.vandebron.nl/api/consumers/{user_id}/connections/{conn_id}/currentcontract",
                f"https://mijn.vandebron.nl/api/consumers/{org_id}/connections/{conn_id}/contractprices",
                # top-level tariff
                f"https://mijn.vandebron.nl/api/consumers/{user_id}/tariffs",
                f"https://mijn.vandebron.nl/api/v2/consumers/{org_id}/tariffs",
            ]
            for url in candidates:
                async with session.get(url, headers=headers) as resp:
                    body = await resp.text()
                    short = url.replace("https://mijn.vandebron.nl/api/", "")
                    print(f"  {resp.status}  {short}")
                    if resp.status == 200:
                        print(f"  BODY:\n{json.dumps(json.loads(body), indent=2)}")

        # ------------------------------------------------------------------ contract prices (found in DevTools)
        if contract_id:
            print(f"\n[12] Fetching contract prices …")
            url = f"https://mijn.vandebron.nl/api/v1/energyConsumers/{org_id}/contracts/{contract_id}/prices"
            for price_date in [today.isoformat(), "2027-01-01"]:
                async with session.get(url, params={"priceDate": price_date}, headers=headers) as resp:
                    body = await resp.text()
                    print(f"  priceDate={price_date}  → status {resp.status}")
                    if resp.status == 200:
                        print(f"  BODY:\n{json.dumps(json.loads(body), indent=2)}")
                        break

    print("\nDone.")


asyncio.run(main())
