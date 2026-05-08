package symbol

// Symbol is a normalized (exchange, raw) pair.
// Zero internal imports — leaf node in the dependency graph.
type Symbol struct {
	Exchange string
	Raw      string
}

// New constructs a Symbol. Both fields are stored as-is; callers are
// responsible for normalizing exchange and raw symbol strings upstream.
func New(exchange, raw string) Symbol {
	return Symbol{Exchange: exchange, Raw: raw}
}

// String returns "exchange:raw", usable as a map key or log field.
func (s Symbol) String() string {
	return s.Exchange + ":" + s.Raw
}
