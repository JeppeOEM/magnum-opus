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
