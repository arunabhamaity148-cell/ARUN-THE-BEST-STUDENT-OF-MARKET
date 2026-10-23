# Phase 5: Signal Synthesis, Dynamic Risk Sizing, & Telegram Dispatch 🎯

Phase 5 holo shobgulo component-er **final convergence**. Ekhane shob edge eksathe boshe Bayesian mathematical probability calculate kore, Kelly criterion diye safe position size thik kore, ebong Telegram-e ultra-low-latency-te signal pathay.

---

## 1. Bayesian Log-Odds Signal Synthesizer (`SignalSynthesizer`)

Kono ekta single indicator (jemon shudhu OFI ba shudhu SMT) diye high-win-rate signal toiri kora jay na. Amader dorkar **Confluence (ekadhik edge-er mil)**.

### Log-Odds Formula:
$$L_t = \beta_0 + \sum_{k=1}^{6} w_k \cdot \text{clip}(z_k, -4.0, +4.0)$$

- **$\beta_0 = -1.5$:** Baseline prior odds (conservative skepticism). Kono edge na thakle bot kono trade nebe na.
- **Weights:**
  - $w_1 = 0.28$: Multi-Level L5 OFI (order book pressure)
  - $w_2 = 0.22$: CVD Absorption Divergence (institutional limit absorption)
  - $w_3 = 0.20$: Liquidation Cascade Exhaustion (retail capitulation)
  - $w_4 = 0.14$: OI & Basis Positioning (derivatives skew)
  - $w_5 = 0.10$: Cross-Asset SMT (BTC/ETH/SOL non-confirmation)
  - $w_6 = 0.06$: Micro-Price Displacement (instantaneous volume skew)

### Sigmoid Probability:
$$P(\text{Win}) = \frac{1}{1 + e^{-L_t}}$$

### Triple Activation Gate:
Kono signal valid hote gele 3 te kothin shoto puron korte hoy:
1. **$P(\text{Win}) \ge 0.820$** (minimum 82% statistical probability).
2. **$\ge 3$ Distinct Edges:** Minimum 3 te alada edge-ke tader threshold exceed korte hobe (OFI $\ge 2.2$, CVD $\ge 2.0$, Liq $\ge 3.0$, OI $\ge 1.8$, SMT $\ge 1.8$, Micro $\ge 1.5$). Ete fake single-spike bypass hoye jay.
3. **Zero Active Vetoes:** Veto matrix-er 7 ta guard-er moddhe ektao jodi veto bole, signal shorashori dismiss hoye jabe!

---

## 2. Dynamic Risk & Volatility Sizing (`RiskCalculator`)

Signal jotoi bhalo hok, **Risk Management chara trading holo gambling**.

### A. Volatility-Buffered Stop Loss (SL):
Market maker-ra shobshomoy obvious swing point-e hunt kore. Tai amader stop loss ATR-er buffer diye protect kora:
- **LONG SL:** $\min(P_{\text{sweep}}, P_{\text{entry}} - 1.5 \times \text{ATR}_{14}) - 0.35 \times \text{ATR}_{14}$
- **SHORT SL:** $\max(P_{\text{sweep}}, P_{\text{entry}} + 1.5 \times \text{ATR}_{14}) + 0.35 \times \text{ATR}_{14}$

Unit Risk:
$$R = |P_{\text{entry}} - \text{SL}|$$

### B. Multi-Tiered Take Profits (Scaling Out):
Professional institutional trader-ra kokhono ekbare shob position close kore na:
- **TP1 ($+1.5R$):** $40\%$ position close kore profit lock kora hoy.
- **TP2 ($+2.8R$):** Aro $40\%$ position close kora hoy.
- **TP3 ($+4.5R$):** Baki $20\%$ runner hishebe chhere deoya hoy boro trend dhorar jonno.

### C. Fractional Kelly Criterion Sizing:
$$f^* = \min\left(0.0125, 0.25 \times \left(p - \frac{1 - p}{b}\right)\right)$$
- $p$: Win probability (e.g. 0.85)
- $b = 2.45$: Average payoff ratio (average win / average loss)
- **0.25 (Quarter-Kelly):** Full Kelly aggressively volatile; quarter-Kelly smooth exponential equity curve toiri kore.
- **1.25% Hard Cap:** Kono trade-e kokhono portfolio-r $1.25\%$-er beshi risk neoya hobe na!

---

## 3. Asynchronous Telegram Dispatcher (`TelegramSignalDispatcher`)

### Token-Bucket Rate Limiter:
Telegram Bot API-te rate limit kothin:
- **Per-Chat:** Max 1 message per 1.05s.
- **Per-Group:** Max 18 messages per 60s.
- **`asyncio.Queue(maxsize=100)`:** Signal gulo memory buffer queue-te joma hoy ebong worker loop background-e smoothly dispatch kore.

### HTTP 429 Auto-Backoff:
Jodi Telegram kokhono 429 (Too Many Requests) return kore, dispatcher tar response-er `parameters.retry_after` read kore toto second sleep kore automatic retry kore.

### HTML Parse Mode:
MarkdownV2-te `.` ba `_` thakle Telegram parse error dey. Tai amra **HTML parse mode** (`<b>`, `<code>`) use korechi ja 100% bug-free format produce kore.
