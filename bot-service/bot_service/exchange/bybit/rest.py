from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
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

_BASE_URL = "https://api.bybit.com"
_RECV_WINDOW = "5000"
_RETRY_DELAYS = [1.0, 2.0, 4.0]


def _sign_bybit(secret: str, timestamp: str, api_key: str, recv_window: str, payload: str) -> str:
    """HMAC-SHA256 hex-encoded signature. Secret does not escape this function."""
    msg = timestamp + api_key + recv_window + payload
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


class BybitRESTClient:
    """Signed httpx REST client for Bybit v5 API.

    Credentials are obtained at sign-time only and never stored on the instance.
    Each request creates a fresh httpx.AsyncClient — no shared session state.
    """

    def _signed_headers(self, timestamp: str, recv_window: str, payload: str) -> dict[str, str]:
        """Build X-BAPI-* headers; credential values do not outlive this method."""
        settings = get_settings()
        secret = settings.bybit_api_secret.get_secret_value()
        api_key = settings.bybit_api_key.get_secret_value()
        sign = _sign_bybit(secret, timestamp, api_key, recv_window, payload)
        # secret, api_key go out of scope after return — not stored on self
        return {
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGN": sign,
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """Send a signed request with 1 initial + 3 retries (4 total). Returns parsed JSON result."""
        # Sort params so the query string passed to httpx matches the signed query string
        sorted_params: dict[str, str] | None = None
        if method.upper() == "GET" and params:
            _sp = dict(sorted(params.items()))
            payload = "&".join(f"{k}={v}" for k, v in _sp.items())
            sorted_params = _sp
        else:
            payload = json.dumps(json_body) if json_body else ""

        url = _BASE_URL + path

        last_exc: Exception = ExchangeRESTError("no attempts made")
        for attempt, delay in enumerate([0.0] + _RETRY_DELAYS):
            if delay > 0:
                await asyncio.sleep(delay)
            # Fresh timestamp per attempt so signature is never stale on retries
            timestamp = str(int(time.time() * 1000))
            headers = self._signed_headers(timestamp, _RECV_WINDOW, payload)
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
                        "bybit_rest_5xx",
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
                # Bybit business-level error: retCode != 0
                ret_code = data.get("retCode", 0)
                if ret_code != 0:
                    raise ExchangeRESTError(
                        f"Bybit error {ret_code}: {_scrub_for_error(str(data.get('retMsg', '')))[:200]}"
                    )
                return data.get("result")
            except ExchangeRESTError:
                raise
            except httpx.HTTPError as exc:
                last_exc = ExchangeRESTError(_scrub_for_error(str(exc))[:200])
                log.warning(
                    "bybit_rest_http_error",
                    attempt=attempt + 1,
                    error=_scrub_for_error(str(exc)[:200]),
                )
        raise last_exc

    async def place_order(self, req: OrderRequest) -> PlacedOrder:
        body: dict[str, Any] = {
            "category": "spot",
            "symbol": req.symbol,
            "side": req.side.capitalize(),  # Bybit uses "Buy" / "Sell"
            "orderType": req.order_type.capitalize(),  # "Limit" / "Market"
            "qty": str(req.size),
            "orderLinkId": req.client_order_id or str(uuid.uuid4()),
        }
        if req.limit_price is not None:
            body["price"] = str(req.limit_price)
        data = await self._request("POST", "/v5/order/create", json_body=body)
        return PlacedOrder(
            order_id=data["orderId"],
            client_order_id=req.client_order_id,
            status="placed",
            ts_exchange=int(time.time() * 1000),
        )

    async def cancel_order(self, order_id: str, symbol: str) -> None:
        body: dict[str, Any] = {
            "category": "spot",
            "symbol": symbol,
            "orderId": order_id,
        }
        await self._request("POST", "/v5/order/cancel", json_body=body)

    async def get_open_orders(self, symbol: str) -> list[OpenOrder]:
        params = {"category": "spot", "symbol": symbol}
        data = await self._request("GET", "/v5/order/realtime", params=params)
        orders: list[OpenOrder] = []
        for item in (data or {}).get("list", []):
            orders.append(
                OpenOrder(
                    order_id=item["orderId"],
                    symbol=item.get("symbol", symbol),
                    side=item.get("side", "").lower(),
                    order_type=item.get("orderType", "").lower(),
                    size=float(item.get("qty", 0)),
                    limit_price=float(item["price"]) if item.get("price") else None,
                    status=item.get("orderStatus", "open").lower(),
                    ts_placed=int(item.get("createdTime", 0)),
                )
            )
        return orders
