# My bot ran for 48 hours and didn't do a thing

*The war story behind this pattern. — Cameron Meese*

I'd been watching a paper-trading bot I've been building for two days. Just paper — no real money at stake — but the silence was getting loud. Zero trades. Not "no opportunities" zero — *actively rejected every single one* zero. The bot logged 1,262 entry attempts in 24 hours. Every one bounced.

This is the post-mortem.

## The hook: why was nothing happening?

The bot's job is to spot setups across a handful of trading pairs and open positions when conditions line up. It had been working. Then I tightened the universe of symbols it watched — added some thinner, more volatile candidates I wanted to test against — and from that moment, nothing.

First instinct: market regime. Maybe nothing was qualifying. So I dumped the rejection log and bucketed by reason.

```
$ awk '/rejected/' state/decisions.jsonl | jq -r .reason | sort | uniq -c
   1015 stale_quote
    176 insufficient_inventory
     71 max_concurrent_reached
```

Stale quotes? On 14 actively-watched symbols, across three exchanges, in the middle of a normal trading day? That number didn't pass the sniff test.

## The investigation: chasing a lying number

Two things were happening, and they were stacking.

First: the bot tracks "freshness" of price quotes per symbol — if the last quote from an exchange is older than ~60 seconds, you don't trust it for sizing. Reasonable rule.

But to *get* fresh quotes, the bot polls the exchange's orderbook (via the wonderful but occasionally-temperamental [ccxt](https://github.com/ccxt/ccxt) library). And those polls were timing out — silently, in batches. Five-minute window: 215 orderbook timeouts. Same five-minute window: zero successful quote refreshes.

OK, so the bot has bad quotes. Why doesn't it just… wait and retry?

It does. Sort of. Here's the part that had been working fine for weeks and quietly became the bomb:

```python
# After 3 consecutive orderbook timeouts on (venue, symbol),
# stop scheduling that pair until the bot restarts.
if failure_count[(venue, symbol)] >= 3:
    quarantine.add((venue, symbol))
```

A reasonable defensive measure. If a `(venue, symbol)` is wedged, stop wasting cycles trying it. Restart-only recovery means a human is paying attention before it retries.

The bug isn't in the code. The bug is in the *assumption* the code encodes: "the only way this fails 3 times in a row is if something is permanently broken." That's true 99% of the time. The 1% is when an exchange has a 30-second session warmup on a cold start, and three consecutive 15-second timeouts trip every symbol you're trying to load.

9 of my 14 symbols got quarantined inside the first 65 seconds of boot. They stayed quarantined for the next 10 hours, until I noticed and restarted the bot.

## The second bug, which lied to me about the first

While I was in there, I noticed something else weird. A lot of the rejections were tagged `stale_quote`, but they shouldn't have been — for some of those candidates, the bot didn't even have inventory available. The "do you have inventory?" check should have rejected first.

It *was* checking. In the wrong order. The freshness check ran before the inventory check, and a stale quote (which, we now know, was caused by the quarantine) was masking the real reason. So the rejection log was *lying* to me — over a thousand `stale_quote` entries were really `insufficient_inventory` events I couldn't see.

This is the part of debugging nobody writes about: you find one bug, and it was hiding two more. Reorder the gate stack, surface the truth, suddenly the histogram tells a different story.

## The fix: auto-recovering quarantine

The real fix was conceptual. Permanent-until-restart is the wrong shape. What I wanted:

- Sideline a flaky `(venue, symbol)` after N consecutive failures (keep this part)
- After a cooldown, **carefully retry** (the new part)
- If still broken, re-quarantine *immediately* — not after another N failures

That last invariant turned out to be the one that mattered. If you reset the failure counter on cooldown expiry, a permanently-broken resource costs `N × cycles` failures over the lifetime of your process. If you *preserve* the count, it costs exactly one failure per cycle.

Here's the whole thing, about 50 lines:

```python
class AutoRecoveringQuarantine(Generic[K]):
    def __init__(self, *, threshold: int, recovery_seconds: float,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._threshold = threshold
        self._recovery_seconds = recovery_seconds
        self._clock = clock
        self._failure_count: dict[K, int] = {}
        self._skip_until: dict[K, float] = {}

    def is_quarantined(self, key: K) -> bool:
        until = self._skip_until.get(key)
        if until is None:
            return False
        if self._clock() < until:
            return True
        # Window expired — drop the deadline, KEEP the failure count.
        self._skip_until.pop(key, None)
        return False

    def record_success(self, key: K) -> None:
        self._failure_count.pop(key, None)
        self._skip_until.pop(key, None)

    def record_failure(self, key: K) -> int:
        count = self._failure_count.get(key, 0) + 1
        self._failure_count[key] = count
        if count >= self._threshold:
            self._skip_until[key] = self._clock() + self._recovery_seconds
        return count
```

The `K` is whatever hashable key identifies your "thing that flakes" — `(venue, symbol)`, a tenant ID, a customer hash, whatever. That's the pattern this repo packages: stdlib only, drop it into any project where one flaky key shouldn't take out the rest.

## The validation: the numbers don't lie this time

Before the fix, on the worst day of the cluster:

- 130 orderbook timeouts (one exchange)
- 1,286 `stale_quote` rejections (mostly lies, as we now know)
- 0 successful trades

After deploying the fix, over the next five days:

- 0 orderbook timeouts
- `stale_quote` rejections collapsed: 464 → 56 → 0 → 0 → 0
- First profitable trade closed: +$0.041 net of fees, held 15h 51min. (Paper money. Don't @ me about the size.)

Five days, four successful trades, 100% win rate on paper. The pattern is doing what it should, and the bot is no longer pretending to work while quietly doing nothing.

## What I'd tell past me

If you're writing defensive code that says "after N failures, give up," ask one more question: *what is the expected lifetime of "broken"?* If it's "forever," your defense is correct. If it's "until something transient clears" — and most things are — you need a way back.

Permanent isn't always the right kind of safe.
