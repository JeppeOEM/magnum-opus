// Package consumer reads ticks from Redis Streams via XREADGROUP, parses
// messages, deduplicates gap markers, and dispatches events to downstream
// interfaces. XACK is sent before forwarding to downstream — a downstream
// panic will not trigger redelivery.
package consumer

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/redis/go-redis/v9"
)

// BookApplier is implemented by internal/orderbook (via OBAdapter).
type BookApplier interface {
	ApplyTick(t Tick)
	ApplySnapshot(s SnapshotEvent)
	ApplyGap(g GapMarker)
	// BestQuote returns the current best bid and ask (price, size) pair.
	// Returns ("","","","") when either side is empty.
	BestQuote() (bidPrice, bidSize, askPrice, askSize string)
	// HasLevel returns true if side ("buy"/"sell") has a level at the given price.
	// Must be called BEFORE ApplyTick to get pre-update state.
	HasLevel(side, price string) bool
}

// OBEventKind classifies a non-trade order book update.
type OBEventKind int8

const (
	OBEventNone   OBEventKind = 0 // trade tick — never passed to ApplyOBEvent
	OBEventAdd    OBEventKind = 1 // new price level (size != "0", level not in book)
	OBEventCancel OBEventKind = 2 // removed level (size == "0")
	OBEventModify OBEventKind = 3 // existing level, size changed
)

// AccumulatorApplier is implemented by the accWriter wrapper in cmd/candle.
type AccumulatorApplier interface {
	// Apply records a tick event with pre/post OB best quotes for OFI computation.
	// side is "buy" or "sell" for trade ticks; "" for OB delta ticks.
	// tsMs is the tick's Unix millisecond timestamp from the stream message.
	Apply(price, size string, isTrade bool, side string, tsMs int64,
		prevBid, prevBidSz, prevAsk, prevAskSz,
		currBid, currBidSz, currAsk, currAskSz string)
	// ApplyOBEvent records a classified OB delta event for activity tracking.
	// Only called for non-trade ticks; kind is never OBEventNone.
	ApplyOBEvent(kind OBEventKind, side string, parsedSize float64)
	Flush(isPartial bool) error
	IncrementGap()
	Reset()
}

// BarCloseSig is sent by the 1-second ticker goroutine.
type BarCloseSig struct{}

// PartialPublishSig is sent by the 250ms partial-publish ticker goroutine.
type PartialPublishSig struct{}

// PartialPublisher is implemented by the accWriter wrapper in cmd/candle.
// Called from the consumer goroutine — safe to call cascade.Engine.CurrentBar().
type PartialPublisher interface {
	PublishCascadePartial(ctx context.Context)
}

const DefaultStreamOverflowThreshold = 45_000

// Consumer handles one (exchange, symbol) pair.
type Consumer struct {
	rdb                  *redis.Client
	consumerGroup        string
	consumerName         string // unique per process instance
	exchange             string
	symbol               string
	streamKey            string
	book                 BookApplier
	acc                  AccumulatorApplier
	barClose             <-chan BarCloseSig
	partialPublish       <-chan PartialPublishSig // nil = disabled
	partial              PartialPublisher          // nil = disabled
	hasNewTicks          bool                      // set on EventTick; cleared after each partial publish
	logger               *slog.Logger
	streamOverflowThresh int64
	// per-second dedup set: cleared on each BarClose
	seenIDs   map[string]struct{}
	tickCount int // ticks in current second; guards zero-tick partial flush on snapshot
	// blue-green shadow mode
	shadowMode   atomic.Bool
	shadowMu     sync.Mutex
	lastShadowID string // last processed shadow message ID; "" before first XREAD
	xreadCursor  string // internal XREAD start cursor; "0-0" until first message
}

// New constructs a Consumer for one (exchange, symbol).
func New(
	rdb *redis.Client,
	consumerGroup string,
	consumerName string,
	exchange string,
	sym string,
	book BookApplier,
	acc AccumulatorApplier,
	barClose <-chan BarCloseSig,
	logger *slog.Logger,
) *Consumer {
	return &Consumer{
		rdb:                  rdb,
		consumerGroup:        consumerGroup,
		consumerName:         consumerName,
		exchange:             exchange,
		symbol:               sym,
		streamKey:            "ticks:" + exchange + ":" + sym,
		book:                 book,
		acc:                  acc,
		barClose:             barClose,
		logger:               logger.With("exchange", exchange, "symbol", sym),
		streamOverflowThresh: DefaultStreamOverflowThreshold,
		seenIDs:              make(map[string]struct{}),
	}
}

// WithPartialPublish enables the 250ms partial-bar publish for this consumer.
// ch receives PartialPublishSig from the partial-publish ticker goroutine (cap 1, non-blocking send).
// publisher is called from the consumer goroutine when new ticks have arrived since the last publish.
// Returns c for chaining with New().
func (c *Consumer) WithPartialPublish(ch <-chan PartialPublishSig, publisher PartialPublisher) *Consumer {
	c.partialPublish = ch
	c.partial = publisher
	return c
}

// WithShadowMode enables shadow XREAD mode for blue-green warmup.
// In shadow mode the consumer uses XREAD (no consumer group), does not XACK,
// does not call Flush, and does not respond to barClose/partialPublish signals.
// Returns c for chaining with New().
func (c *Consumer) WithShadowMode() *Consumer {
	c.shadowMode.Store(true)
	c.shadowMu.Lock()
	c.lastShadowID = ""     // no messages processed yet
	c.xreadCursor = "0-0"  // start reading from beginning of stream
	c.shadowMu.Unlock()
	return c
}

// LastShadowID returns the ID of the most recently processed XREAD message.
// Returns "" before any message has been processed; safe to call from any goroutine.
func (c *Consumer) LastShadowID() string {
	c.shadowMu.Lock()
	defer c.shadowMu.Unlock()
	return c.lastShadowID
}

// ShadowLag returns the number of entries in the stream after lastShadowID.
// Returns 0 when caught up, before first XREAD, or on error.
func (c *Consumer) ShadowLag(ctx context.Context) int64 {
	c.shadowMu.Lock()
	id := c.lastShadowID
	c.shadowMu.Unlock()
	if id == "" {
		// No messages processed yet — lag is the full stream length.
		xlen, err := c.rdb.XLen(ctx, c.streamKey).Result()
		if err != nil {
			return 0
		}
		return xlen
	}
	entries, err := c.rdb.XRange(ctx, c.streamKey, id, "+").Result()
	if err != nil {
		c.logger.WarnContext(ctx, "consumer: shadow lag query failed", "error", err)
		return 0
	}
	return computeShadowLag(int64(len(entries)))
}

// computeShadowLag converts an XRANGE count (inclusive of lastShadowID) to lag.
// Returns 0 when at tip (only lastShadowID in results).
func computeShadowLag(xrangeCount int64) int64 {
	lag := xrangeCount - 1
	if lag < 0 {
		lag = 0
	}
	return lag
}

// Promote transitions the consumer from shadow XREAD to active XREADGROUP.
// Ensures the consumer group exists, sets the group cursor to lastShadowID,
// and clears the shadow mode flag so Run() enters the XREADGROUP loop.
// Promote transitions the consumer from shadow XREAD to active XREADGROUP.
// lastShadowID is the last processed shadow message ID (from LastShadowID()).
// Empty string means no messages were processed; group cursor is set to "$" (tip).
// Creates the group at cursorID directly (single step — no two-step SetID window).
func (c *Consumer) Promote(lastShadowID string) error {
	ctx := context.Background()
	cursorID := lastShadowID
	if cursorID == "" {
		cursorID = "$" // no messages processed — start from stream tip
	}
	err := c.rdb.XGroupCreateMkStream(ctx, c.streamKey, c.consumerGroup, cursorID).Err()
	if err != nil && !isGroupExistsErr(err) {
		return fmt.Errorf("consumer: promote: ensure group: %w", err)
	}
	if isGroupExistsErr(err) {
		// Group already exists — update its cursor.
		if err2 := c.rdb.XGroupSetID(ctx, c.streamKey, c.consumerGroup, cursorID).Err(); err2 != nil {
			return fmt.Errorf("consumer: promote: set group id: %w", err2)
		}
	}
	c.shadowMu.Lock()
	c.lastShadowID = lastShadowID
	c.shadowMu.Unlock()
	c.shadowMode.Store(false) // signals runShadow to exit
	c.logger.Info("promoted: switched from shadow XREAD to XREADGROUP",
		"last_shadow_id", lastShadowID)
	return nil
}

// XAutoClaimPending claims all pending messages from the old slot (min-idle-time 0)
// and processes them through the normal dispatch path.
// Call after Promote() to recover any un-ACKed messages from the old slot.
// XAutoClaimPending claims all pending messages from the old slot (min-idle-time 0)
// and processes them through the normal dispatch path.
// Loops until the next-start cursor is "0-0" to handle > 1000 pending messages.
// Must be called from the consumer goroutine after Promote().
func (c *Consumer) XAutoClaimPending(ctx context.Context) error {
	start := "0-0"
	total := 0
	for {
		msgs, next, err := c.rdb.XAutoClaim(ctx, &redis.XAutoClaimArgs{
			Stream:   c.streamKey,
			Group:    c.consumerGroup,
			Consumer: c.consumerName,
			MinIdle:  0,
			Start:    start,
			Count:    1000,
		}).Result()
		if err != nil {
			return fmt.Errorf("consumer: xautoclaim: %w", err)
		}
		for _, msg := range msgs {
			if err := c.handleMessage(ctx, msg); err != nil {
				c.logger.ErrorContext(ctx, "consumer: xautoclaim dispatch failed", "id", msg.ID, "error", err)
			}
		}
		total += len(msgs)
		if next == "0-0" {
			break
		}
		start = next
	}
	c.logger.InfoContext(ctx, "XAUTOCLAIM recovered messages from old slot", "count", total)
	return nil
}

// Run reads from the Redis stream until ctx is cancelled.
// If started in shadow mode (WithShadowMode), runs XREAD warmup first,
// then transitions to XREADGROUP after Promote() is called.
func (c *Consumer) Run(ctx context.Context) error {
	if c.shadowMode.Load() {
		if err := c.runShadow(ctx); err != nil {
			return err
		}
		// Shadow loop exited after promotion. Reset accumulator and dedup state
		// from the consumer goroutine (the only goroutine that owns this state),
		// then claim any pending messages left by the old slot.
		c.acc.Reset()
		c.seenIDs = make(map[string]struct{})
		c.tickCount = 0
		if err := c.XAutoClaimPending(ctx); err != nil {
			c.logger.ErrorContext(ctx, "xautoclaim on promotion failed", "error", err)
			// Non-fatal — continue into XREADGROUP loop.
		}
	}

	firstConnect, err := c.ensureGroup(ctx)
	if err != nil {
		return fmt.Errorf("consumer: ensure group: %w", err)
	}

	if firstConnect {
		if err := c.checkMaxLen(ctx); err != nil {
			return fmt.Errorf("consumer: maxlen check: %w", err)
		}
	}

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-c.barClose:
			c.handleBarClose(ctx)
			c.seenIDs = make(map[string]struct{})
		case <-c.partialPublish:
			if c.hasNewTicks && c.partial != nil {
				c.partial.PublishCascadePartial(ctx)
				c.hasNewTicks = false
			}
		default:
		}

		msgs, err := c.rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
			Group:    c.consumerGroup,
			Consumer: c.consumerName,
			Streams:  []string{c.streamKey, ">"},
			Count:    100,
			Block:    250 * time.Millisecond,
		}).Result()

		if err != nil {
			if errors.Is(err, redis.Nil) || errors.Is(err, context.DeadlineExceeded) {
				continue
			}
			if ctx.Err() != nil {
				return ctx.Err()
			}
			return fmt.Errorf("consumer: xreadgroup: %w", err)
		}

		for _, stream := range msgs {
			for _, msg := range stream.Messages {
				if err := c.handleMessage(ctx, msg); err != nil {
					return err
				}
			}
		}
	}
}

func (c *Consumer) handleBarClose(ctx context.Context) {
	if err := c.acc.Flush(false); err != nil {
		c.logger.ErrorContext(ctx, "accumulator flush failed", "error", err)
	}
	c.tickCount = 0
}

func (c *Consumer) handleMessage(ctx context.Context, msg redis.XMessage) error {
	// Dedup within current 1-second window.
	if _, seen := c.seenIDs[msg.ID]; seen {
		if err := c.rdb.XAck(ctx, c.streamKey, c.consumerGroup, msg.ID).Err(); err != nil {
			c.logger.WarnContext(ctx, "xack failed for dup", "id", msg.ID, "error", err)
		}
		return nil
	}

	parsed := parseMessage(msg.Values)

	// Discard zero-volume trades before XACK.
	if parsed.Type == EventTick && parsed.Tick.Level == 0 && parsed.Tick.Size == "0" {
		if err := c.rdb.XAck(ctx, c.streamKey, c.consumerGroup, msg.ID).Err(); err != nil {
			c.logger.WarnContext(ctx, "xack failed for zero-vol trade", "id", msg.ID, "error", err)
		}
		c.logger.DebugContext(ctx, "discarded zero-volume trade", "id", msg.ID)
		c.seenIDs[msg.ID] = struct{}{}
		return nil
	}

	// XACK before dispatch — see package doc.
	if err := c.rdb.XAck(ctx, c.streamKey, c.consumerGroup, msg.ID).Err(); err != nil {
		// Context cancelled during shutdown: unacknowledged messages are
		// reclaimed by XAUTOCLAIM on next start — not a real error.
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return fmt.Errorf("consumer: xack %s: %w", msg.ID, err)
	}
	c.seenIDs[msg.ID] = struct{}{}

	switch parsed.Type {
	case EventTick:
		prevBid, prevBidSz, prevAsk, prevAskSz := c.book.BestQuote()
		isTrade := parsed.Tick.Level == 0
		var obKind OBEventKind
		if !isTrade {
			if parsed.Tick.Size == "0" {
				obKind = OBEventCancel
			} else if c.book.HasLevel(parsed.Tick.Side, parsed.Tick.Price) {
				obKind = OBEventModify
			} else {
				obKind = OBEventAdd
			}
		}
		c.book.ApplyTick(*parsed.Tick)
		currBid, currBidSz, currAsk, currAskSz := c.book.BestQuote()
		c.acc.Apply(parsed.Tick.Price, parsed.Tick.Size, isTrade, parsed.Tick.Side, parsed.Tick.TsMs,
			prevBid, prevBidSz, prevAsk, prevAskSz,
			currBid, currBidSz, currAsk, currAskSz)
		if !isTrade {
			parsedSize, _ := strconv.ParseFloat(parsed.Tick.Size, 64)
			c.acc.ApplyOBEvent(obKind, parsed.Tick.Side, parsedSize)
		}
		c.tickCount++
		c.hasNewTicks = true

	case EventGap:
		if dup, err := c.isGapDup(ctx, *parsed.Gap); err != nil {
			return fmt.Errorf("consumer: gap dedup check: %w", err)
		} else if dup {
			return nil
		}
		if err := c.recordGapDedup(ctx, *parsed.Gap); err != nil {
			return fmt.Errorf("consumer: gap dedup record: %w", err)
		}
		c.acc.IncrementGap()
		c.book.ApplyGap(*parsed.Gap)

	case EventSnapshot:
		// Only flush as partial bar if there are ticks in the current window.
		// Avoids writing a zero-tick partial bar on snapshot at second boundary.
		if c.tickCount > 0 {
			if err := c.acc.Flush(true); err != nil {
				c.logger.WarnContext(ctx, "partial flush on snapshot failed", "error", err)
			}
		}
		c.tickCount = 0
		c.acc.Reset()
		c.book.ApplySnapshot(*parsed.Snapshot)

	case EventUnknown:
		c.logger.WarnContext(ctx, "unknown message type — skipping", "id", msg.ID,
			"type", msg.Values["type"], "event_type", msg.Values["event_type"])
	}

	return nil
}

// Lag returns the current pending message count for this consumer's group.
// Returns 0 on error — lag is advisory and must not block the consumer loop.
func (c *Consumer) Lag(ctx context.Context) int64 {
	info, err := c.rdb.XPending(ctx, c.streamKey, c.consumerGroup).Result()
	if err != nil {
		c.logger.WarnContext(ctx, "consumer: lag query failed", "error", err)
		return 0
	}
	return info.Count
}

// ensureGroup creates the consumer group at $ if it does not exist.
// Returns true if the group was freshly created (first connect).
func (c *Consumer) ensureGroup(ctx context.Context) (bool, error) {
	err := c.rdb.XGroupCreateMkStream(ctx, c.streamKey, c.consumerGroup, "$").Err()
	if err == nil {
		return true, nil
	}
	if isGroupExistsErr(err) {
		return false, nil
	}
	return false, err
}

func isGroupExistsErr(err error) bool {
	return err != nil && strings.Contains(err.Error(), "BUSYGROUP")
}

// checkMaxLen emits a stream_overflow gap marker if the stream is near MAXLEN.
func (c *Consumer) checkMaxLen(ctx context.Context) error {
	length, err := c.rdb.XLen(ctx, c.streamKey).Result()
	if err != nil {
		return err
	}
	if length > c.streamOverflowThresh {
		gap := GapMarker{
			GapCause: "stream_overflow",
			Exchange: c.exchange,
			Symbol:   c.symbol,
			GapTsMs:  time.Now().UnixMilli(),
		}
		c.book.ApplyGap(gap)
		c.logger.WarnContext(ctx, "stream overflow gap emitted",
			"stream_len", length, "threshold", c.streamOverflowThresh)
	}
	return nil
}

// gapDedupKey builds the ZSET member key from the five dedup fields.
func gapDedupKey(g GapMarker) string {
	return strconv.FormatInt(g.SeqBefore, 10) + ":" +
		strconv.FormatInt(g.SeqAfter, 10) + ":" + g.GapCause
}

func (c *Consumer) gapZSetKey() string {
	return "candle:gap_dedup:" + c.exchange + ":" + c.symbol
}

func (c *Consumer) isGapDup(ctx context.Context, g GapMarker) (bool, error) {
	_, err := c.rdb.ZScore(ctx, c.gapZSetKey(), gapDedupKey(g)).Result()
	if errors.Is(err, redis.Nil) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	// ZScore returned without error → member exists → duplicate
	return true, nil
}

func (c *Consumer) recordGapDedup(ctx context.Context, g GapMarker) error {
	key := c.gapZSetKey()
	member := gapDedupKey(g)
	score := float64(time.Now().UnixMilli())

	if err := c.rdb.ZAdd(ctx, key, redis.Z{Score: score, Member: member}).Err(); err != nil {
		return err
	}
	// Trim to 10,000 most recent entries.
	return c.rdb.ZRemRangeByRank(ctx, key, 0, -10001).Err()
}

// runShadow runs the shadow XREAD loop until ctx is cancelled or Promote() is called.
func (c *Consumer) runShadow(ctx context.Context) error {
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		if !c.shadowMode.Load() {
			return nil // promoted — hand off to XREADGROUP loop
		}

		c.shadowMu.Lock()
		startID := c.xreadCursor
		c.shadowMu.Unlock()

		msgs, err := c.rdb.XRead(ctx, &redis.XReadArgs{
			Streams: []string{c.streamKey, startID},
			Count:   100,
			Block:   1000 * time.Millisecond,
		}).Result()

		if err != nil {
			if errors.Is(err, redis.Nil) || errors.Is(err, context.DeadlineExceeded) {
				continue
			}
			if ctx.Err() != nil {
				return ctx.Err()
			}
			return fmt.Errorf("consumer: xread (shadow): %w", err)
		}

		for _, stream := range msgs {
			for _, msg := range stream.Messages {
				c.handleShadowMessage(msg)
			}
		}
	}
}

// handleShadowMessage processes a message in shadow mode: updates book and accumulator
// state for warmup, but never XACKs, never calls Flush.
func (c *Consumer) handleShadowMessage(msg redis.XMessage) {
	c.shadowMu.Lock()
	c.lastShadowID = msg.ID // last processed; exposed via LastShadowID()
	c.xreadCursor = msg.ID  // internal XREAD cursor; used by runShadow
	c.shadowMu.Unlock()

	parsed := parseMessage(msg.Values)

	if parsed.Type == EventTick && parsed.Tick.Level == 0 && parsed.Tick.Size == "0" {
		return // discard zero-volume trades
	}

	switch parsed.Type {
	case EventTick:
		prevBid, prevBidSz, prevAsk, prevAskSz := c.book.BestQuote()
		isTrade := parsed.Tick.Level == 0
		var obKind OBEventKind
		if !isTrade {
			if parsed.Tick.Size == "0" {
				obKind = OBEventCancel
			} else if c.book.HasLevel(parsed.Tick.Side, parsed.Tick.Price) {
				obKind = OBEventModify
			} else {
				obKind = OBEventAdd
			}
		}
		c.book.ApplyTick(*parsed.Tick)
		currBid, currBidSz, currAsk, currAskSz := c.book.BestQuote()
		c.acc.Apply(parsed.Tick.Price, parsed.Tick.Size, isTrade, parsed.Tick.Side, parsed.Tick.TsMs,
			prevBid, prevBidSz, prevAsk, prevAskSz,
			currBid, currBidSz, currAsk, currAskSz)
		if !isTrade {
			parsedSize, _ := strconv.ParseFloat(parsed.Tick.Size, 64)
			c.acc.ApplyOBEvent(obKind, parsed.Tick.Side, parsedSize)
		}

	case EventGap:
		c.acc.IncrementGap()
		c.book.ApplyGap(*parsed.Gap)

	case EventSnapshot:
		c.acc.Reset()
		c.book.ApplySnapshot(*parsed.Snapshot)

	case EventUnknown:
		c.logger.Warn("shadow: unknown message type — skipping", "id", msg.ID,
			"type", msg.Values["type"], "event_type", msg.Values["event_type"])
	}
}
