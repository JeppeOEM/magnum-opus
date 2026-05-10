from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import uuid
from typing import Any

import httpx
import structlog

from bot_service.config import get_settings
from bot_service.exchange import (
    ExchangeRESTError,
    OpenOrder,
    OrderRequest,
    PlacedOrder,
    _scrub_for_error,
)

log = structlog.get_logger()

_BASE_URL = "https://api-futures.kucoin.com"
_RETRY_DELAYS = [1.0, 2.0, 4.0]


def _sign_kucoin(secret: str, timestamp: str, method: str, path: str, body_str: str) -> str:
    """HMAC-SHA256 sign, base64-encoded. Secret does not escape this function."""
    msg = timestamp + method.upper() + path + body_str
    return base64.b64encode(
        hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
    ).decode()


def _sign_passphrase(secret: str, passphrase: str) -> str:
    """KuCoin v2: passphrase header is HMAC-SHA256(secret, passphrase), base64-encoded."""
    return base64.b64encode(
        hmac.new(secret.encode(), passphrase.encode(), hashlib.sha256).digest()
    ).decode()


class KuCoinRESTClient:
    """Signed httpx REST client for KuCoin Futures API (v1, key version 2).

    Credentials are obtained at sign-time only and never stored on the instance.
    Each request creates a fresh httpx.AsyncClient — no shared session state.
    """

    def _signed_headers(self, method: str, path: str, body_str: str) -> dict[str, str]:
        """Build KC-API-* headers; credential values do not outlive this method."""
        settings = get_settings()
        secret = settings.kucoin_api_secret.get_secret_value()
        passphrase = settings.kucoin_api_passphrase.get_secret_value()
        api_key = settings.kucoin_api_key.get_secret_value()
        timestamp = str(int(time.time() * 1000))
        sign = _sign_kucoin(secret, timestamp, method, path, body_str)
        signed_pass = _sign_passphrase(secret, passphrase)
        # secret, passphrase, api_key go out of scope after return — not stored on self
        return {
            "KC-API-KEY": api_key,
            "KC-API-SIGN": sign,
            "KC-API-TIMESTAMP": timestamp,
            "KC-API-PASSPHRASE": signed_pass,
            "KC-API-KEY-VERSION": "2",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """Send a signed request with 1 initial + 3 retries (4 total). Returns parsed JSON data."""
        body_str = json.dumps(json_body) if json_body else ""
        # Sort params so the query string passed to httpx matches the signed query string
        sorted_params: dict[str, str] | None = None
        if params and method.upper() == "GET":
            sorted_params = dict(sorted(params.items()))
            qs = "&".join(f"{k}={v}" for k, v in sorted_params.items())
            sign_path = f"{path}?{qs}"
        else:
            sign_path = path

        url = _BASE_URL + path

        last_exc: Exception = ExchangeRESTError("no attempts made")
        for attempt, delay in enumerate([0.0] + _RETRY_DELAYS):
            if delay > 0:
                await asyncio.sleep(delay)
            # Fresh timestamp per attempt so signature is never stale on retries
            headers = self._signed_headers(method, sign_path, body_str)
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(10.0, read=30.0)
                ) as client:
                    resp = await client.request(
                        method, url, headers=headers,
                        params=sorted_params if sorted_params is not None else params,
                        json=json_body,
                    )
                if resp.status_code >= 500:
                    last_exc = ExchangeRESTError(
                        f"HTTP {resp.status_code}: {_scrub_for_error(resp.text)[:200]}"
                    )
                    log.warning(
                        "kucoin_rest_5xx",
                        attempt=attempt + 1,
                        status=resp.status_code,
                        path=path,
                    )
                    continue
                if resp.status_code >= 400:
                    raise ExchangeRESTError(
                        f"HTTP {resp.status_code}: {_scrub_for_error(resp.text)[:200]}"
                    )
                data = resp.json()
                # KuCoin business-level error: code != "200000"
                if str(data.get("code", "200000")) != "200000":
                    raise ExchangeRESTError(
                        f"KuCoin error {data.get('code')}: {_scrub_for_error(str(data.get('msg', '')))[:200]}"
                    )
                return data.get("data")
            except ExchangeRESTError:
                raise
            except httpx.HTTPError as exc:
                last_exc = ExchangeRESTError(_scrub_for_error(str(exc))[:200])
                log.warning(
                    "kucoin_rest_http_error",
                    attempt=attempt + 1,
                    error=_scrub_for_error(str(exc)[:200]),
                )
        raise last_exc

    async def place_order(self, req: OrderRequest) -> PlacedOrder:
        body: dict[str, Any] = {
            "clientOid": req.client_order_id or str(uuid.uuid4()),
            "side": req.side,
            "symbol": req.symbol,
            "type": req.order_type,
            "size": str(req.size),
        }
        if req.limit_price is not None:
            body["price"] = str(req.limit_price)
        if req.stop_price is not None:
            # KuCoin "up" fires when price rises — use for buy stops; "down" for sell stops
            body["stop"] = "up" if req.side == "buy" else "down"
            body["stopPrice"] = str(req.stop_price)
            body["stopPriceType"] = "TP"
        data = await self._request("POST", "/api/v1/orders", json_body=body)
        return PlacedOrder(
            order_id=data["orderId"],
            client_order_id=req.client_order_id,
            status="placed",
            ts_exchange=int(time.time() * 1000),
        )

    async def cancel_order(self, order_id: str, symbol: str) -> None:  # noqa: ARG002
        safe_id = urllib.parse.quote(order_id, safe="")
        await self._request("DELETE", f"/api/v1/orders/{safe_id}")

    async def get_open_orders(self, symbol: str) -> list[OpenOrder]:
        data = await self._request("GET", "/api/v1/openOrders", params={"symbol": symbol})
        orders: list[OpenOrder] = []
        if not isinstance(data, list):
            return orders
        for item in data:
            orders.append(
                OpenOrder(
                    order_id=item["id"],
                    symbol=item.get("symbol", symbol),
                    side=item.get("side", ""),
                    order_type=item.get("type", ""),
                    size=float(item.get("size", 0)),
                    limit_price=float(item["price"]) if item.get("price") else None,
                    status=item.get("status", "open"),
                    ts_placed=int(item.get("createdAt", 0)),
                )
            )
        return orders
