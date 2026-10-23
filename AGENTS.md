# AGENTS.md — Project Rules & Constraints

> [!CAUTION]
> All agents and contributors **MUST** follow these rules without exception.

---

## 1. Project Purpose

We are building a **brand new, zero-cost, read-only crypto futures signal bot** based on **order flow microstructure**. This is a greenfield project — no legacy code, no paid APIs, no trade execution.

---

## 2. Production Bot — DO NOT TOUCH

**NEVER** touch, read, or modify anything in the `../neo-wilder-signal-bot` folder.  
It is a **running production bot**. Any interaction with that directory — including reads — is strictly forbidden.

---

## 3. Memory Constraints — No Heavy Libraries

We are operating under a **1 GB RAM limit**. The following libraries are **banned**:

- ❌ `pandas`
- ❌ `numpy`
- ❌ `scipy`

**Use instead:**

- ✅ Python standard library `array` module for typed numeric arrays.
- ✅ `collections.deque` for sliding windows and bounded buffers.
- ✅ Built-in `math` and `statistics` modules for calculations.

---

## 4. WebSocket Data Parsing

- Use **`orjson`** for parsing raw bytes from WebSockets.
- **Do NOT** decode bytes to strings first — pass raw `bytes` directly to `orjson.loads()`.

```python
# ✅ Correct
data = orjson.loads(raw_bytes)

# ❌ Wrong — unnecessary string allocation
data = orjson.loads(raw_bytes.decode("utf-8"))
```

---

## 5. Memory-Efficient Data Classes

- Use **`__slots__`** on **all** data classes to eliminate per-instance `__dict__` overhead.

```python
# ✅ Correct
class Trade:
    __slots__ = ("price", "qty", "side", "timestamp")

    def __init__(self, price: float, qty: float, side: str, timestamp: int):
        self.price = price
        self.qty = qty
        self.side = side
        self.timestamp = timestamp
```

---

## 6. Testing Requirements

- Write **`pytest` unit tests** for **every module** created.
- Tests live in a `tests/` directory mirroring the source structure.
- All tests must pass before any module is considered complete.

---

## Summary Checklist

| Rule | Requirement |
|------|-------------|
| Purpose | Zero-cost, read-only signal bot (order flow) |
| `../neo-wilder-signal-bot` | **NEVER** touch, read, or modify |
| Banned libs | `pandas`, `numpy`, `scipy` |
| Allowed structures | `array`, `collections.deque`, `math`, `statistics` |
| JSON parsing | `orjson.loads(raw_bytes)` — no `.decode()` |
| Data classes | Always use `__slots__` |
| Testing | `pytest` tests for every module |
