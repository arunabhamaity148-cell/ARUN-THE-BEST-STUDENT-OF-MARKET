"""
engine/institutional_edges.py — 5 Institutional Microstructure Alpha Edges (Phase 6B).

Formulas & Architecture
-----------------------
1. Edge 1: Spot-to-Perp Microsecond Lead-Lag (Lambda_lead)
   - 10-second rolling CVD window (spot vs perp)
   - Basis spread delta: basis_bps = ((perp - spot) / spot) * 10000
   - Lambda_lead = Z(spot_cvd) - 0.65 * Z(perp_cvd) - 0.80 * sign(spot_cvd) * Z(delta_basis_bps)
   - Trigger: abs(Lambda_lead) >= 2.65 and sign(spot_cvd) != sign(delta_basis)

2. Edge 2: Self-Exciting Hawkes Process (Lambda_t)
   - Forced liquidations (!forceOrder@arr), notional V_i = P * Q
   - A_t = A_t_prev * exp(-beta * dt) + alpha * (V_k / avg_V)
   - Parameters: mu_0 = 0.05, alpha = 0.85, beta = 1.20
   - lambda_t = mu_0 + A_t
   - Exhaustion Trigger: peak_lambda / mu_0 >= 25.0 and lambda_t <= 0.70 * peak_lambda

3. Edge 3: Iceberg / Ghost Liquidity Replenishment (G_t)
   - visible_drop = max(0, prev_qty - new_qty)
   - ghost_volume = max(0, aggressive_sell_volume - visible_drop)
   - G_t = sum(ghost_vol) / (sum(exec_vol) + 1e-9)
   - Trigger: 30s aggressive volume > 2.0 sigma and G_t >= 0.68

4. Edge 4: Cross-Margined Collateral Stress (COIN-M vs USD-M)
   - Psi_stress = Z(MP_CM) - Z(MP_UM)
   - Trigger Short: Z(MP_UM) >= 2.0 and Psi_stress <= -2.7
   - Trigger Long Bottom: COIN-M Hawkes ratio >= 20.0 and Psi_stress >= 1.6

5. Edge 5: Lo-MacKinlay Variance Ratio (VR) & GKYZ Volatility
   - 1-minute klines: r_t = ln(C_t / C_{t-1}), r_t(5) = ln(C_t / C_{t-5})
   - VR(5) = Var(r_t(5)) / (5.0 * Var(r_t))
   - GKYZ_var = (ln(O/C_prev))^2 + 0.5*(ln(H/L))^2 - (2ln2 - 1)*(ln(C/O))^2
   - Carry_Skew = Funding_Rate / (sqrt(sum(GKYZ_var)) + 1e-9)
   - Regime: "MEAN_REVERSION" if VR(5) <= 0.85 and abs(Carry_Skew) >= 1.5; "BREAKOUT" if VR(5) >= 1.18

Strict Constraints:
- Zero NumPy, zero Pandas.
- Python standard library math, array, collections.deque.
- __slots__ on all classes.
"""

import math
from collections import deque

from config import (
    HAWKES_PEAK_WINDOW,
    GHOST_VOL_WINDOW,
    HAWKES_MU_0,
    HAWKES_ALPHA,
    HAWKES_BETA,
)
from core.state import OnlineNormalizer, SymbolMicroState

# Pre-computed mathematical constants
GKYZ_CONST: float = 2.0 * math.log(2.0) - 1.0  # 0.3862943611198906


# ─────────────────────────────────────────────────────────────────────
# Pure Helper Functions (Stateless Mathematical Functions)
# ─────────────────────────────────────────────────────────────────────

def sign(x: float) -> float:
    """Return sign of x: +1.0 for positive, -1.0 for negative, 0.0 for zero."""
    if x > 0.0:
        return 1.0
    elif x < 0.0:
        return -1.0
    return 0.0


def compute_lead_lag_score(
    z_spot: float,
    z_perp: float,
    z_delta_basis: float,
    spot_cvd: float,
    delta_basis: float,
) -> tuple[float, bool]:
    """Compute Edge 1: Spot-to-Perp Lead-Lag Lambda_lead and trigger flag.

    Lambda_lead = Z(spot_cvd) - 0.65 * Z(perp_cvd) - 0.80 * sign(spot_cvd) * Z(delta_basis_bps)
    Trigger: abs(Lambda_lead) >= 2.65 and sign(spot_cvd) != sign(delta_basis)
    """
    s_spot = sign(spot_cvd)
    lambda_lead = z_spot - 0.65 * z_perp - 0.80 * s_spot * z_delta_basis
    triggered = (abs(lambda_lead) >= 2.65) and (s_spot != sign(delta_basis))
    return lambda_lead, triggered


def compute_hawkes_intensity(
    a_prev: float,
    dt: float,
    v_k: float,
    avg_v: float,
    alpha: float = HAWKES_ALPHA,
    beta: float = HAWKES_BETA,
    mu_0: float = HAWKES_MU_0,
) -> tuple[float, float]:
    """Compute Edge 2: Hawkes A_t and intensity lambda_t = mu_0 + A_t."""
    v_norm = (v_k / avg_v) if avg_v > 0.0 else 1.0
    a_t = a_prev * math.exp(-beta * max(0.0, dt)) + alpha * v_norm
    lambda_t = mu_0 + a_t
    return a_t, lambda_t


def check_hawkes_exhaustion(
    lambda_t: float,
    peak_lambda: float,
    mu_0: float = HAWKES_MU_0,
) -> bool:
    """Return True if liquidation cascade has exhausted from peak.

    Condition: (peak_lambda / mu_0 >= 25.0) and (lambda_t <= 0.70 * peak_lambda)
    """
    if mu_0 <= 0.0:
        return False
    return (peak_lambda / mu_0 >= 25.0) and (lambda_t <= 0.70 * peak_lambda)


def compute_ghost_volume(
    aggressive_volume: float,
    prev_qty: float,
    new_qty: float,
) -> float:
    """Compute ghost liquidity replenishment volume at a price level.

    visible_drop = max(0, prev_qty - new_qty)
    ghost_volume = max(0, aggressive_volume - visible_drop)
    """
    visible_drop = max(0.0, prev_qty - new_qty)
    return max(0.0, aggressive_volume - visible_drop)


def compute_iceberg_ratio(
    ghost_vol_sum: float,
    exec_vol_sum: float,
) -> float:
    """Compute Edge 3: Iceberg G_t = sum(ghost_vol) / (sum(exec_vol) + 1e-9)."""
    return ghost_vol_sum / (exec_vol_sum + 1e-9)


def compute_micro_price(
    bid: float,
    ask: float,
    bid_qty: float,
    ask_qty: float,
) -> float:
    """Compute volume-weighted micro-price: (bid*ask_qty + ask*bid_qty) / (bid_qty + ask_qty)."""
    denom = bid_qty + ask_qty
    if denom <= 0.0:
        return (bid + ask) / 2.0
    return (bid * ask_qty + ask * bid_qty) / denom


def compute_collateral_stress_score(
    z_mp_cm: float,
    z_mp_um: float,
) -> float:
    """Compute Edge 4: Cross-Margined Collateral Stress Psi_stress = Z(MP_CM) - Z(MP_UM)."""
    return z_mp_cm - z_mp_um


def sample_variance(values: list[float] | deque) -> float:
    """Calculate sample variance s^2 for an iterable of floats."""
    n = len(values)
    if n < 2:
        return 0.0
    m = sum(values) / n
    ss = sum((x - m) ** 2 for x in values)
    return ss / (n - 1)


def compute_variance_ratio(closes: list[float] | deque) -> float:
    """Compute Edge 5: Lo-MacKinlay Variance Ratio VR(5) = Var(r_t(5)) / (5.0 * Var(r_t))."""
    n = len(closes)
    if n < 7:  # Need at least 2 5-period returns
        return 1.0

    r1 = []
    for i in range(1, n):
        if closes[i - 1] > 0.0 and closes[i] > 0.0:
            r1.append(math.log(closes[i] / closes[i - 1]))

    r5 = []
    for i in range(5, n):
        if closes[i - 5] > 0.0 and closes[i] > 0.0:
            r5.append(math.log(closes[i] / closes[i - 5]))

    if len(r1) < 2 or len(r5) < 2:
        return 1.0

    var_1 = sample_variance(r1)
    var_5 = sample_variance(r5)

    if var_1 <= 1e-12:
        return 1.0

    return var_5 / (5.0 * var_1)


def compute_gkyz_variance(
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    prev_close: float,
) -> float:
    """Compute Garman-Klass-Yang-Zhang variance for a single bar."""
    if open_p <= 0.0 or high_p <= 0.0 or low_p <= 0.0 or close_p <= 0.0 or prev_close <= 0.0:
        return 0.0
    term1 = math.log(open_p / prev_close) ** 2
    term2 = 0.5 * (math.log(high_p / low_p) ** 2)
    term3 = GKYZ_CONST * (math.log(close_p / open_p) ** 2)
    return max(0.0, term1 + term2 - term3)


def classify_volatility_regime(vr_5: float, carry_skew: float) -> str:
    """Classify regime based on VR(5) and Carry_Skew:

    - "MEAN_REVERSION" if VR(5) <= 0.85 and abs(Carry_Skew) >= 1.5
    - "BREAKOUT" if VR(5) >= 1.18
    - "NEUTRAL" otherwise
    """
    if vr_5 <= 0.85 and abs(carry_skew) >= 1.5:
        return "MEAN_REVERSION"
    elif vr_5 >= 1.18:
        return "BREAKOUT"
    return "NEUTRAL"


# ─────────────────────────────────────────────────────────────────────
# InstitutionalEdgeCalculator Class
# ─────────────────────────────────────────────────────────────────────

class InstitutionalEdgeCalculator:
    """Stateful calculator for the 5 Institutional Microstructure Alpha Edges.

    Maintains online normalizers and rolling state per symbol.
    """

    __slots__ = (
        "symbol",
        # Edge 1: Lead-Lag
        "spot_cvd_normalizer",
        "perp_cvd_normalizer",
        "basis_delta_normalizer",
        "prev_basis_bps",
        # Edge 2: Hawkes
        "liq_notional_normalizer",
        # Edge 3: Iceberg
        "agg_vol_30s_normalizer",
        # Edge 4: Collateral Stress
        "mp_cm_normalizer",
        "mp_um_normalizer",
        "coin_m_hawkes_a",
        "coin_m_hawkes_last_ts",
        "coin_m_peak_ring",
        # Edge 5: Variance Ratio & GKYZ
        "kline_closes",
        "gkyz_ring",
        "prev_kline_close",
    )

    def __init__(self, symbol: str = "BTCUSDT") -> None:
        self.symbol: str = symbol

        # Edge 1 normalizers & state
        self.spot_cvd_normalizer: OnlineNormalizer = OnlineNormalizer()
        self.perp_cvd_normalizer: OnlineNormalizer = OnlineNormalizer()
        self.basis_delta_normalizer: OnlineNormalizer = OnlineNormalizer()
        self.prev_basis_bps: float = 0.0

        # Edge 2 notional normalizer
        self.liq_notional_normalizer: OnlineNormalizer = OnlineNormalizer()

        # Edge 3 aggressive volume normalizer
        self.agg_vol_30s_normalizer: OnlineNormalizer = OnlineNormalizer()

        # Edge 4 normalizers & COIN-M hawkes tracking
        self.mp_cm_normalizer: OnlineNormalizer = OnlineNormalizer()
        self.mp_um_normalizer: OnlineNormalizer = OnlineNormalizer()
        self.coin_m_hawkes_a: float = 0.0
        self.coin_m_hawkes_last_ts: int = 0
        self.coin_m_peak_ring: deque = deque(maxlen=HAWKES_PEAK_WINDOW)

        # Edge 5 kline tracking (last 60 bars)
        self.kline_closes: deque = deque(maxlen=60)
        self.gkyz_ring: deque = deque(maxlen=30)
        self.prev_kline_close: float = 0.0

    # ── Edge 1: Spot-to-Perp Lead-Lag ────────────────────────────────

    def update_lead_lag(
        self,
        state: SymbolMicroState,
        spot_price: float,
        perp_price: float,
        now_ms: int,
    ) -> tuple[float, bool]:
        """Update 10s CVD, basis delta, and calculate Lambda_lead."""
        spot_net, perp_net = state.prune_cvd_10s(now_ms)

        self.spot_cvd_normalizer.update(spot_net)
        self.perp_cvd_normalizer.update(perp_net)

        z_spot = self.spot_cvd_normalizer.z_score(spot_net)
        z_perp = self.perp_cvd_normalizer.z_score(perp_net)

        basis_bps = 0.0
        if spot_price > 0.0:
            basis_bps = ((perp_price - spot_price) / spot_price) * 10000.0

        delta_basis = basis_bps - self.prev_basis_bps
        self.prev_basis_bps = basis_bps

        self.basis_delta_normalizer.update(delta_basis)
        z_delta_basis = self.basis_delta_normalizer.z_score(delta_basis)

        return compute_lead_lag_score(
            z_spot=z_spot,
            z_perp=z_perp,
            z_delta_basis=z_delta_basis,
            spot_cvd=spot_net,
            delta_basis=delta_basis,
        )

    # ── Edge 2: Self-Exciting Hawkes Process ─────────────────────────

    def update_hawkes(
        self,
        state: SymbolMicroState,
        price: float,
        qty: float,
        ts_ms: int,
    ) -> tuple[float, float, bool]:
        """Incorporate liquidation order and update Hawkes intensity lambda_t."""
        v_k = price * qty
        self.liq_notional_normalizer.update(v_k)
        avg_v = self.liq_notional_normalizer.mean if self.liq_notional_normalizer.count > 0 else v_k

        dt = 0.0
        if state.hawkes_last_ts > 0:
            diff = ts_ms - state.hawkes_last_ts
            dt = diff / 1000.0 if diff > 10000 else float(diff)

        state.hawkes_last_ts = ts_ms

        a_t, lambda_t = compute_hawkes_intensity(
            a_prev=state.hawkes_a,
            dt=dt,
            v_k=v_k,
            avg_v=avg_v,
        )
        state.hawkes_a = a_t
        state.hawkes_peak_ring.append(lambda_t)

        peak_lambda = max(state.hawkes_peak_ring) if state.hawkes_peak_ring else lambda_t
        is_exhausted = check_hawkes_exhaustion(lambda_t, peak_lambda)

        return lambda_t, peak_lambda, is_exhausted

    # ── Edge 3: Iceberg / Ghost Liquidity Replenishment ───────────────

    def update_iceberg(
        self,
        state: SymbolMicroState,
        aggressive_volume: float,
        prev_qty: float,
        new_qty: float,
    ) -> tuple[float, bool]:
        """Record ghost volume from orderbook update and check trigger."""
        ghost_vol = compute_ghost_volume(aggressive_volume, prev_qty, new_qty)
        state.record_ghost_liquidity(ghost_vol, aggressive_volume)

        ghost_sum = sum(state.ghost_vol_ring)
        exec_sum = sum(state.exec_vol_ring)
        g_t = compute_iceberg_ratio(ghost_sum, exec_sum)

        self.agg_vol_30s_normalizer.update(exec_sum)
        z_vol = self.agg_vol_30s_normalizer.z_score(exec_sum)

        is_iceberg_trigger = (z_vol > 2.0) and (g_t >= 0.68)
        return g_t, is_iceberg_trigger

    # ── Edge 4: Cross-Margined Collateral Stress ─────────────────────

    def update_coin_m_liquidation(
        self,
        price: float,
        qty: float,
        ts_ms: int,
    ) -> float:
        """Track COIN-M liquidations for collateral stress exhaustion."""
        v_k = price * qty
        dt = 0.0
        if self.coin_m_hawkes_last_ts > 0:
            diff = ts_ms - self.coin_m_hawkes_last_ts
            dt = diff / 1000.0 if diff > 10000 else float(diff)
        self.coin_m_hawkes_last_ts = ts_ms

        a_t, lambda_t = compute_hawkes_intensity(
            a_prev=self.coin_m_hawkes_a,
            dt=dt,
            v_k=v_k,
            avg_v=10000.0,
        )
        self.coin_m_hawkes_a = a_t
        self.coin_m_peak_ring.append(lambda_t)
        return lambda_t

    def compute_collateral_stress(
        self,
        bid_cm: float,
        ask_cm: float,
        bid_qty_cm: float,
        ask_qty_cm: float,
        bid_um: float,
        ask_um: float,
        bid_qty_um: float,
        ask_qty_um: float,
    ) -> tuple[float, bool, bool]:
        """Compute Psi_stress = Z(MP_CM) - Z(MP_UM) and check directional triggers."""
        mp_cm = compute_micro_price(bid_cm, ask_cm, bid_qty_cm, ask_qty_cm)
        mp_um = compute_micro_price(bid_um, ask_um, bid_qty_um, ask_qty_um)

        self.mp_cm_normalizer.update(mp_cm)
        self.mp_um_normalizer.update(mp_um)

        z_mp_cm = self.mp_cm_normalizer.z_score(mp_cm)
        z_mp_um = self.mp_um_normalizer.z_score(mp_um)

        psi_stress = compute_collateral_stress_score(z_mp_cm, z_mp_um)

        # Trigger Short: Z(MP_UM) >= 2.0 and Psi_stress <= -2.7
        trigger_short = (z_mp_um >= 2.0) and (psi_stress <= -2.7)

        # Trigger Long Bottom: COIN-M Hawkes ratio >= 20.0 and Psi_stress >= 1.6
        peak_cm = max(self.coin_m_peak_ring) if self.coin_m_peak_ring else 0.0
        coin_m_ratio = peak_cm / HAWKES_MU_0 if HAWKES_MU_0 > 0 else 0.0
        trigger_long_bottom = (coin_m_ratio >= 20.0) and (psi_stress >= 1.6)

        return psi_stress, trigger_short, trigger_long_bottom

    # ── Edge 5: Lo-MacKinlay Variance Ratio & GKYZ Volatility ─────────

    def update_kline(
        self,
        open_p: float,
        high_p: float,
        low_p: float,
        close_p: float,
        funding_rate: float,
    ) -> tuple[float, float, float, str]:
        """Incorporate a 1-minute closed kline bar and classify volatility regime."""
        self.kline_closes.append(close_p)

        if self.prev_kline_close > 0.0:
            gkyz_var = compute_gkyz_variance(
                open_p=open_p,
                high_p=high_p,
                low_p=low_p,
                close_p=close_p,
                prev_close=self.prev_kline_close,
            )
            self.gkyz_ring.append(gkyz_var)
        self.prev_kline_close = close_p

        vr_5 = compute_variance_ratio(self.kline_closes)
        gkyz_vol = math.sqrt(max(0.0, sum(self.gkyz_ring)))
        carry_skew = funding_rate / (gkyz_vol + 1e-9)
        regime = classify_volatility_regime(vr_5, carry_skew)

        return vr_5, gkyz_vol, carry_skew, regime

    def __repr__(self) -> str:
        return f"InstitutionalEdgeCalculator(symbol={self.symbol})"
