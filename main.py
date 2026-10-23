"""
main.py — Production Orchestrator for the Microstructure Signal Bot.

Ties together all 5 phases:
- Phase 1: Dual-WebSocket ingestion (depth20, bookTicker, aggTrade, forceOrder, kline).
- Phase 2: Continuous-time microstructure alpha engine (L5 OFI, Micro-Price, CVD Absorption, Z_liq).
- Phase 3: Lazy on-demand REST confirmation (OI, Funding, Basis), SMT divergence, and regime classification.
- Phase 4: Macroeconomic calendar, RSS news shock scanner, Correlation Heat Lock, and Liquidity Vacuum vetoes.
- Phase 5: Bayesian log-odds synthesis, volatility-adjusted Kelly risk calculator, and Telegram queue dispatcher.

Strict Constraints:
- Zero NumPy / Pandas / SciPy.
- Zero paid APIs / zero trade execution (read-only signals).
- Environment secrets loaded via python-dotenv.
"""

import asyncio
import logging
import os
import signal
import sys
import time
from typing import Any

from dotenv import load_dotenv

# Try uvloop on non-Windows platforms
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except (ImportError, AttributeError):
    pass

from config import SYMBOLS
from core.state import SymbolMicroState
from ingestion.ws_manager import WSManager
from engine.microstructure import (
    extract_stream_type,
    update_l5_ofi,
    compute_micro_price,
    compute_micro_spread_bps,
    compute_absorption_score,
    update_z_liq,
)
from engine.vetoes import (
    SpoofingQuoteFlickerVeto,
    FlashCrashKnifeCatchVeto,
    DeleveragingVeto,
    FundingSettlementBoundaryVeto,
    LiquidityVacuumVeto,
    compute_top5_depth_notional,
)
from engine.rest_engine import LazyRestConfirmedEngine
from engine.cross_asset import SMTDetector
from engine.derivatives import DerivativesRegimeClassifier
from engine.news_engine import MacroAndNewsVetoEngine
from engine.heat_lock import CorrelationHeatLock
from engine.synthesis import SignalSynthesizer
from engine.risk import RiskCalculator
from dispatch.telegram_bot import TelegramSignalDispatcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("signal_bot")


# ─────────────────────────────────────────────────────────────────────
# Signal Bot Orchestrator Class
# ─────────────────────────────────────────────────────────────────────

class SignalBotOrchestrator:
    """Coordinates streaming ingestion, microstructure edge computation, vetoes,
    and Telegram dispatch.
    """

    __slots__ = (
        "symbols",
        "states",
        "ws_manager",
        "rest_engine",
        "smt_detector",
        "regime_classifier",
        "news_engine",
        "heat_lock",
        "spoofing_veto",
        "flash_crash_veto",
        "deleveraging_veto",
        "funding_boundary_veto",
        "liquidity_veto",
        "synthesizer",
        "risk_calculator",
        "dispatcher",
        "active_signals",
        "_running",
    )

    def __init__(self, symbols: tuple[str, ...] = SYMBOLS) -> None:
        self.symbols: tuple[str, ...] = symbols

        # Phase 1: MicroState per symbol
        self.states: dict[str, SymbolMicroState] = {
            sym: SymbolMicroState(sym) for sym in symbols
        }

        # Phase 2 & 4 Vetoes
        self.spoofing_veto: SpoofingQuoteFlickerVeto = SpoofingQuoteFlickerVeto()
        self.flash_crash_veto: FlashCrashKnifeCatchVeto = FlashCrashKnifeCatchVeto()
        self.deleveraging_veto: DeleveragingVeto = DeleveragingVeto()
        self.funding_boundary_veto: FundingSettlementBoundaryVeto = (
            FundingSettlementBoundaryVeto()
        )
        self.liquidity_veto: LiquidityVacuumVeto = LiquidityVacuumVeto()

        # Phase 3: Lazy REST & Cross-Asset
        self.rest_engine: LazyRestConfirmedEngine = LazyRestConfirmedEngine()
        self.smt_detector: SMTDetector = SMTDetector()
        self.regime_classifier: DerivativesRegimeClassifier = (
            DerivativesRegimeClassifier()
        )

        # Phase 4: News & Correlation Heat Lock
        self.news_engine: MacroAndNewsVetoEngine = MacroAndNewsVetoEngine()
        self.heat_lock: CorrelationHeatLock = CorrelationHeatLock()

        # Phase 5: Synthesis, Risk, Dispatch
        self.synthesizer: SignalSynthesizer = SignalSynthesizer()
        self.risk_calculator: RiskCalculator = RiskCalculator()
        self.dispatcher: TelegramSignalDispatcher = TelegramSignalDispatcher()

        self.active_signals: set[str] = set()
        self._running: bool = False

        # Phase 1: Dual-WS Manager
        self.ws_manager: WSManager = WSManager(
            symbols=symbols,
            on_message=self.on_message,
        )

    # ── Message Dispatch Router ──────────────────────────────────────

    async def on_message(self, data: dict) -> None:
        """Handle raw WebSocket JSON payload from Binance Futures."""
        stream: str = data.get("stream", "")
        payload: dict = data.get("data", {})
        if not stream or not payload:
            return

        now_ms = int(time.time() * 1000)

        # 1. Depth Snapshot (depth20@100ms)
        if "@depth20" in stream:
            symbol = stream.split("@")[0].upper()
            state = self.states.get(symbol)
            if state is not None:
                bids = payload.get("bids", [])
                asks = payload.get("asks", [])
                if len(bids) >= 5 and len(asks) >= 5:
                    # Update L5 OFI
                    update_l5_ofi(state, bids, asks)
                    # Track top-5 depth for liquidity vacuum veto
                    d5 = compute_top5_depth_notional(bids, asks)
                    self.liquidity_veto.update_depth(d5)

        # 2. BookTicker (best bid & ask)
        elif "@bookTicker" in stream:
            symbol = stream.split("@")[0].upper()
            state = self.states.get(symbol)
            if state is not None:
                bid = float(payload.get("b", 0.0))
                bid_qty = float(payload.get("B", 0.0))
                ask = float(payload.get("a", 0.0))
                ask_qty = float(payload.get("A", 0.0))
                state.update_book_ticker(bid, bid_qty, ask, ask_qty)

        # 3. AggTrade (realized taker flow)
        elif "@aggTrade" in stream:
            symbol = stream.split("@")[0].upper()
            state = self.states.get(symbol)
            if state is not None:
                price = float(payload.get("p", 0.0))
                qty = float(payload.get("q", 0.0))
                is_buyer_maker = bool(payload.get("m", False))
                trade_time = int(payload.get("T", now_ms))
                state.update_trade(price, qty, is_buyer_maker, trade_time)

                # Check edge evaluation on trade flow
                await self._evaluate_symbol_edges(symbol, now_ms)

        # 4. Liquidation Orders (!forceOrder@arr)
        elif "forceOrder" in stream:
            # Array of liquidation events
            event = payload.get("o", {})
            sym = event.get("s", "").upper()
            state = self.states.get(sym)
            if state is not None:
                qty = float(event.get("q", 0.0))
                price = float(event.get("p", 0.0))
                notional = qty * price
                ts = int(payload.get("E", now_ms))
                state.add_liquidation(ts, notional)
                update_z_liq(state, ts)

        # 5. Kline 1-Minute
        elif "@kline" in stream:
            k = payload.get("k", {})
            sym = k.get("s", "").upper()
            if k.get("x", False):  # On bar close
                h = float(k.get("h", 0.0))
                l = float(k.get("l", 0.0))
                c = float(k.get("c", 0.0))
                self.smt_detector.update_bar(sym, high=h, low=l, close=c)
                self.heat_lock.add_price(sym, c)

    # ── Signal Synthesis & Execution Pipeline ────────────────────────

    async def _evaluate_symbol_edges(self, symbol: str, now_ms: int) -> None:
        """Evaluate microstructure edges, query REST if hot, test vetoes, and synthesize."""
        state = self.states.get(symbol)
        if state is None:
            return

        # 1. Edge Z-Scores
        # L5 OFI score
        latest_ofi = state.ofi_history[-1] if state.ofi_history else 0.0
        z_ofi = 2.5 if latest_ofi > 0 else (-2.5 if latest_ofi < 0 else 0.0)

        # CVD Absorption
        absorption = compute_absorption_score(state, now_ms)
        z_cvd = max(-4.0, min(4.0, absorption))

        # Liquidation Exhaustion
        z_liq = state.liq_normalizer.z_score(
            state.liq_velocity_peak if state.liq_velocity_peak > 0 else 0.0
        )

        # Micro-Price displacement
        micro_bps = compute_micro_spread_bps(
            state.best_bid, state.best_bid_qty,
            state.best_ask, state.best_ask_qty,
        )
        z_micro = max(-4.0, min(4.0, micro_bps / 2.0))

        # Trigger Threshold: only trigger REST confirmation if OFI or CVD shows edge
        if abs(z_ofi) < 2.0 and abs(z_cvd) < 1.8 and z_liq < 2.5:
            return

        # 2. Lazy On-Demand REST Confirmation
        delta_oi, funding_rate, basis_bps = (
            await self.rest_engine.get_derivatives_data(symbol)
        )
        z_oi = max(-4.0, min(4.0, delta_oi / 1.5))

        # 3. SMT Divergence
        # Evaluate vs BTC (if ETH or SOL)
        z_smt = 0.0
        direction = "LONG" if z_ofi > 0 else "SHORT"
        if symbol != "BTCUSDT" and "BTCUSDT" in self.states:
            btc_price = self.states["BTCUSDT"].mid_price
            curr_price = state.mid_price
            if direction == "LONG":
                is_smt, smt_score = self.smt_detector.check_bullish_smt(
                    "BTCUSDT", symbol, btc_price, curr_price
                )
            else:
                is_smt, smt_score = self.smt_detector.check_bearish_smt(
                    "BTCUSDT", symbol, btc_price, curr_price
                )
            z_smt = max(-4.0, min(4.0, smt_score * 2.0))

        # 4. Veto Matrix Evaluation
        is_counter_trend = (z_cvd * (1 if direction == "LONG" else -1)) > 0
        now_s = now_ms / 1000.0

        # Check all guards
        has_veto = (
            self.news_engine.is_blackout(now_s)
            or self.liquidity_veto.should_veto(state.best_bid, state.best_ask)
            or self.spoofing_veto.should_veto(state, latest_ofi, now_ms)
            or self.flash_crash_veto.should_veto(state, is_counter_trend, now_ms)
            or self.deleveraging_veto.should_veto(
                is_long=(direction == "LONG"),
                is_counter_trend=is_counter_trend,
                price_change_pct=-1.0 if direction == "LONG" else 1.0,
                delta_oi_pct=delta_oi,
                funding_rate=funding_rate,
                z_liq=z_liq,
            )
            or self.funding_boundary_veto.should_veto(
                is_long=(direction == "LONG"),
                funding_rate=funding_rate,
                next_funding_time_ms=self.rest_engine.get_next_funding_time(symbol),
                now_ms=now_ms,
            )
            or self.heat_lock.should_veto(symbol, self.active_signals)
        )

        # 5. Bayesian Synthesis
        eval_z = (
            z_ofi if direction == "LONG" else -z_ofi,
            z_cvd if direction == "LONG" else -z_cvd,
            z_liq,
            z_oi if direction == "LONG" else -z_oi,
            z_smt,
            z_micro if direction == "LONG" else -z_micro,
        )

        synthesis = self.synthesizer.synthesize(
            *eval_z,
            has_active_veto=has_veto,
        )

        # 6. Valid Signal Dispatch
        if synthesis.is_valid:
            entry = state.mid_price
            sweep = state.best_bid if direction == "LONG" else state.best_ask
            atr = self.smt_detector.atrs.get(symbol, entry * 0.005)

            risk = self.risk_calculator.calculate_risk(
                direction=direction,  # type: ignore
                entry_price=entry,
                sweep_price=sweep,
                atr_14=atr,
                win_probability=synthesis.win_probability,
            )

            signal_payload = {
                "symbol": symbol,
                "direction": direction,
                "win_probability": synthesis.win_probability,
                "entry_price": risk.entry_price,
                "stop_loss": risk.stop_loss,
                "risk_r": risk.risk_r,
                "tp1": risk.tp1,
                "tp2": risk.tp2,
                "tp3": risk.tp3,
                "kelly_fraction": risk.kelly_fraction,
                "z_scores": {
                    "z_ofi": z_ofi,
                    "z_cvd": z_cvd,
                    "z_liq": z_liq,
                    "z_oi": z_oi,
                    "z_smt": z_smt,
                    "z_micro": z_micro,
                },
            }

            logger.info("VALID SIGNAL TRIGGERED: %s %s P(Win)=%.3f", symbol, direction, synthesis.win_probability)
            self.active_signals.add(symbol)
            await self.dispatcher.enqueue_signal(signal_payload)

    # ── Lifecycle ───────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the orchestrator pipeline."""
        self._running = True
        logger.info("Starting SignalBotOrchestrator across %d symbols", len(self.symbols))

        # Start Telegram dispatcher worker
        self.dispatcher.start()

        # Refresh macro calendar
        await self.news_engine.refresh_macro_calendar()

        # Start Dual-WebSocket pipeline
        await self.ws_manager.start()

    async def stop(self) -> None:
        """Gracefully stop all pipeline services."""
        logger.info("Stopping SignalBotOrchestrator...")
        self._running = False
        await self.ws_manager.stop()
        await self.dispatcher.stop()
        await self.rest_engine.close()
        await self.news_engine.close()
        logger.info("SignalBotOrchestrator cleanly stopped.")


# ─────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    load_dotenv()
    bot = SignalBotOrchestrator()

    # Graceful shutdown handlers
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def handle_signal():
        logger.info("Shutdown signal received.")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            # Signal handling on Windows
            pass

    await bot.start()

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await bot.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
