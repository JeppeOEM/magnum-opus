// Package gapdetector classifies sequence number discontinuities into exactly
// four exhaustive causes. No IO — the Clock is injected; time.Now() is banned.
package gapdetector

import "time"

// Cause classifies why a sequence gap occurred. Exactly four values exist.
// Callers inspect the Internal() method to distinguish service bugs from
// exchange behaviour without string comparison.
type Cause string

const (
	// CauseInternalBufferOverflow means the aggregator's delta buffer filled
	// before a snapshot arrived. This is a defect in this service (internal_*).
	CauseInternalBufferOverflow Cause = "internal_buffer_overflow"

	// CauseInternalMergeError means a snapshot/delta merge failed due to a
	// logic error in the merge state machine. This is a defect (internal_*).
	CauseInternalMergeError Cause = "internal_merge_error"

	// CauseExternalDisconnect means the exchange or network dropped the
	// WebSocket connection. Not a service bug (external_*).
	//
	// Heuristic: the transport layer detects a connection close or read timeout
	// (no pong within deadline). The exchange did not send an error message;
	// the connection was simply lost. Signal: transport reconnect event.
	CauseExternalDisconnect Cause = "external_disconnect"

	// CauseExternalRateLimit means the exchange throttled or disconnected the
	// connection due to rate limiting. Not a service bug (external_*).
	//
	// Heuristic: distinguished from external_disconnect by a specific rate-limit
	// signal from the exchange — either an HTTP 429 response on the subscribe
	// or token endpoint, or an exchange-specific WebSocket error code/message
	// (e.g. KuCoin "Too Many Requests" system message, Bybit error code 10006).
	// If the connection drops without such a signal, use external_disconnect.
	CauseExternalRateLimit Cause = "external_rate_limit"
)

// Internal returns true for internal_* gap causes (bugs in this service).
// Returns false for external_* causes (exchange behaviour).
func (c Cause) Internal() bool {
	return c == CauseInternalBufferOverflow || c == CauseInternalMergeError
}

// Clock abstracts time.Now() for deterministic testing.
type Clock interface {
	Now() time.Time
}

// GapEvent is produced when a sequence gap is detected.
type GapEvent struct {
	Cause     Cause
	SeqBefore uint64
	SeqAfter  uint64
	Timestamp time.Time
}
