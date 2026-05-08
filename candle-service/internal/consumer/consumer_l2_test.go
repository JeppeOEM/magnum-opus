//go:build l2

package consumer

import (
	"context"
	"log/slog"
	"os"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/redis/go-redis/v9"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// stubBook records calls without doing any work.
type stubBook struct {
	ticks     []Tick
	gaps      []GapMarker
	snapshots []SnapshotEvent
}

func (s *stubBook) ApplyTick(t Tick)              { s.ticks = append(s.ticks, t) }
func (s *stubBook) ApplyGap(g GapMarker)           { s.gaps = append(s.gaps, g) }
func (s *stubBook) ApplySnapshot(e SnapshotEvent)  { s.snapshots = append(s.snapshots, e) }
func (s *stubBook) BestQuote() (string, string, string, string) { return "", "", "", "" }
func (s *stubBook) HasLevel(side, price string) bool             { return false }

// stubAcc records flush/reset/apply calls.
type stubAcc struct {
	flushCalls        []bool // isPartial values
	resetCalls        int
	applyCalls        int
	incrementGapCalls int
	obEventCalls      []obEventCall
}

type obEventCall struct {
	kind      OBEventKind
	side      string
	parsedSize float64
}

func (s *stubAcc) Apply(price, size string, isTrade bool, side string, tsMs int64,
	prevBid, prevBidSz, prevAsk, prevAskSz,
	currBid, currBidSz, currAsk, currAskSz string) {
	s.applyCalls++
}
func (s *stubAcc) ApplyOBEvent(kind OBEventKind, side string, parsedSize float64) {
	s.obEventCalls = append(s.obEventCalls, obEventCall{kind, side, parsedSize})
}
func (s *stubAcc) Flush(isPartial bool) error {
	s.flushCalls = append(s.flushCalls, isPartial)
	return nil
}
func (s *stubAcc) IncrementGap() { s.incrementGapCalls++ }
func (s *stubAcc) Reset()        { s.resetCalls++ }

// stubBookWithLevel is a stubBook that returns true for HasLevel on configured prices.
type stubBookWithLevel struct {
	stubBook
	levels map[string]bool // "side:price" → true
}

func (s *stubBookWithLevel) HasLevel(side, price string) bool {
	return s.levels[side+":"+price]
}

func newTestRDB(t *testing.T, mr *miniredis.Miniredis) *redis.Client {
	t.Helper()
	rdb := redis.NewClient(&redis.Options{
		Addr:     mr.Addr(),
		Protocol: 2, // RESP2 avoids go-redis v9 push notification complications with miniredis
	})
	t.Cleanup(func() { rdb.Close() })
	return rdb
}

func newTestConsumer(t *testing.T, rdb *redis.Client, book BookApplier, acc AccumulatorApplier, barClose <-chan BarCloseSig) *Consumer {
	t.Helper()
	c := New(rdb, "test-group", "test-consumer", "kucoin", "BTC-USDT",
		book, acc, barClose, slog.New(slog.NewTextHandler(os.Stderr, nil)))
	c.streamOverflowThresh = 5 // low threshold for testing
	return c
}

// addAndReadMessage adds a message to the stream, creates the consumer group
// AFTER the add (so the group starts at $, which is past the message unless
// we create it with "0"), then reads back via XREADGROUP. To make the message
// visible, we create the group first (at "0-0") then add the message.
func setupStreamWithMessage(t *testing.T, rdb *redis.Client, values map[string]any) (string, *Consumer, *stubBook, *stubAcc) {
	t.Helper()
	ctx := context.Background()
	book := &stubBook{}
	acc := &stubAcc{}
	c := newTestConsumer(t, rdb, book, acc, make(chan BarCloseSig, 1))

	// Create group at 0-0 (read all history) before adding the message.
	err := rdb.XGroupCreateMkStream(ctx, "ticks:kucoin:BTC-USDT", "test-group", "0-0").Err()
	require.NoError(t, err)

	id, err := rdb.XAdd(ctx, &redis.XAddArgs{
		Stream: "ticks:kucoin:BTC-USDT",
		Values: values,
	}).Result()
	require.NoError(t, err)
	return id, c, book, acc
}

func readOne(t *testing.T, rdb *redis.Client) []redis.XMessage {
	t.Helper()
	msgs, err := rdb.XReadGroup(context.Background(), &redis.XReadGroupArgs{
		Group:    "test-group",
		Consumer: "test-consumer",
		Streams:  []string{"ticks:kucoin:BTC-USDT", ">"},
		Count:    10,
	}).Result()
	require.NoError(t, err)
	require.NotEmpty(t, msgs)
	return msgs[0].Messages
}

func TestConsumer_EnsureGroup_FirstConnect(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := newTestRDB(t, mr)

	c := newTestConsumer(t, rdb, &stubBook{}, &stubAcc{}, make(chan BarCloseSig, 1))
	first, err := c.ensureGroup(context.Background())
	require.NoError(t, err)
	assert.True(t, first, "first connect should return true")
}

func TestConsumer_EnsureGroup_Idempotent(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := newTestRDB(t, mr)

	c := newTestConsumer(t, rdb, &stubBook{}, &stubAcc{}, make(chan BarCloseSig, 1))
	_, err := c.ensureGroup(context.Background())
	require.NoError(t, err)

	second, err := c.ensureGroup(context.Background())
	require.NoError(t, err)
	assert.False(t, second, "second call should return false (BUSYGROUP)")
}

func TestConsumer_MaxLenCheck_NoGapBelowThreshold(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := newTestRDB(t, mr)
	book := &stubBook{}
	c := newTestConsumer(t, rdb, book, &stubAcc{}, make(chan BarCloseSig, 1))
	// c.streamOverflowThresh is 5 from newTestConsumer; add 3 entries (below threshold).
	for i := 0; i < 3; i++ {
		mr.XAdd("ticks:kucoin:BTC-USDT", "*", []string{"event_type", "tick"})
	}

	err := c.checkMaxLen(context.Background())
	require.NoError(t, err)
	assert.Empty(t, book.gaps, "no gap should be emitted below threshold")
}

func TestConsumer_MaxLenCheck_GapAboveThreshold(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := newTestRDB(t, mr)
	book := &stubBook{}
	c := newTestConsumer(t, rdb, book, &stubAcc{}, make(chan BarCloseSig, 1))
	// threshold is 5; add 6 entries.
	for i := 0; i < 6; i++ {
		mr.XAdd("ticks:kucoin:BTC-USDT", "*", []string{"event_type", "tick"})
	}

	err := c.checkMaxLen(context.Background())
	require.NoError(t, err)
	require.Len(t, book.gaps, 1)
	assert.Equal(t, "stream_overflow", book.gaps[0].GapCause)
}

func TestConsumer_XAckBeforeDispatch(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := newTestRDB(t, mr)
	ctx := context.Background()

	msgID, c, book, _ := setupStreamWithMessage(t, rdb, map[string]any{
		"event_type": "tick", "seq": "1", "ts": "100",
		"price": "50000", "size": "1.0", "side": "buy", "level": "0",
	})

	msgs := readOne(t, rdb)
	require.Len(t, msgs, 1)

	err := c.handleMessage(ctx, msgs[0])
	require.NoError(t, err)

	pending, err := rdb.XPending(ctx, "ticks:kucoin:BTC-USDT", "test-group").Result()
	require.NoError(t, err)
	assert.Equal(t, int64(0), pending.Count, "message should be ACK'd")
	assert.Equal(t, msgID, msgs[0].ID)
	require.Len(t, book.ticks, 1)
	assert.Equal(t, "50000", book.ticks[0].Price)
}

func TestConsumer_ZeroVolumeTrade_Discarded(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := newTestRDB(t, mr)
	ctx := context.Background()

	_, c, book, _ := setupStreamWithMessage(t, rdb, map[string]any{
		"event_type": "tick", "seq": "1", "ts": "100",
		"price": "50000", "size": "0", "side": "buy", "level": "0",
	})

	msgs := readOne(t, rdb)
	require.Len(t, msgs, 1)
	require.NoError(t, c.handleMessage(ctx, msgs[0]))
	assert.Empty(t, book.ticks, "zero-volume trade must not reach BookApplier")
}

func TestConsumer_SnapshotFlushesAccumulator(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := newTestRDB(t, mr)
	ctx := context.Background()

	book := &stubBook{}
	acc := &stubAcc{}
	c := newTestConsumer(t, rdb, book, acc, make(chan BarCloseSig, 1))
	require.NoError(t, rdb.XGroupCreateMkStream(ctx, "ticks:kucoin:BTC-USDT", "test-group", "0-0").Err())

	// First add a tick so tickCount > 0, then a snapshot.
	rdb.XAdd(ctx, &redis.XAddArgs{Stream: "ticks:kucoin:BTC-USDT", Values: map[string]any{
		"event_type": "tick", "seq": "1", "ts": "100",
		"price": "50000", "size": "1.0", "side": "buy", "level": "0",
	}})
	rdb.XAdd(ctx, &redis.XAddArgs{Stream: "ticks:kucoin:BTC-USDT", Values: map[string]any{
		"event_type": "snapshot", "seq": "10", "ts": "200",
	}})

	msgs, err := rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group: "test-group", Consumer: "test-consumer",
		Streams: []string{"ticks:kucoin:BTC-USDT", ">"}, Count: 10,
	}).Result()
	require.NoError(t, err)
	require.Len(t, msgs[0].Messages, 2)

	for _, msg := range msgs[0].Messages {
		require.NoError(t, c.handleMessage(ctx, msg))
	}

	require.Len(t, acc.flushCalls, 1)
	assert.True(t, acc.flushCalls[0], "snapshot after tick must trigger isPartial=true flush")
	assert.Equal(t, 1, acc.resetCalls, "accumulator must be reset after snapshot")
	require.Len(t, book.snapshots, 1)
}

func TestConsumer_SnapshotZeroTick_NoFlush(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := newTestRDB(t, mr)
	ctx := context.Background()

	_, c, _, acc := setupStreamWithMessage(t, rdb, map[string]any{
		"event_type": "snapshot", "seq": "10", "ts": "200",
	})

	msgs := readOne(t, rdb)
	require.Len(t, msgs, 1)
	require.NoError(t, c.handleMessage(ctx, msgs[0]))

	assert.Empty(t, acc.flushCalls, "zero-tick snapshot must not trigger partial flush")
	assert.Equal(t, 1, acc.resetCalls, "accumulator must still be reset")
}

func TestConsumer_GapDedup_DuplicateDiscarded(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := newTestRDB(t, mr)
	ctx := context.Background()

	gapFields := map[string]any{
		"event_type": "gap", "seq_before": "1", "seq_after": "2",
		"gap_cause": "external_disconnect", "gap_ts": "300",
		"exchange": "kucoin", "symbol": "BTC-USDT",
	}

	book := &stubBook{}
	acc := &stubAcc{}
	c := newTestConsumer(t, rdb, book, acc, make(chan BarCloseSig, 1))
	require.NoError(t, rdb.XGroupCreateMkStream(ctx, "ticks:kucoin:BTC-USDT", "test-group", "0-0").Err())

	for i := 0; i < 2; i++ {
		rdb.XAdd(ctx, &redis.XAddArgs{Stream: "ticks:kucoin:BTC-USDT", Values: gapFields})
	}

	msgs, err := rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group: "test-group", Consumer: "test-consumer",
		Streams: []string{"ticks:kucoin:BTC-USDT", ">"}, Count: 10,
	}).Result()
	require.NoError(t, err)

	for _, msg := range msgs[0].Messages {
		require.NoError(t, c.handleMessage(ctx, msg))
	}
	assert.Len(t, book.gaps, 1, "duplicate gap must be deduplicated")
	assert.Equal(t, 1, acc.incrementGapCalls, "IncrementGap called once — dedup path skips second gap")
}

func TestConsumer_UnknownEventType_Skipped(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := newTestRDB(t, mr)
	ctx := context.Background()

	_, c, book, _ := setupStreamWithMessage(t, rdb, map[string]any{
		"event_type": "heartbeat", "ts": "500",
	})

	msgs := readOne(t, rdb)
	require.Len(t, msgs, 1)
	require.NoError(t, c.handleMessage(ctx, msgs[0]))

	assert.Empty(t, book.ticks)
	assert.Empty(t, book.gaps)

	pending, _ := rdb.XPending(ctx, "ticks:kucoin:BTC-USDT", "test-group").Result()
	assert.Equal(t, int64(0), pending.Count)
}

func TestConsumer_GapEvent_IncrementsAccGapCount(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := newTestRDB(t, mr)
	ctx := context.Background()

	gapFields := map[string]any{
		"event_type": "gap", "seq_before": "10", "seq_after": "20",
		"gap_cause": "external_disconnect", "gap_ts": "500",
		"exchange": "kucoin", "symbol": "BTC-USDT",
	}

	book := &stubBook{}
	acc := &stubAcc{}
	c := newTestConsumer(t, rdb, book, acc, make(chan BarCloseSig, 1))
	require.NoError(t, rdb.XGroupCreateMkStream(ctx, "ticks:kucoin:BTC-USDT", "test-group", "0-0").Err())

	rdb.XAdd(ctx, &redis.XAddArgs{Stream: "ticks:kucoin:BTC-USDT", Values: gapFields})

	msgs, err := rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group: "test-group", Consumer: "test-consumer",
		Streams: []string{"ticks:kucoin:BTC-USDT", ">"}, Count: 1,
	}).Result()
	require.NoError(t, err)
	require.NoError(t, c.handleMessage(ctx, msgs[0].Messages[0]))

	assert.Equal(t, 1, acc.incrementGapCalls, "IncrementGap must be called on EventGap")
	assert.Len(t, book.gaps, 1)
}

func TestConsumer_ContextCancel_StopsLoop(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := newTestRDB(t, mr)

	c := newTestConsumer(t, rdb, &stubBook{}, &stubAcc{}, make(chan BarCloseSig, 1))

	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	err := c.Run(ctx)
	assert.ErrorIs(t, err, context.DeadlineExceeded)
}

func TestConsumer_OBEventKind_Add(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := newTestRDB(t, mr)
	ctx := context.Background()

	book := &stubBookWithLevel{levels: map[string]bool{}} // no levels known
	acc := &stubAcc{}
	_, c, _, _ := setupStreamWithMessage(t, rdb, map[string]any{
		"event_type": "tick", "seq": "1", "ts": "100",
		"price": "50000", "size": "1.5", "side": "buy", "level": "1", // OB delta
	})
	// Replace the consumer's book and acc with ones that record OBEventKind
	c2 := newTestConsumer(t, rdb, book, acc, make(chan BarCloseSig, 1))

	msgs, err := rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group: "test-group", Consumer: "test-consumer",
		Streams: []string{"ticks:kucoin:BTC-USDT", ">"}, Count: 1,
	}).Result()
	require.NoError(t, err)
	require.NoError(t, c2.handleMessage(ctx, msgs[0].Messages[0]))

	require.Len(t, acc.obEventCalls, 1)
	assert.Equal(t, OBEventAdd, acc.obEventCalls[0].kind, "new level must be OBEventAdd")
	assert.Equal(t, "buy", acc.obEventCalls[0].side)
	assert.InDelta(t, 1.5, acc.obEventCalls[0].parsedSize, 1e-9)
	_ = c
}

func TestConsumer_OBEventKind_Modify(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := newTestRDB(t, mr)
	ctx := context.Background()

	// Pre-seed: HasLevel returns true for buy:50000
	book := &stubBookWithLevel{levels: map[string]bool{"buy:50000": true}}
	acc := &stubAcc{}
	c := newTestConsumer(t, rdb, book, acc, make(chan BarCloseSig, 1))
	require.NoError(t, rdb.XGroupCreateMkStream(ctx, "ticks:kucoin:BTC-USDT", "test-group", "0-0").Err())

	rdb.XAdd(ctx, &redis.XAddArgs{Stream: "ticks:kucoin:BTC-USDT", Values: map[string]any{
		"event_type": "tick", "seq": "1", "ts": "100",
		"price": "50000", "size": "2.0", "side": "buy", "level": "1", // existing level
	}})

	msgs, err := rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group: "test-group", Consumer: "test-consumer",
		Streams: []string{"ticks:kucoin:BTC-USDT", ">"}, Count: 1,
	}).Result()
	require.NoError(t, err)
	require.NoError(t, c.handleMessage(ctx, msgs[0].Messages[0]))

	require.Len(t, acc.obEventCalls, 1)
	assert.Equal(t, OBEventModify, acc.obEventCalls[0].kind, "existing level update must be OBEventModify")
}

func TestConsumer_OBEventKind_Cancel(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := newTestRDB(t, mr)
	ctx := context.Background()

	book := &stubBookWithLevel{levels: map[string]bool{"sell:50100": true}}
	acc := &stubAcc{}
	c := newTestConsumer(t, rdb, book, acc, make(chan BarCloseSig, 1))
	require.NoError(t, rdb.XGroupCreateMkStream(ctx, "ticks:kucoin:BTC-USDT", "test-group", "0-0").Err())

	rdb.XAdd(ctx, &redis.XAddArgs{Stream: "ticks:kucoin:BTC-USDT", Values: map[string]any{
		"event_type": "tick", "seq": "1", "ts": "100",
		"price": "50100", "size": "0", "side": "sell", "level": "1", // zero-size = cancel
	}})

	msgs, err := rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group: "test-group", Consumer: "test-consumer",
		Streams: []string{"ticks:kucoin:BTC-USDT", ">"}, Count: 1,
	}).Result()
	require.NoError(t, err)
	require.NoError(t, c.handleMessage(ctx, msgs[0].Messages[0]))

	require.Len(t, acc.obEventCalls, 1)
	assert.Equal(t, OBEventCancel, acc.obEventCalls[0].kind, "zero-size level must be OBEventCancel")
	assert.Equal(t, "sell", acc.obEventCalls[0].side)
}

func TestConsumer_TradeTick_NoApplyOBEvent(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := newTestRDB(t, mr)
	ctx := context.Background()

	book := &stubBookWithLevel{levels: map[string]bool{}}
	acc := &stubAcc{}
	_, c, _, _ := setupStreamWithMessage(t, rdb, map[string]any{
		"event_type": "tick", "seq": "1", "ts": "100",
		"price": "50000", "size": "1.0", "side": "buy", "level": "0", // trade tick
	})
	c2 := newTestConsumer(t, rdb, book, acc, make(chan BarCloseSig, 1))

	msgs, err := rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group: "test-group", Consumer: "test-consumer",
		Streams: []string{"ticks:kucoin:BTC-USDT", ">"}, Count: 1,
	}).Result()
	require.NoError(t, err)
	require.NoError(t, c2.handleMessage(ctx, msgs[0].Messages[0]))

	assert.Empty(t, acc.obEventCalls, "trade tick must NOT call ApplyOBEvent")
	_ = c
}
