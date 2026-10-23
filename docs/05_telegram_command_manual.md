# 📱 05. Telegram Command Dashboard Manual

> **Purpose:** Tomake VPS-e bar bar SSH kore log dekhte hobe na. Shob kichu tomar pocket-e Telegram-er madhyome real-time control kora jabe.  
> **Language:** Benglish (Bengali explanation + Telegram examples).

---

## 1. Overview & Security

Bot-ti shudhu tomar configured `TELEGRAM_CHAT_ID` thekei command accept korbe. Jodi onno keu tomar bot-ke command pathay, bot **`ACCESS DENIED`** bolbe ebong security intact thakbe.

Prottek command HTML parse mode-e formatting kora thake, ja dekhte khub clean ebong professional lage.

---

## 2. All 12 Commands (Detailed Breakdown)

### 1. `/help`
- **Ki kore?** Shob available commands ebong tader chotto description list kore.
- **Kokhono bebohar korbe?** Jokhon kono command-er spelling bhule jabe.
- **Example Output:**
  ```text
  🤖 ARUN MARKET ALPHA — COMMAND DASHBOARD

  Available Commands:
  • /status — System health, Uptime, RAM, CPU & WS Connections
  • /ping — Exchange WebSocket latency (Binance & Deribit)
  • /pairs — All 10 tracked symbols & current Bayesian P(Win)
  • /edges <SYM> — Detailed 11-edge breakdown & active triggers
  • /vetoes — Full institutional veto matrix status
  • /risk — Spectral Absorption Ratio (AR) & portfolio heat
  • /scan <SYM> — Diagnostic check on what's missing for a signal
  • /top_edges — Top 3 pairs with strongest order flow alignment
  • /memory — Python RAM usage, GC objects & buffer stats
  • /last_signal — Full details of the last generated alpha signal
  • /version — Git commit hash, build version & license info
  • /help — Show this command manual
  ```

---

### 2. `/status`
- **Ki kore?** System-er health, VPS uptime, RAM usage, CPU load, ebong 4-ti WebSocket-er connection status dekhay.
- **Kokhono bebohar korbe?** Shokalbela ghum theke uthe check korar jonno je bot thik moto cholche kina.
- **Example Output:**
  ```text
  ⚡ SYSTEM HEALTH & CONNECTION STATUS

  ⏱️ Uptime: 3d 14h 22m 10s
  🧠 RAM Usage: 104.2 MB (Limit: 250 MB)
  💻 CPU Load: 1.2%

  📡 WebSocket Connections:
  • USD-M Futures: ONLINE 🟢
  • Spot Lead-Lag: ONLINE 🟢
  • COIN-M Stress: ONLINE 🟢
  • Deribit DVOL: ONLINE 🟢
  • DefiLlama Poller: ONLINE (170 chains)
  ```

---

### 3. `/ping`
- **Ki kore?** Exchange matching engine-er latency (E - T delay) ebong Deribit WebSocket ping check kore.
- **Kokhono bebohar korbe?** Jodi bhabo Binance-e network lag hochhe kina.
- **Example Output:**
  ```text
  🏓 EXCHANGE WEBSOCKET LATENCIES

  • Binance Matching Jitter (E - T): 4.1 ms
  • Binance USD-M Stream: ONLINE (Latency < 15ms)
  • Deribit DVOL Latency: 12.5 ms
  • Latest Deribit BTC DVOL: 53.40
  • Heartbeat Status: HEALTHY (< 35ms threshold)
  ```

---

### 4. `/pairs`
- **Ki kore?** Tracked 10-ti asset-er live price ebong Bayesian $P(\text{Win})$ win probability rank dekhay.
- **Kokhono bebohar korbe?** Market-er general alignment dekhar jonno.
- **Example Output:**
  ```text
  📊 TRACKED ASSET UNIVERSE (10 PAIRS)

  1. #BTCUSDT: $64,250.00 | P(Win): 54.2% ⚪
  2. #ETHUSDT: $3,450.20 | P(Win): 61.8% ⚪
  3. #SOLUSDT: $148.50 | P(Win): 76.5% ⚡
  4. #SUIUSDT: $1.8540 | P(Win): 84.5% 🔥
  5. #LINKUSDT: $14.20 | P(Win): 52.0% ⚪
  6. #TONUSDT: $5.20 | P(Win): 49.8% ⚪
  7. #AVAXUSDT: $28.40 | P(Win): 68.2% ⚪
  8. #NEARUSDT: $4.85 | P(Win): 58.1% ⚪
  9. #AAVEUSDT: $155.00 | P(Win): 51.5% ⚪
  10. #DOGEUSDT: $0.1245 | P(Win): 47.0% ⚪

  🎯 Activation Threshold: P(Win) >= 82.0% with >= 4 active edges
  ```

---

### 5. `/edges <SYMBOL>` (e.g. `/edges SUIUSDT`)
- **Ki kore?** Oi specific coin-er 11-ti microstructure edge-er exact Z-score ebong threshold status breakdown kore.
- **Kokhono bebohar korbe?** Kono coin hot mone hole tar underlying order flow dekhte.
- **Example Output:**
  ```text
  🧠 ORDER FLOW ALPHA EDGES — #SUIUSDT

  1. L5 OFI: +2.85 (Thresh: 2.2) — [ACTIVE] ✅
  2. CVD Absorption: +2.30 (Thresh: 2.0) — [ACTIVE] ✅
  3. Liq Exhaustion: +0.40 (Thresh: 3.0) — [INACTIVE] ⚪
  4. OI & Basis: +1.95 (Thresh: 1.8) — [ACTIVE] ✅
  5. SMT Divergence: +1.20 (Thresh: 1.8) — [INACTIVE] ⚪
  6. Micro-Price: +1.65 (Thresh: 1.5) — [ACTIVE] ✅
  7. Hawkes Cascade: +2.90 (Thresh: 2.5) — [ACTIVE] ✅
  8. Iceberg / Ghost: +2.80 (Thresh: 2.0) — [ACTIVE] ✅
  9. Spot Lead-Lag: +2.60 (Thresh: 2.5) — [ACTIVE] ✅
  10. COIN-M Stress: +1.10 (Thresh: 2.0) — [INACTIVE] ⚪
  11. Variance Ratio: +1.85 (Thresh: 1.5) — [ACTIVE] ✅

  Total Active Edges: 8/11 (Min required: 4)
  ```

---

### 6. `/vetoes`
- **Ki kore?** Shob 12-ti institutional veto-r live status dekhiye dey.
- **Kokhono bebohar korbe?** Signal keno asche na seta janar jonno (e.g., news blackout cholche kina).
- **Example Output:**
  ```text
  🛡️ INSTITUTIONAL VETO MATRIX STATUS

  1. Spoofing Quote-Flicker: CLEAR 🟢
  2. Flash-Crash Knife-Catch: CLEAR 🟢
  3. Macro Calendar Blackout: CLEAR 🟢
  4. Real-Time RSS News Shock: CLEAR 🟢
  5. Deribit Options Gamma Unwind: CLEAR 🟢
  6. US Spot ETF AP Window: CLEAR 🟢
  7. Matching Engine Jitter Veto: CLEAR 🟢
  8. Order Book Liquidity Vacuum: CLEAR 🟢
  9. Deleveraging Cascades: CLEAR 🟢
  10. Funding Settlement Boundary: CLEAR 🟢
  11. DefiLlama Stablecoin Flow: CLEAR 🟢
  12. Spectral Risk Heat Lock: CLEAR 🟢

  🛡️ OVERALL STATUS: CLEARED (0 Active) 🟢
  ```

---

### 7. `/risk`
- **Ki kore?** Spectral Absorption Ratio (AR), market regime, max position cap, ebong active open positions dekhay.
- **Kokhono bebohar korbe?** Market-er overall risk level bujhte.
- **Example Output:**
  ```text
  📐 SPECTRAL RISK & PORTFOLIO ALLOCATION

  • Absorption Ratio (AR): 0.450
  • Market Regime: DISPERSED (Healthy Diversification) 🟢
  • Max Simultaneous Positions: 4
  • Current Open Positions (1): SUIUSDT
  • Quarter-Kelly Cap: 1.25% of capital per signal
  • Eigen-Beta Sizing: Active (Flat 10x10 C-Array Matrix)
  ```

---

### 8. `/scan <SYMBOL>` (e.g. `/scan SOLUSDT`)
- **Ki kore?** Deep diagnostic scan kore bole dey signal fire korar jonno ar ki ki baki ache.
- **Kokhono bebohar korbe?** Jodi dekho kono coin 78% win prob-e ache kintu signal trigger hocche na.
- **Example Output:**
  ```text
  🔍 DIAGNOSTIC SCAN — #SOLUSDT

  • Bayesian P(Win): 77.5% — LOW (77.5% < 82.0%) ❌
  • Edge Alignment: 3/11 Active — INSUFFICIENT (3/4 active) ❌
  • Active Edges: L5 OFI (+2.40), CVD Absorption (+2.10), Spot Lead-Lag (+2.65)
  • Missing Edges: Liq Exhaustion, OI & Basis, Hawkes Cascade, Iceberg / Ghost
  • Status: WAITING FOR CONFLUENCE
  ```

---

### 9. `/top_edges`
- **Ki kore?** 10-ti pair-ke scan kore shobcheye beshi order flow volume ebong alignment thaka top 3 candidates rank kore.
- **Kokhono bebohar korbe?** Next kon coin-e trade ashte pare seta janar jonno.
- **Example Output:**
  ```text
  🏆 TOP 3 ORDER FLOW ALPHA CANDIDATES

  1. #SUIUSDT — Active Edges: 8/11 | P(Win): 84.5% | Magnitude: 22.40
  2. #SOLUSDT — Active Edges: 3/11 | P(Win): 77.5% | Magnitude: 14.20
  3. #ETHUSDT — Active Edges: 2/11 | P(Win): 61.8% | Magnitude: 9.10
  ```

---

### 10. `/memory`
- **Ki kore?** Real-time Python memory usage, GC objects, ring buffer slots dekhay.
- **Kokhono bebohar korbe?** Bot-er 250MB memory ceiling verify korte.
- **Example Output:**
  ```text
  💾 PYTHON RUNTIME MEMORY PROFILE

  • Process RSS Memory: 108.5 MB (Limit: 250.0 MB)
  • Memory Status: HEALTHY (Well under 250MB limit) 🟢
  • Garbage Collection Objects: 84,210
  • Tracked Symbols: 10 (__slots__ compliant)
  • Active Ring Buffer Slots: 12,450
  • Covariance Tensor: 440 bytes (Zero NumPy / SciPy)
  ```

---

### 11. `/last_signal`
- **Ki kore?** Sobcheye recent je signal-ti Telegram-e pathano hoyechilo seta punaray dekhay (Entry, SL, TP1, TP2, TP3, Kelly sizing, Active Edges).
- **Kokhono bebohar korbe?** Jodi ager signal miss kore thako.

---

### 12. `/version`
- **Ki kore?** Bot-er exact version, git commit hash, zero-cost read-only disclaimer dekhay.
- **Example Output:**
  ```text
  ℹ️ ARUN MARKET ALPHA — VERSION & BUILD

  • Version: v8.0.0-institutional
  • Git Commit: bba6f92
  • Architecture: Pure Python 3.11+ / Microsecond Order Flow
  • Banned Libraries: 0 NumPy, 0 Pandas, 0 SciPy
  • Mode: Zero-Cost, Read-Only Signal Generation
  ```
