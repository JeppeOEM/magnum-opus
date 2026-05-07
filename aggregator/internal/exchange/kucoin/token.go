package kucoin

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strconv"
	"time"

	"github.com/mrqdt/magnum-opus/aggregator/internal/backoff"
	"github.com/mrqdt/magnum-opus/aggregator/internal/config"
)

const (
	defaultAPIBase    = "https://api.kucoin.com"
	bulletPrivatePath = "/api/v1/bullet-private"
	bulletPublicPath  = "/api/v1/bullet-public"
	tokenTTL          = 24 * time.Hour
)

// tokenData holds a parsed KuCoin WebSocket token and its connection parameters.
type tokenData struct {
	endpoint     string        // WebSocket base endpoint from instanceServers
	token        string
	pingInterval time.Duration
	pingTimeout  time.Duration
	fetchedAt    time.Time
}

// wsURL builds the full WebSocket URL including token and a connect ID.
// If token is empty (test mode), returns endpoint as-is.
func (t tokenData) wsURL() string {
	if t.token == "" {
		return t.endpoint
	}
	return t.endpoint + "?token=" + t.token + "&connectId=" + nextMsgID()
}

// expiresAt returns when the token expires (24h after fetch).
func (t tokenData) expiresAt() time.Time {
	return t.fetchedAt.Add(tokenTTL)
}

type tokenAPIResponse struct {
	Code string `json:"code"`
	Data struct {
		Token           string `json:"token"`
		InstanceServers []struct {
			Endpoint     string `json:"endpoint"`
			PingInterval int    `json:"pingInterval"` // milliseconds
			PingTimeout  int    `json:"pingTimeout"`  // milliseconds
		} `json:"instanceServers"`
	} `json:"data"`
}

// fetchToken obtains a KuCoin WebSocket token.
// If cfg.Public is true (no credentials provided) it uses the unauthenticated
// bullet-public endpoint; otherwise it signs the request against bullet-private.
// apiBase allows overriding the base URL for tests.
func fetchToken(ctx context.Context, client *http.Client, apiBase string, cfg config.KuCoinConfig, clock Clock) (tokenData, error) {
	now := clock.Now()
	method := "POST"
	var path string
	if cfg.Public {
		path = bulletPublicPath
	} else {
		path = bulletPrivatePath
	}

	req, err := http.NewRequestWithContext(ctx, method, apiBase+path, nil)
	if err != nil {
		return tokenData{}, fmt.Errorf("build request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	if !cfg.Public {
		ts := strconv.FormatInt(now.UnixMilli(), 10)
		preSign := ts + method + path
		sig := kucoinHMAC(cfg.APISecret.Value(), preSign)
		// API v2: passphrase itself is HMAC-signed with the API secret.
		signedPassphrase := kucoinHMAC(cfg.APISecret.Value(), cfg.Passphrase.Value())
		req.Header.Set("KC-API-KEY", cfg.APIKey.Value())
		req.Header.Set("KC-API-SIGN", sig)
		req.Header.Set("KC-API-TIMESTAMP", ts)
		req.Header.Set("KC-API-PASSPHRASE", signedPassphrase)
		req.Header.Set("KC-API-KEY-VERSION", "2")
	}

	resp, err := client.Do(req)
	if err != nil {
		return tokenData{}, fmt.Errorf("request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusTooManyRequests {
		return tokenData{}, fmt.Errorf("rate limited (429)")
	}
	if resp.StatusCode != http.StatusOK {
		return tokenData{}, fmt.Errorf("status %d", resp.StatusCode)
	}

	raw, err := io.ReadAll(io.LimitReader(resp.Body, 8192))
	if err != nil {
		return tokenData{}, fmt.Errorf("read body: %w", err)
	}

	var apiResp tokenAPIResponse
	if err := json.Unmarshal(raw, &apiResp); err != nil {
		return tokenData{}, fmt.Errorf("parse response: %w", err)
	}
	if apiResp.Code != "200000" {
		return tokenData{}, fmt.Errorf("api error code %s", apiResp.Code)
	}
	if len(apiResp.Data.InstanceServers) == 0 {
		return tokenData{}, fmt.Errorf("no instance servers in response")
	}

	srv := apiResp.Data.InstanceServers[0]
	return tokenData{
		endpoint:     srv.Endpoint,
		token:        apiResp.Data.Token,
		pingInterval: time.Duration(srv.PingInterval) * time.Millisecond,
		pingTimeout:  time.Duration(srv.PingTimeout) * time.Millisecond,
		fetchedAt:    now,
	}, nil
}

// tokenRenewalLoop polls on renewalCheckInterval and fetches a fresh token
// once the current one is within renewalLeadTime of expiry. On retry exhaustion
// it fires the reconnect trigger so runLoop re-authenticates from scratch, then
// exits — runLoop owns reconnect and will obtain a fresh 24h token independently.
// The goroutine exits cleanly when ctx is cancelled.
func (a *Adapter) tokenRenewalLoop(ctx context.Context) {
	if a.renewalMaxAttempts <= 0 {
		return
	}

	ticker := time.NewTicker(a.renewalCheckInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}

		a.tokMu.RLock()
		renewalDue := a.clk.Now().After(a.tok.expiresAt().Add(-a.renewalLeadTime))
		a.tokMu.RUnlock()

		if !renewalDue {
			continue
		}

		var (
			newTok tokenData
			err    error
		)
		for attempt := 0; attempt < a.renewalMaxAttempts; attempt++ {
			if ctx.Err() != nil {
				return
			}
			newTok, err = fetchToken(ctx, a.httpClient, a.apiBase, a.cfg, a.clk)
			if err == nil {
				break
			}
			slog.Warn("kucoin: token renewal failed", "attempt", attempt, "err", err)
			if attempt+1 < a.renewalMaxAttempts {
				d := backoff.Duration(attempt, a.clk)
				pongTimer := time.NewTimer(d)
				select {
				case <-pongTimer.C:
				case <-ctx.Done():
					pongTimer.Stop()
					return
				}
				pongTimer.Stop()
			}
		}

		if err != nil {
			slog.Error("kucoin: token renewal exhausted retries, triggering reconnect")
			a.fireTrigger()
			return
		}

		a.tokMu.Lock()
		a.tok = newTok
		a.tokMu.Unlock()
		slog.Info("kucoin: token renewed", "expires_at", newTok.expiresAt())
	}
}

// kucoinHMAC computes base64(HMAC-SHA256(message, secret)).
func kucoinHMAC(secret, message string) string {
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(message))
	return base64.StdEncoding.EncodeToString(mac.Sum(nil))
}
