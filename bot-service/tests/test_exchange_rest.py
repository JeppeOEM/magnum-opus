from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from bot_service.exchange import ExchangeRESTError
from bot_service.exchange.bybit.rest import BybitRESTClient, _sign_bybit
from bot_service.exchange.kucoin.rest import KuCoinRESTClient, _sign_kucoin, _sign_passphrase


# ---------------------------------------------------------------------------
# KuCoin signing — known test vectors (values pre-computed offline)
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_kucoin_sign_matches_expected_vector() -> None:
    result = _sign_kucoin(
        secret="c5c895a3-5c4b-4c33-b73b-d82b9f1a7b3f",
        timestamp="1685000000000",
        method="POST",
        path="/api/v1/orders",
        body_str='{"clientOid":"abc","side":"buy"}',
    )
    # Pre-computed: base64(HMAC-SHA256(secret, "1685000000000POST/api/v1/orders{...}"))
    assert result == "ujnZf0qpm2i3ovUWle0zKSJGKqkzziQy2z9rN0MtqvI="


@pytest.mark.l1
def test_kucoin_passphrase_sign_matches_expected_vector() -> None:
    result = _sign_passphrase(
        secret="c5c895a3-5c4b-4c33-b73b-d82b9f1a7b3f",
        passphrase="test-passphrase",
    )
    # Pre-computed: base64(HMAC-SHA256(secret, passphrase))
    assert result == "5a7XYsfhYWcUgUJ13ofpMEC4L/DMpiCNWxDmYvP70Vk="


@pytest.mark.l1
def test_kucoin_sign_includes_method_uppercase() -> None:
    secret = "s3cr3t"
    ts = "1000000000000"
    # "get" and "GET" must produce the same signature — method is always uppercased
    assert _sign_kucoin(secret, ts, "get", "/path", "") == _sign_kucoin(
        secret, ts, "GET", "/path", ""
    )


# ---------------------------------------------------------------------------
# Bybit signing — known test vectors (values pre-computed offline)
# ---------------------------------------------------------------------------


@pytest.mark.l1
def test_bybit_sign_matches_expected_vector() -> None:
    result = _sign_bybit(
        secret="fake-bybit-secret-for-testing",
        timestamp="1685000000000",
        api_key="test-bybit-key",
        recv_window="5000",
        payload="category=spot&symbol=BTCUSDT",
    )
    # Pre-computed: hex(HMAC-SHA256(secret, "1685000000000test-bybit-key5000category=spot&symbol=BTCUSDT"))
    assert result == "57a53302867cca302061bf372808fac725ae66b66ac871be3288463703350400"


@pytest.mark.l1
def test_bybit_sign_is_hex_not_base64() -> None:
    secret = "s3cr3t"
    sign = _sign_bybit(secret, "ts", "key", "5000", "payload")
    # hex string: only 0-9, a-f, length == 64
    assert len(sign) == 64
    assert all(c in "0123456789abcdef" for c in sign)


# ---------------------------------------------------------------------------
# KuCoin HTTP error handling
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_kucoin_5xx_retries_3_times_then_raises(httpx_mock: HTTPXMock) -> None:
    # 4 responses needed: 1 initial + 3 retries
    for _ in range(4):
        httpx_mock.add_response(status_code=500, text="Internal Server Error")

    client = KuCoinRESTClient()
    with pytest.raises(ExchangeRESTError, match="HTTP 500"):
        await client._request("GET", "/api/v1/openOrders", params={"symbol": "BTC-USDT"})

    assert len(httpx_mock.get_requests()) == 4


@pytest.mark.l1
async def test_kucoin_4xx_raises_immediately_no_retry(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=400, text="Bad Request")

    client = KuCoinRESTClient()
    with pytest.raises(ExchangeRESTError, match="HTTP 400"):
        await client._request("GET", "/api/v1/openOrders", params={"symbol": "BTC-USDT"})

    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.l1
async def test_kucoin_business_error_raises_immediately(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        status_code=200,
        json={"code": "300003", "msg": "Insufficient funds"},
    )
    client = KuCoinRESTClient()
    with pytest.raises(ExchangeRESTError, match="300003"):
        await client._request("GET", "/api/v1/openOrders", params={"symbol": "BTC-USDT"})

    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.l1
async def test_kucoin_success_returns_data(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        status_code=200,
        json={"code": "200000", "data": {"orderId": "abc123"}},
    )
    client = KuCoinRESTClient()
    result = await client._request("GET", "/api/v1/openOrders", params={"symbol": "BTC-USDT"})
    assert result == {"orderId": "abc123"}


# ---------------------------------------------------------------------------
# Bybit HTTP error handling
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_bybit_5xx_retries_3_times_then_raises(httpx_mock: HTTPXMock) -> None:
    for _ in range(4):
        httpx_mock.add_response(status_code=503, text="Service Unavailable")

    client = BybitRESTClient()
    with pytest.raises(ExchangeRESTError, match="HTTP 503"):
        await client._request("GET", "/v5/order/realtime", params={"category": "spot", "symbol": "BTCUSDT"})

    assert len(httpx_mock.get_requests()) == 4


@pytest.mark.l1
async def test_bybit_4xx_raises_immediately_no_retry(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(status_code=403, text="Forbidden")

    client = BybitRESTClient()
    with pytest.raises(ExchangeRESTError, match="HTTP 403"):
        await client._request("GET", "/v5/order/realtime", params={"category": "spot", "symbol": "BTCUSDT"})

    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.l1
async def test_bybit_business_error_raises_immediately(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        status_code=200,
        json={"retCode": 10001, "retMsg": "Params Error", "result": {}},
    )
    client = BybitRESTClient()
    with pytest.raises(ExchangeRESTError, match="10001"):
        await client._request("GET", "/v5/order/realtime", params={"category": "spot", "symbol": "BTCUSDT"})

    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.l1
async def test_bybit_success_returns_result(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        status_code=200,
        json={"retCode": 0, "retMsg": "OK", "result": {"list": []}},
    )
    client = BybitRESTClient()
    result = await client._request("GET", "/v5/order/realtime", params={"category": "spot", "symbol": "BTCUSDT"})
    assert result == {"list": []}


# ---------------------------------------------------------------------------
# Credential safety — no raw secret in error messages (5xx and 4xx paths)
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_kucoin_no_credential_in_error_on_5xx(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_secret = "my-super-secret-key"
    monkeypatch.setenv("KUCOIN_API_SECRET", raw_secret)

    for _ in range(4):
        httpx_mock.add_response(
            status_code=500,
            text=f"error containing {raw_secret} in body",
        )

    client = KuCoinRESTClient()
    with pytest.raises(ExchangeRESTError) as exc_info:
        await client._request("GET", "/api/v1/openOrders", params={"symbol": "BTC-USDT"})

    assert raw_secret not in str(exc_info.value)


@pytest.mark.l1
async def test_kucoin_no_credential_in_error_on_4xx(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_secret = "my-super-secret-key"
    monkeypatch.setenv("KUCOIN_API_SECRET", raw_secret)

    httpx_mock.add_response(
        status_code=400,
        text=f"error containing {raw_secret} in body",
    )

    client = KuCoinRESTClient()
    with pytest.raises(ExchangeRESTError) as exc_info:
        await client._request("GET", "/api/v1/openOrders", params={"symbol": "BTC-USDT"})

    assert raw_secret not in str(exc_info.value)


@pytest.mark.l1
async def test_bybit_no_credential_in_error_on_5xx(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_secret = "my-bybit-secret-xyz"
    monkeypatch.setenv("BYBIT_API_SECRET", raw_secret)

    for _ in range(4):
        httpx_mock.add_response(
            status_code=500,
            text=f"error containing {raw_secret} in body",
        )

    client = BybitRESTClient()
    with pytest.raises(ExchangeRESTError) as exc_info:
        await client._request("GET", "/v5/order/realtime", params={"category": "spot", "symbol": "BTCUSDT"})

    assert raw_secret not in str(exc_info.value)


@pytest.mark.l1
async def test_bybit_no_credential_in_error_on_4xx(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_secret = "my-bybit-secret-xyz"
    monkeypatch.setenv("BYBIT_API_SECRET", raw_secret)

    httpx_mock.add_response(
        status_code=400,
        text=f"error containing {raw_secret} in body",
    )

    client = BybitRESTClient()
    with pytest.raises(ExchangeRESTError) as exc_info:
        await client._request("GET", "/v5/order/realtime", params={"category": "spot", "symbol": "BTCUSDT"})

    assert raw_secret not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Story 26-3: get_recent_fills — KuCoin
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_kucoin_get_recent_fills_calls_correct_endpoint(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        status_code=200,
        json={
            "code": "200000",
            "data": {
                "items": [
                    {
                        "orderId": "fill-001",
                        "symbol": "XBTUSDTM",
                        "side": "buy",
                        "price": "30000.5",
                        "size": "0.001",
                        "fee": "0.0015",
                        "createdAt": 1685000000000,
                    }
                ]
            },
        },
    )

    client = KuCoinRESTClient()
    fills = await client.get_recent_fills(symbol="XBTUSDTM", since_ms=1684000000000)

    assert len(fills) == 1
    assert fills[0].order_id == "fill-001"
    assert fills[0].exchange == "kucoin"
    assert fills[0].symbol == "XBTUSDTM"
    assert fills[0].side == "buy"
    assert fills[0].fill_price == pytest.approx(30000.5)
    assert fills[0].fill_size == pytest.approx(0.001)
    assert fills[0].ts_exchange == 1685000000000

    req = httpx_mock.get_requests()[0]
    assert "/api/v1/fills" in str(req.url)
    assert "startAt=1684000000000" in str(req.url)
    assert "symbol=XBTUSDTM" in str(req.url)


@pytest.mark.l1
async def test_kucoin_get_recent_fills_empty_response(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        status_code=200,
        json={"code": "200000", "data": {"items": []}},
    )
    client = KuCoinRESTClient()
    fills = await client.get_recent_fills(symbol="BTCUSDT", since_ms=0)
    assert fills == []


# ---------------------------------------------------------------------------
# Story 26-3: get_recent_fills — Bybit
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_bybit_get_recent_fills_calls_correct_endpoint(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        status_code=200,
        json={
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "list": [
                    {
                        "orderId": "bybit-fill-001",
                        "symbol": "BTCUSDT",
                        "side": "Buy",
                        "orderStatus": "Filled",
                        "cumExecQty": "0.001",
                        "avgPrice": "30000.0",
                        "cumExecFee": "0.003",
                        "updatedTime": "1685000000000",
                    }
                ]
            },
        },
    )

    client = BybitRESTClient()
    fills = await client.get_recent_fills(symbol="BTCUSDT", since_ms=1684000000000)

    assert len(fills) == 1
    assert fills[0].order_id == "bybit-fill-001"
    assert fills[0].exchange == "bybit"
    assert fills[0].symbol == "BTCUSDT"
    assert fills[0].side == "buy"
    assert fills[0].fill_price == pytest.approx(30000.0)
    assert fills[0].fill_size == pytest.approx(0.001)
    assert fills[0].ts_exchange == 1685000000000

    req = httpx_mock.get_requests()[0]
    assert "/v5/order/history" in str(req.url)
    assert "startTime=1684000000000" in str(req.url)
    assert "symbol=BTCUSDT" in str(req.url)
    assert "orderStatus=Filled" in str(req.url)


@pytest.mark.l1
async def test_bybit_get_recent_fills_filters_non_filled(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        status_code=200,
        json={
            "retCode": 0,
            "result": {
                "list": [
                    {"orderId": "a", "symbol": "BTCUSDT", "side": "Buy",
                     "orderStatus": "Cancelled", "cumExecQty": "0", "avgPrice": "0",
                     "cumExecFee": "0", "updatedTime": "0"},
                    {"orderId": "b", "symbol": "BTCUSDT", "side": "Buy",
                     "orderStatus": "Filled", "cumExecQty": "0.001", "avgPrice": "30000",
                     "cumExecFee": "0.001", "updatedTime": "1685000000000"},
                ]
            },
        },
    )
    client = BybitRESTClient()
    fills = await client.get_recent_fills(symbol="BTCUSDT", since_ms=0)
    assert len(fills) == 1
    assert fills[0].order_id == "b"


# ---------------------------------------------------------------------------
# Story 28-3: pagination for get_recent_fills
# ---------------------------------------------------------------------------


@pytest.mark.l1
async def test_kucoin_get_recent_fills_paginates_multiple_pages(httpx_mock: HTTPXMock) -> None:
    """When totalPage=2, both pages are fetched and merged."""
    httpx_mock.add_response(
        status_code=200,
        json={
            "code": "200000",
            "data": {
                "items": [{"orderId": "f1", "symbol": "XBTUSDTM", "side": "buy",
                           "price": "50000", "size": "0.001", "fee": "0.001", "createdAt": 1000}],
                "totalPage": 2,
                "currentPage": 1,
            },
        },
    )
    httpx_mock.add_response(
        status_code=200,
        json={
            "code": "200000",
            "data": {
                "items": [{"orderId": "f2", "symbol": "XBTUSDTM", "side": "sell",
                           "price": "51000", "size": "0.001", "fee": "0.001", "createdAt": 2000}],
                "totalPage": 2,
                "currentPage": 2,
            },
        },
    )
    client = KuCoinRESTClient()
    fills = await client.get_recent_fills(symbol="XBTUSDTM", since_ms=0)

    assert len(fills) == 2
    assert fills[0].order_id == "f1"
    assert fills[1].order_id == "f2"
    # Two HTTP requests made
    assert len(httpx_mock.get_requests()) == 2
    # Second request has currentPage=2
    assert "currentPage=2" in str(httpx_mock.get_requests()[1].url)


@pytest.mark.l1
async def test_kucoin_get_recent_fills_single_page_no_extra_request(httpx_mock: HTTPXMock) -> None:
    """Single page (totalPage=1) makes exactly one HTTP request."""
    httpx_mock.add_response(
        status_code=200,
        json={
            "code": "200000",
            "data": {
                "items": [{"orderId": "f1", "symbol": "XBTUSDTM", "side": "buy",
                           "price": "50000", "size": "0.001", "fee": "0", "createdAt": 1000}],
                "totalPage": 1,
                "currentPage": 1,
            },
        },
    )
    client = KuCoinRESTClient()
    fills = await client.get_recent_fills(symbol="XBTUSDTM", since_ms=0)
    assert len(fills) == 1
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.l1
async def test_bybit_get_recent_fills_paginates_via_cursor(httpx_mock: HTTPXMock) -> None:
    """When nextPageCursor is non-empty, subsequent page is fetched."""
    httpx_mock.add_response(
        status_code=200,
        json={
            "retCode": 0,
            "result": {
                "list": [{"orderId": "b1", "symbol": "BTCUSDT", "side": "Buy",
                          "orderStatus": "Filled", "cumExecQty": "0.001",
                          "avgPrice": "50000", "cumExecFee": "0.001", "updatedTime": "1000"}],
                "nextPageCursor": "cursor-abc",
            },
        },
    )
    httpx_mock.add_response(
        status_code=200,
        json={
            "retCode": 0,
            "result": {
                "list": [{"orderId": "b2", "symbol": "BTCUSDT", "side": "Sell",
                          "orderStatus": "Filled", "cumExecQty": "0.001",
                          "avgPrice": "51000", "cumExecFee": "0.001", "updatedTime": "2000"}],
                "nextPageCursor": "",
            },
        },
    )
    client = BybitRESTClient()
    fills = await client.get_recent_fills(symbol="BTCUSDT", since_ms=0)

    assert len(fills) == 2
    assert fills[0].order_id == "b1"
    assert fills[1].order_id == "b2"
    assert len(httpx_mock.get_requests()) == 2
    assert "cursor=cursor-abc" in str(httpx_mock.get_requests()[1].url)


@pytest.mark.l1
async def test_bybit_get_recent_fills_no_cursor_stops_after_one_page(httpx_mock: HTTPXMock) -> None:
    """Empty nextPageCursor → single page, one HTTP request."""
    httpx_mock.add_response(
        status_code=200,
        json={
            "retCode": 0,
            "result": {
                "list": [{"orderId": "b1", "symbol": "BTCUSDT", "side": "Buy",
                          "orderStatus": "Filled", "cumExecQty": "0.001",
                          "avgPrice": "50000", "cumExecFee": "0", "updatedTime": "1000"}],
                "nextPageCursor": "",
            },
        },
    )
    client = BybitRESTClient()
    fills = await client.get_recent_fills(symbol="BTCUSDT", since_ms=0)
    assert len(fills) == 1
    assert len(httpx_mock.get_requests()) == 1
