# ccxt-auto-recovering-quarantine

A tiny Python pattern for sidelining a flaky resource after N consecutive failures, then auto-retrying after a cooldown — without permanent state, without requiring a restart, and without giving up on a resource that just needs a moment.

Born from a `ccxt` socket-hang where a single crypto exchange's session warmup tripped per-symbol timeouts on first boot and silently wedged 9 of 14 trading symbols for 10 hours, because the original "quarantine until restart" design had no way to recover.

## The problem

Naive retry-forever burns CPU and rate limits while the resource is dead. Naive permanent-fail strands you — the resource will probably recover, but your process won't notice until you restart.

You want a middle path: trip after N consecutive failures, sit out a cooldown window, then carefully retry. If the resource is still broken, re-trip immediately (not "wait N more failures").

## Usage

```python
from auto_recovering_quarantine import AutoRecoveringQuarantine

# Key can be anything hashable: tuple, str, dataclass, etc.
q = AutoRecoveringQuarantine[tuple[str, str]](
    threshold=4,
    recovery_seconds=300,
)

async def fetch_order_book(venue: str, symbol: str):
    key = (venue, symbol)
    if q.is_quarantined(key):
        return None  # skip; will retry after window expires
    try:
        result = await ccxt_call(venue, symbol)
        q.record_success(key)
        return result
    except (TimeoutError, ExchangeError):
        q.record_failure(key)
        return None
```

## Why "auto-recovering"

The clever bit: **the failure count persists across the recovery window.** So a still-broken resource re-quarantines on the very next retry (1 failure, not N more). A recovered resource clears the count on first success.

Alternative designs (resetting count on window expiry) let a permanently-broken resource cause `N × cycle_count` failures over time. With persistence, a dead resource costs exactly 1 failure per cycle.

## When to use

- Network calls to an external service that occasionally hangs
- Per-symbol orderbook fetches when one symbol is wedged
- Per-tenant operations where one tenant's outage shouldn't take out the rest
- Any `(provider, resource)` pair where failure is bounded to specific keys

## When NOT to use

- Your failures are global (whole service down) — use a circuit breaker instead
- Failures are stateless retries on a single resource — `tenacity` or similar fits better
- You need persistent quarantine across process restarts — wrap this with disk-backed state

## Installation

Until this lands on PyPI: copy `auto_recovering_quarantine.py` into your project. ~50 lines, stdlib-only.

## The story

A longer war-story post walks through how this pattern emerged from a real `ccxt` incident — timeline, diagnostic process, the gate-order bug it exposed along the way. Forthcoming as the first post in a Build-in-Public series; link will land here when published.

## License

MIT. See `LICENSE`.
