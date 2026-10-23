# Phase 2: Microstructure Alpha Engine ⚡

Phase 2 holo bot-er **math and physics laboratory**. Ekhane raw order book updates, aggressive market trades ebong liquidations-ke convert kora hoy quantitative directional edge-e.

Ekhaner shob calculations completely in-memory ebong continuous-time-e ghote. ZERO REST API call kora hoy!

---

## 1. Multi-Level Order Flow Imbalance (L5 OFI)

### Simple Bhashay OFI Ki?
Retail trader-ra chart dekhe candle bondho howar por decision ney. Kintu market-e price move korar ashol karon holo **Order Flow Imbalance** — mane bid side-e limit order joma hochhe naki ask side-e? Limit order cancel hochhe naki notun order asche?

L5 OFI top-5 order book depth levels scan kore prottek 100 millisecond-e.

### Level OFI Formula:
Bid side-er jonno:
- Price barle: Notun higher bid esheche $\rightarrow +Q_{\text{curr}}$
- Price eki thakle: Quantity change hoyeche $\rightarrow Q_{\text{curr}} - Q_{\text{prev}}$
- Price komle: Bid order cancel ba fill hoyeche $\rightarrow -Q_{\text{prev}}$

Ask side-e eita ulto bhabe kaj kore.

### Exponential Depth Decay Weights:
Top-of-book (Level 1) price ke shobcheye beshi impact kore. Level 5 er impact Level 1 er cheye onek kom. Tai amra ekta exponential decay weight use kori:
$$w_i = e^{-\lambda \cdot (i - 1)} \quad (\text{where } \lambda = 0.35)$$

Amader pre-computed weights tuple:
- **Level 1 ($w_0$):** $1.0000$ (100% impact)
- **Level 2 ($w_1$):** $e^{-0.35} \approx 0.7047$
- **Level 3 ($w_2$):** $e^{-0.70} \approx 0.4966$
- **Level 4 ($w_3$):** $e^{-1.05} \approx 0.3499$
- **Level 5 ($w_4$):** $e^{-1.40} \approx 0.2466$

$$\text{OFI}_t = \sum_{i=1}^{5} w_i \cdot (e_{i,t}^b - e_{i,t}^a)$$

Jodi $\text{OFI} > 0$ hoy, tar mane aggressive buying pressure book-e enter korche!

---

## 2. Volume-Weighted Micro-Price Displacement

Normal mid-price holo:
$$P_{\text{mid}} = \frac{P_{\text{bid}} + P_{\text{ask}}}{2}$$

Kintu jodi Bid-e 100 BTC thake ebong Ask-e matro 1 BTC thake, tahole price ki thik majhkhan-e thakbe? **Kokhono na!** Market-er shob pressure ask-ke bhenge upore uthar chesta korbe.

Etai holo **Micro-Price**:
$$P_{\text{micro}} = \frac{P_{\text{bid}} \cdot Q_{\text{ask}} + P_{\text{ask}} \cdot Q_{\text{bid}}}{Q_{\text{ask}} + Q_{\text{bid}}}$$

Amra basis points (bps) displacement calculate kori:
$$\text{MicroSpreadBps} = \left(\frac{P_{\text{micro}} - P_{\text{mid}}}{P_{\text{mid}}}\right) \times 10^4$$

- Positive bps mane Micro-price mid-er upore ache $\rightarrow$ Bullish tilt.
- Negative bps mane Micro-price mid-er niche ache $\rightarrow$ Bearish tilt.

---

## 3. CVD Absorption Divergence (Institutional Limit Buying)

### Concept:
Bhabo market-e ekjon boro whale aggressive sell order marchhe ($m = \text{True}$ in `aggTrade`). CVD druto drop korche (CVD slope negative). 

Kintu price ashole komche na! Keno?
Karon kono **institutional algorithmic buyer** niche limit order rekhe boshe ache, ebong shob aggressive sell order ke "absorb" (gile felche) kore niche!

Eitake bole **Bullish Absorption Divergence**:
$$\text{Score} = \left(\frac{\text{slope}_{\text{CVD}}}{\text{std}_{\text{CVD}}}\right) - \left(\frac{\text{slope}_{\text{Price}}}{\text{std}_{\text{Price}}}\right)$$

Jokhon price niche jacche kintu CVD slope sthir ba positive thakche, Absorption Score boro positive number hoy. Eita institutional support-er shobcheye boro praman!

---

## 4. Liquidation Cascade Exhaustion ($Z_{\text{liq}}$)

Jokhon retail trader-ra over-leverage kore long ba short ney, market maker-ra tader stop hunt kore liquidation trigger kore.

Ekta liquidation hole price aro drop kore, jar fole porer liquidation trigger hoy — eitake bole **Liquidation Cascade (Domino Effect)**.

Amra `!forceOrder@arr` stream theke rolling 60-second liquidation notional volume $V_{\text{liq}}$ track kori.
Tarpor 15-minute streaming baseline-er against-e Welford Z-score calculate kori:
$$Z_{\text{liq}} = \frac{V_{\text{liq}} - \mu_{15\text{m}}}{\sigma_{15\text{m}}}$$

Jokhon **$Z_{\text{liq}} \ge 3.0$** hoy, tar mane market-e shob retailer-ra shobashobi liquidate hoye geche (extreme capitulation). Erpor jodi OFI ghure jay, shetai holo ideal reversal entry!

---

## 5. Phase 2 Veto Guards

Kotogulo signal toiri holo tar cheye beshi gorutto holo **bhul signal theke bacha (False-Positive Suppression)**.

### A. Spoofing Quote-Flicker Veto (`SpoofingQuoteFlickerVeto`):
Boro market maker-ra order book-e fake 100 BTC bid shiye dey retail-der fashanor jonno, tarpor fill howar agei cancel kore dey (Spoofing).
- **Rule 1 (Persistence):** OFI anomaly-ke minimum 5 ta consecutive 100ms updates (500ms) persist korte hobe.
- **Rule 2 (Taker Flow Corroboration):** Shudhu order book dekhle hobe na, `@aggTrade`-eo actual taker buying thakte hobe. Jodi CVD direction OFI-er ulto hoy, signal VETO hoye jay!

### B. Flash-Crash Knife-Catch Veto (`FlashCrashKnifeCatchVeto`):
Porche emon chhuri dhora (falling knife catch kora) suicidal.
- Joto khon liquidation velocity barche ($\frac{dV_{\text{liq}}}{dt} > 0$), kono counter-trend mean-reversion long signal allow kora hoy na!
- Signal shudhumatro tokhon unlock hobe jokhon liquidation cascade peak theke **$> 60\%$ decelerate** (thanda) hobe.
