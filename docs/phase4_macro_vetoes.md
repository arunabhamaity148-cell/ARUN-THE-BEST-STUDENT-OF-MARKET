# Phase 4: Macro News & Institutional Veto Matrix 🛑

Trading-e jeta shobcheye beshi account wipeout kore, sheta holo **unexpected macro news spikes** ebong **crypto black swan events** (hack, exchange insolvency, depeg).

Phase 4 holo amader **Iron Dome Defense System** — ja technical signal jotoi sundor hok na keno, market-e macro risk thakle trade-e enter korte dey na!

---

## 1. ForexFactory Macro Calendar Blackout

US macroeconomic news (jemon CPI, Non-Farm Payrolls, FOMC Interest Rate Decision) jokhon release hoy, tokhon crypto market-e bid/ask order book pura faka hoye jay ebong massive slippage hoy.

### How it Works:
1. Engine ForexFactory theke calendar download kore: `https://nfs.faireconomy.media/ff_calendar_thisweek.json`.
2. **12-Hour Disk Cache:** Server bar bar remote server-e hit kore na. File-ta disk-e cache thake ebong prottek 12 ghontay (43,200s) matro ekbar refresh hoy.
3. **Filter:** Shudhumatro `country == "USD"` ebong `impact == "High"` events filter kora hoy.
4. **Blackout Window:** High-impact event release-er **15 minute age theke 15 minute por porjonto ($\pm 900\text{s}$)** shob dhoroner signal strictly VETO kora hoy!

---

## 2. Real-Time Crypto RSS Shock Scanner

Jodi kono major crypto bridge hack hoy ba kono stablecoin depeg hoy, order flow indicator-ra sheta bojhate bojhate price 20% pore jabe. Tai amader dorkar instant news awareness.

### Zero Heavy NLP Rule:
Amra kono heavy library (jemon `spacy`, `transformers`, `beautifulsoup4`) use kori na, karon era 500MB+ RAM kheye fele!
Tar bodole amra standard Python-er built-in **`xml.etree.ElementTree`** use korechi.

- **Sources:** CoinDesk RSS ebong Cointelegraph RSS feeds poll kora hoy.
- **Top 10 Headlines:** Prottek feed-er top 10 items scan kora hoy.
- **Immediate Memory Clearance:** XML parse korar por-i `root.clear()` call kore DOM tree memory theke shoriye deoya hoy.
- **Shock Keywords:**
  - `hack`
  - `exploited` / `exploit`
  - `insolvency`
  - `sec sues`
  - `depeg`
- **45-Minute Mandatory Blackout:** Ei shob keywords er kono ekta paoa gele shat-shathe **45 minutes (2700s)** er jonno entire bot blackout mode-e chole jay!

---

## 3. Correlation Heat Lock (Portfolio Risk Management)

Tumi jodi eksathe BTC Long, ETH Long, ebong SOL Long teeno tai open koro, tahole tumi ashole 3 te alada trade korcho na — tumi eki asset-e 3 gun risk nichho! BTC drop korle teeno tai loss-e jabe.

Eitake bole **Systemic Correlation Risk**.

### Solution (`CorrelationHeatLock`):
1. **Rolling 60-Minute Log Returns:**
   $$r_t = \ln\left(\frac{P_t}{P_{t-1}}\right)$$
   Prottek symbol-er last 60 minutes-er return buffer thake.
2. **Pearson Correlation Matrix ($\rho_{i,j}$):**
   Candidate symbol ebong currently active trade-er moddhe correlation measure kora hoy.
3. **Heat Lock Veto Rules:**
   - **Rule 1 (Concurrency Cap):** Market-e maximum **3 te active signal** eksathe thakte pare. 4 number signal shorashori reject hoy.
   - **Rule 2 (Correlation Overexposure):** Candidate symbol-er correlation jodi kono active signal-er sathe **$|\rho| > 0.75$** hoy, tahole shei signal reject hoy!

---

## 4. Liquidity Vacuum Veto

Jokhon market maker-ra order book theke limit order tule ney (Liquidity Vacuum), tokhon chhoto ekta market order-o price-ke 2% jump koriye dite pare.

### Two Guards in `LiquidityVacuumVeto`:
1. **Spread Blowout Veto:**
   $$S_{\text{bps}} = \left(\frac{\text{ask} - \text{bid}}{\text{mid}}\right) \times 10^4$$
   Normal shomoy spread thake 0.5 theke 1.5 bps. Jodi **$S_{\text{bps}} > 3.5\text{ bps}$** hoy, tar mane order book faka hoye geche $\rightarrow$ Signal VETO!
2. **Top-5 Depth Vacuum Veto:**
   $$D_5 = \sum_{i=1}^{5} (P_{\text{bid}, i} \cdot Q_{\text{bid}, i} + P_{\text{ask}, i} \cdot Q_{\text{ask}, i})$$
   Welford algorithm diye rolling average depth $\mu_{D5}$ ebong $\sigma_{D5}$ track kora hoy.
   Jodi $D_5 < (\mu_{D5} - 1.5 \cdot \sigma_{D5})$ hoy, tar mane book depth normal-er theke $1.5\sigma$ kom $\rightarrow$ Signal VETO!
