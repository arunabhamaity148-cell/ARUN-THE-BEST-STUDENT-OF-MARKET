# Phase 1: Zero-Cost Ingestion & Memory Core 🚀

Phase 1 holo amader trading bot-er **heartbeat ebong foundation**. Ekhan theke shuru hoy market-er shob raw data pipeline. 

Jodi foundation strong na hoy, tahole boro boro mathematical calculation ba high-frequency order flow analysis kono kajer na. Ei phase-e amra emon ekta data pipeline baniyechi ja **Binance Futures** theke second-e hazar hazar tick consume kore, kintu kono paid API ba heavy server chara — shudhumatro **150MB RAM**-er moddhe run kore!

---

## 1. Keno Dual-WebSocket Architecture?

Binance Futures-e shob data ekta connection diye anle connection choke hoye jete pare ebong rate limit issue hote pare. Tai amra system-ke duto independent WebSocket connection-e vag korechi:

1. **Public Stream Connection (`wss://fstream.binance.com/stream?streams=...`):**
   - **`depth20@100ms`:** Order book-er top 20 bid ebong ask levels prottek 100 millisecond por por update hoy. Eita amader order flow imbalance (OFI) er raw material.
   - **`bookTicker`:** Best bid ebong best ask price ebong quantity real-time instantaneous stream.

2. **Market Stream Connection (`wss://fstream.binance.com/stream?streams=...`):**
   - **`aggTrade`:** Shob aggressive market buy ebong market sell trades. Eita amader CVD (Cumulative Volume Delta) banate lage.
   - **`kline_1m`:** 1-minute candlestick bars, jar theke amra swing high/low ebong ATR calculate kori.
   - **`!forceOrder@arr`:** Global liquidation stream. Jokhon kono over-leveraged trader liquidate hoy, tar market order ekhane chole ashe.

```mermaid
flowchart TD
    Binance[Binance Futures WebSockets] -->|Public Stream| WS1[WS Connection 1: Book & Depth]
    Binance -->|Market Stream| WS2[WS Connection 2: Trades & Liqs]
    
    WS1 -->|depth20@100ms & bookTicker| Ingestion[ingestion/ws_manager.py]
    WS2 -->|aggTrade & forceOrder & kline| Ingestion
    
    Ingestion -->|Raw Bytes orjson.loads| State[core/state.py: SymbolMicroState]
```

---

## 2. Strict Memory Constraints (1GB RAM Rules)

Amader server-e RAM limit matro 1GB. Tai amra hedge-fund grade optimization use korechi:

- ❌ **No `pandas` & No `numpy`:** Ei libraries gulo onek memory consume kore (ek ekta DataFrame tens of megabytes ney). Amra standard Python `array` ebong `collections.deque` use korechi.
- ⚡ **`__slots__` everywhere:** Python-er normal class-e prottek instance-er jonno ekta `__dict__` dictionary thake, ja prochur RAM khay. `__slots__` use korle memory overhead 70% kome jay!
- 🏎️ **`orjson` Raw Bytes Parsing:** WebSocket theke jokhon frame ashe, amra kokhono `.decode('utf-8')` kori na! Direct raw `bytes`-ke `orjson.loads(raw_bytes)` diye parse kori. Ete CPU cycle ebong string memory allocation bache.

```python
# ✅ Etai holo amader memory saving rule
data = orjson.loads(raw_bytes)  # Zero UTF-8 decoding string allocation
```

---

## 3. Welford's Online Algorithm (`OnlineNormalizer`)

Trading-e amader proshno thake: *"Ei matro je volume ba spread ta ashlo, sheta ki normal naki extreme anomaly?"*

Eta janar jonno amader **Z-score** lagbe:
$$Z = \frac{x - \mu}{\sigma}$$

Kintu jodi tumi 10,000 data point ekta list-e rekhe bar bar `mean()` ebong `std()` calculate koro, tahole CPU 100% hoye jabe ebong latency bere jabe!

Tai amra use korechi **Welford's Algorithm (1962)**:
- Eita ekta **$O(1)$ streaming algorithm**.
- Kono data array store korte hoy na!
- Shudhumatro 3 te floating point numbers store kore: `_count`, `_mean`, ebong `_m2` (sum of squared differences).
- Prottek notun tick ashar sathe sathe instant mean ebong standard deviation update hoye jay zero memory allocation-e.

```python
class OnlineNormalizer:
    __slots__ = ("_count", "_mean", "_m2")

    def update(self, value: float) -> None:
        self._count += 1
        delta = value - self._mean
        self._mean += delta / self._count
        delta2 = value - self._mean
        self._m2 += delta * delta2

    @property
    def std_dev(self) -> float:
        if self._count < 2:
            return 0.0
        return math.sqrt(self._m2 / self._count)
```

---

## 4. `SymbolMicroState` — In-Memory Order Book State

Prottek symbol-er (`BTCUSDT`, `ETHUSDT`, `SOLUSDT`) jonno ekta kore `SymbolMicroState` instance memory-te thake.

Ete thake:
- `best_bid`, `best_ask`, `best_bid_qty`, `best_ask_qty`
- `cvd`: Cumulative Volume Delta (Market Buy Volume − Market Sell Volume)
- Bounded buffers (`deque(maxlen=500)`): Purono data automatic pop hoye jay, tai bot 1 bochor continuous cholleo RAM kokhono barte pare na.
- Fixed C-double arrays: `array('d', [0.0]*5)` level 1 theke 5 er price ebong quantity track korar jonno.
