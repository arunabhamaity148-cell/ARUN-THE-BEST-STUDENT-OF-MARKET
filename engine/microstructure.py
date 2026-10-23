"""
engine/microstructure.py — Continuous-time microstructure alpha features.

Transforms raw depth, trade, and liquidation data from SymbolMicroState
into normalized directional alpha signals.  ZERO REST API calls — purely
in-memory computation.

Features
--------
1. Multi-Level Order Flow Imbalance (L5 OFI)
2. Volume-Weighted Micro-Price Displacement
3. CVD Absorption Divergence
4. Liquidation Cascade Exhaustion  (Z_liq)

Constraints
-----------
- No NumPy / Pandas / SciPy.
- ``array.array('d', …)`` for fixed-size C-double storage.
- ``__slots__`` on every class.
- Module-level immutable tuples for pre-computed constants.
- Message router inspects only the first 256 bytes.
"""

import math
from array import array

# ─────────────────────────────────────────────────────────────────────
# Pre-computed Constants  (module-level immutable tuples)
# ─────────────────────────────────────────────────────────────────────

_LAMBDA: float = 0.35

DEPTH_WEIGHTS: tuple[float, ...] = tuple(
    math.exp(-_LAMBDA * i) for i in range(5)
)
# → (1.0, ≈0.7047, ≈0.4966, ≈0.3499, ≈0.2466)

ABSORPTION_WINDOW_MS: int = 15 * 60 * 1000   # 15 minutes
LIQ_ROLLING_MS: int = 60 * 1000              # 60-second rolling notional
LIQ_BASELINE_MS: int = 15 * 60 * 1000        # 15-minute baseline for Z
MAX_ROUTE_SCAN: int = 256                     # bytes inspected by router

_MIN_SLOPE_SAMPLES: int = 10                  # minimum for regression


# ─────────────────────────────────────────────────────────────────────
# 0.  Fast Message Router  (first 256 bytes only)
# ─────────────────────────────────────────────────────────────────────

def extract_stream_type(raw: bytes | str,
                        max_scan: int = MAX_ROUTE_SCAN) -> str | None:
    """Return the ``stream`` field value by scanning only *max_scan* bytes.

    Returns ``None`` for WebSocket control responses (``"result"`` /
    ``"id"`` fields) so callers can skip them cheaply.

    Parameters
    ----------
    raw : bytes or str
        Raw WebSocket frame (not yet parsed by orjson).
    max_scan : int
        Upper byte limit for the prefix scan.
    """
    if isinstance(raw, str):
        prefix = raw[:max_scan]
        # control response check
        if '"result"' in prefix or '"id"' in prefix:
            return None
        idx = prefix.find('"stream":"')
        if idx == -1:
            return None
        start = idx + 10
        end = prefix.find('"', start)
        return None if end == -1 else prefix[start:end]

    # bytes path — preferred
    prefix = raw[:max_scan]
    if b'"result"' in prefix or b'"id"' in prefix:
        return None
    idx = prefix.find(b'"stream":"')
    if idx == -1:
        return None
    start = idx + 10
    end = prefix.find(b'"', start)
    if end == -1:
        return None
    return prefix[start:end].decode("ascii")


# ─────────────────────────────────────────────────────────────────────
# 1.  Multi-Level Order Flow Imbalance  (L5 OFI)
# ─────────────────────────────────────────────────────────────────────

def compute_level_ofi_bid(
    prev_price: float,
    prev_qty: float,
    curr_price: float,
    curr_qty: float,
) -> float:
    """Order-flow contribution for a single **bid** level.

    Rules
    -----
    - Price up   → new higher bid appeared  → ``+curr_qty``
    - Price same → quantity changed          → ``curr_qty − prev_qty``
    - Price down → bid level dropped         → ``−prev_qty``
    """
    if curr_price > prev_price:
        return curr_qty
    if curr_price == prev_price:
        return curr_qty - prev_qty
    return -prev_qty


def compute_level_ofi_ask(
    prev_price: float,
    prev_qty: float,
    curr_price: float,
    curr_qty: float,
) -> float:
    """Order-flow contribution for a single **ask** level.

    Rules (inverted vs. bid)
    -----
    - Price down → new lower ask appeared  → ``+curr_qty``
    - Price same → quantity changed         → ``curr_qty − prev_qty``
    - Price up   → ask level retreated      → ``−prev_qty``
    """
    if curr_price < prev_price:
        return curr_qty
    if curr_price == prev_price:
        return curr_qty - prev_qty
    return -prev_qty


def update_l5_ofi(state, bids: list, asks: list) -> float:
    """Compute depth-weighted L5 OFI, update *state*, return the value.

    Parameters
    ----------
    state : SymbolMicroState
        Must have ``prev_bid_prices / qtys`` and ``prev_ask_prices / qtys``
        arrays of length 5.
    bids : list
        Top-20 bid levels from ``@depth20@100ms`` — each ``[price_str, qty_str]``.
        Only the first 5 are used.
    asks : list
        Top-20 ask levels — same format.

    Returns
    -------
    float
        Signed OFI value (positive → buying pressure).
    """
    # First update ever?  Store snapshot, return 0.
    first_update = (state.prev_bid_prices[0] == 0.0
                    and state.prev_ask_prices[0] == 0.0)

    ofi = 0.0
    for i in range(5):
        curr_bp = float(bids[i][0])
        curr_bq = float(bids[i][1])
        curr_ap = float(asks[i][0])
        curr_aq = float(asks[i][1])

        if not first_update:
            e_b = compute_level_ofi_bid(
                state.prev_bid_prices[i], state.prev_bid_qtys[i],
                curr_bp, curr_bq,
            )
            e_a = compute_level_ofi_ask(
                state.prev_ask_prices[i], state.prev_ask_qtys[i],
                curr_ap, curr_aq,
            )
            ofi += DEPTH_WEIGHTS[i] * (e_b - e_a)

        # store current → previous
        state.prev_bid_prices[i] = curr_bp
        state.prev_bid_qtys[i] = curr_bq
        state.prev_ask_prices[i] = curr_ap
        state.prev_ask_qtys[i] = curr_aq

    # ── OFI sign-streak tracking (used by spoofing veto) ──
    ofi_sign = (1 if ofi > 0 else (-1 if ofi < 0 else 0))
    if ofi_sign != 0 and ofi_sign == state.last_ofi_sign:
        state.ofi_sign_streak += 1
    else:
        state.ofi_sign_streak = 1 if ofi_sign != 0 else 0
    state.last_ofi_sign = ofi_sign
    state.ofi_history.append(ofi)

    return ofi


# ─────────────────────────────────────────────────────────────────────
# 2.  Volume-Weighted Micro-Price Displacement
# ─────────────────────────────────────────────────────────────────────

def compute_micro_price(
    bid: float,
    bid_qty: float,
    ask: float,
    ask_qty: float,
) -> float:
    """Volume-weighted micro-price.

    ``P_micro = (P_bid × Q_ask  +  P_ask × Q_bid) / (Q_ask + Q_bid)``
    """
    total_qty = ask_qty + bid_qty
    if total_qty == 0.0:
        return (bid + ask) / 2.0
    return (bid * ask_qty + ask * bid_qty) / total_qty


def compute_micro_spread_bps(
    bid: float,
    bid_qty: float,
    ask: float,
    ask_qty: float,
) -> float:
    """Micro-price displacement from mid, in basis points.

    ``MicroSpreadBps = ((P_micro − P_mid) / P_mid) × 10⁴``

    Positive → micro-price above mid (buying pressure).
    Negative → micro-price below mid (selling pressure).
    """
    mid = (bid + ask) / 2.0
    if mid == 0.0:
        return 0.0
    micro = compute_micro_price(bid, bid_qty, ask, ask_qty)
    return ((micro - mid) / mid) * 1e4


# ─────────────────────────────────────────────────────────────────────
# 3.  CVD Absorption Divergence
# ─────────────────────────────────────────────────────────────────────

def _rolling_statistics(
    snapshots,
    now_ms: int,
    window_ms: int,
    value_index: int,
) -> tuple[float, float]:
    """Compute OLS slope and population std-dev of a value column.

    Parameters
    ----------
    snapshots : iterable of tuples
        ``(timestamp_ms, price, cvd)``.
    now_ms : int
        Current timestamp in milliseconds.
    window_ms : int
        Look-back window in milliseconds.
    value_index : int
        Column index inside each tuple (1 = price, 2 = cvd).

    Returns
    -------
    (slope, std_dev) : tuple[float, float]
    """
    cutoff = now_ms - window_ms

    n = 0
    sum_t = 0.0
    sum_v = 0.0
    sum_tv = 0.0
    sum_t2 = 0.0
    sum_v2 = 0.0
    t0 = 0

    for snap in snapshots:
        if snap[0] < cutoff:
            continue
        if n == 0:
            t0 = snap[0]
        t = (snap[0] - t0) / 1000.0        # seconds since first sample
        v = snap[value_index]
        n += 1
        sum_t += t
        sum_v += v
        sum_tv += t * v
        sum_t2 += t * t
        sum_v2 += v * v

    if n < 2:
        return 0.0, 0.0

    denom = n * sum_t2 - sum_t * sum_t
    slope = (n * sum_tv - sum_t * sum_v) / denom if abs(denom) > 1e-15 else 0.0

    mean_v = sum_v / n
    variance = sum_v2 / n - mean_v * mean_v
    std = math.sqrt(max(0.0, variance))

    return slope, std


def compute_absorption_score(state, now_ms: int) -> float:
    """15-minute rolling slope divergence between price and CVD.

    Returns a positive score when price drops but aggressive sellers
    are absorbed by limit bids (bullish absorption).

    ``score = cvd_slope_normalised − price_slope_normalised``
    """
    snaps = state.absorption_snapshots
    if len(snaps) < _MIN_SLOPE_SAMPLES:
        return 0.0

    price_slope, price_std = _rolling_statistics(
        snaps, now_ms, ABSORPTION_WINDOW_MS, 1,
    )
    cvd_slope, cvd_std = _rolling_statistics(
        snaps, now_ms, ABSORPTION_WINDOW_MS, 2,
    )

    if price_std < 1e-15 or cvd_std < 1e-15:
        return 0.0

    return (cvd_slope / cvd_std) - (price_slope / price_std)


# ─────────────────────────────────────────────────────────────────────
# 4.  Liquidation Cascade Exhaustion  (Z_liq)
# ─────────────────────────────────────────────────────────────────────

def compute_rolling_liq_notional(
    state,
    now_ms: int,
    window_ms: int = LIQ_ROLLING_MS,
) -> float:
    """Sum of forced-liquidation notional within *window_ms*."""
    cutoff = now_ms - window_ms
    total = 0.0
    for ts, notional in state.liq_events:
        if ts >= cutoff:
            total += notional
    return total


def update_z_liq(state, now_ms: int) -> float:
    """Compute Z_liq: z-score of 60 s rolling liquidation notional
    against the 15-minute streaming baseline.

    Side-effects: feeds the current 60 s notional into
    ``state.liq_normalizer`` so the baseline evolves.

    Returns
    -------
    float
        Z-score (≥ 3.0 is the cascade-exhaustion trigger).
    """
    v_liq = compute_rolling_liq_notional(state, now_ms, LIQ_ROLLING_MS)

    # feed into 15-minute Welford baseline
    state.liq_normalizer.update(v_liq)

    return state.liq_normalizer.z_score(v_liq)
