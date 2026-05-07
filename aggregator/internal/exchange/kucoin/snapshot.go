package kucoin

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"

	"github.com/mrqdt/magnum-opus/aggregator/internal/coordinator"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)

// Compile-time proof that OrderBookFetcher satisfies coordinator.SnapshotFetcher.
var _ coordinator.SnapshotFetcher = (*OrderBookFetcher)(nil)

// OrderBookFetcher fetches L2 order book snapshots from the KuCoin REST API.
// Lives in package kucoin to access adapter.apiBase and adapter.httpClient (unexported fields).
type OrderBookFetcher struct {
	adapter *Adapter
}

// NewOrderBookFetcher creates a fetcher backed by the adapter's HTTP client and API base URL.
// The adapter.apiBase is overridden in tests to point at a mock server.
func NewOrderBookFetcher(a *Adapter) *OrderBookFetcher {
	return &OrderBookFetcher{adapter: a}
}

const orderBookPath = "/api/v1/market/orderbook/level2_100"

// FetchSnapshot fetches the top-100 level-2 order book from the KuCoin REST API.
// This is a public endpoint — no authentication headers are required.
func (f *OrderBookFetcher) FetchSnapshot(ctx context.Context, _ string, sym symbol.Symbol) (coordinator.SnapshotResult, error) {
	rawURL := f.adapter.apiBase + orderBookPath + "?symbol=" + url.QueryEscape(string(sym))
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return coordinator.SnapshotResult{}, fmt.Errorf("kucoin: build orderbook request: %w", err)
	}

	resp, err := f.adapter.httpClient.Do(req)
	if err != nil {
		return coordinator.SnapshotResult{}, fmt.Errorf("kucoin: orderbook request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusTooManyRequests {
		return coordinator.SnapshotResult{}, fmt.Errorf("kucoin: orderbook rate limited (429)")
	}
	if resp.StatusCode != http.StatusOK {
		return coordinator.SnapshotResult{}, fmt.Errorf("kucoin: orderbook status %d", resp.StatusCode)
	}

	raw, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return coordinator.SnapshotResult{}, fmt.Errorf("kucoin: read orderbook body: %w", err)
	}

	var apiResp kucoinOrderBookResp
	if err := json.Unmarshal(raw, &apiResp); err != nil {
		return coordinator.SnapshotResult{}, fmt.Errorf("kucoin: parse orderbook: %w", err)
	}
	if apiResp.Code != "200000" {
		return coordinator.SnapshotResult{}, fmt.Errorf("kucoin: orderbook api code %s", apiResp.Code)
	}

	seq, err := strconv.ParseUint(apiResp.Data.Sequence, 10, 64)
	if err != nil {
		return coordinator.SnapshotResult{}, fmt.Errorf("kucoin: parse sequence %q: %w", apiResp.Data.Sequence, err)
	}

	bids := make(map[string]string, len(apiResp.Data.Bids))
	for _, level := range apiResp.Data.Bids {
		if len(level) == 2 {
			bids[level[0]] = level[1]
		}
	}
	asks := make(map[string]string, len(apiResp.Data.Asks))
	for _, level := range apiResp.Data.Asks {
		if len(level) == 2 {
			asks[level[0]] = level[1]
		}
	}

	return coordinator.SnapshotResult{Seq: seq, Bids: bids, Asks: asks}, nil
}

type kucoinOrderBookResp struct {
	Code string `json:"code"`
	Data struct {
		Sequence string     `json:"sequence"`
		Time     int64      `json:"time"`
		Bids     [][]string `json:"bids"`
		Asks     [][]string `json:"asks"`
	} `json:"data"`
}
