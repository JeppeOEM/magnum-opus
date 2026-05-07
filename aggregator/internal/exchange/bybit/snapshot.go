package bybit

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/mrqdt/magnum-opus/aggregator/internal/coordinator"
	"github.com/mrqdt/magnum-opus/aggregator/internal/symbol"
)

// Compile-time proof that SnapshotFetcher satisfies coordinator.SnapshotFetcher.
var _ coordinator.SnapshotFetcher = (*SnapshotFetcher)(nil)

const defaultBybitAPIBase = "https://api.bybit.com"

// SnapshotFetcher fetches L2 order book snapshots from the Bybit REST API.
// Standalone struct (no dependency on bybit.Adapter) — the Bybit REST endpoint is public.
type SnapshotFetcher struct {
	baseURL    string
	httpClient *http.Client
}

// NewSnapshotFetcher creates a Bybit snapshot fetcher. httpClient defaults to http.DefaultClient.
func NewSnapshotFetcher(httpClient *http.Client) *SnapshotFetcher {
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return &SnapshotFetcher{baseURL: defaultBybitAPIBase, httpClient: httpClient}
}

// FetchSnapshot fetches the top-200 level-2 order book from the Bybit REST API.
// category=linear matches the WebSocket feed endpoint (wss://stream.bybit.com/v5/public/linear).
func (f *SnapshotFetcher) FetchSnapshot(ctx context.Context, _ string, sym symbol.Symbol) (coordinator.SnapshotResult, error) {
	// Canonical "BTC-USDT" → Bybit wire format "BTCUSDT"
	rawSym := strings.ReplaceAll(string(sym), "-", "")
	rawURL := fmt.Sprintf("%s/v5/market/orderbook?category=linear&symbol=%s&limit=200", f.baseURL, rawSym)

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return coordinator.SnapshotResult{}, fmt.Errorf("bybit: build orderbook request: %w", err)
	}

	resp, err := f.httpClient.Do(req)
	if err != nil {
		return coordinator.SnapshotResult{}, fmt.Errorf("bybit: orderbook request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusTooManyRequests {
		return coordinator.SnapshotResult{}, fmt.Errorf("bybit: orderbook rate limited (429)")
	}
	if resp.StatusCode != http.StatusOK {
		return coordinator.SnapshotResult{}, fmt.Errorf("bybit: orderbook status %d", resp.StatusCode)
	}

	raw, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return coordinator.SnapshotResult{}, fmt.Errorf("bybit: read orderbook body: %w", err)
	}

	var apiResp bybitOrderBookResp
	if err := json.Unmarshal(raw, &apiResp); err != nil {
		return coordinator.SnapshotResult{}, fmt.Errorf("bybit: parse orderbook: %w", err)
	}
	if apiResp.RetCode != 0 {
		return coordinator.SnapshotResult{}, fmt.Errorf("bybit: orderbook retCode %d: %s", apiResp.RetCode, apiResp.RetMsg)
	}

	bids := make(map[string]string, len(apiResp.Result.B))
	for _, level := range apiResp.Result.B {
		if len(level) == 2 {
			bids[level[0]] = level[1]
		}
	}
	asks := make(map[string]string, len(apiResp.Result.A))
	for _, level := range apiResp.Result.A {
		if len(level) == 2 {
			asks[level[0]] = level[1]
		}
	}

	return coordinator.SnapshotResult{Seq: apiResp.Result.Seq, Bids: bids, Asks: asks}, nil
}

type bybitOrderBookResp struct {
	RetCode int    `json:"retCode"`
	RetMsg  string `json:"retMsg"`
	Result  struct {
		S   string     `json:"s"`
		B   [][]string `json:"b"`
		A   [][]string `json:"a"`
		Seq uint64     `json:"seq"`
	} `json:"result"`
}
