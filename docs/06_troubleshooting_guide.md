# 🛠️ 06. Troubleshooting & Operational Guide

> **Target:** Live VPS production maintenance, error resolution, ebong zero-downtime recovery.  
> **Language:** Benglish (Bengali + Linux Command line).

---

## 1. WebSocket Disconnection & Auto-Reconnect

### Scenario:
Telegram-e `/status` dile dekhle kono ekta connection (e.g. `Spot` ba `USD-M`) `OFFLINE 🔴` dekhacche.

### Ken hoy?
Binance server 24 ghonta por por automatic connection refresh kore, ba internet routing-e chotto hiccup hoy.

### Amader Built-In Fix:
Bot-er moddhe ache **Exponential Back-Off Reconnector**:
- Prothom retry: 1.0s
- Second retry: 1.5s
- Max ceiling: 30.0s
Bot continuous background-e reconnect korte thakbe jotokkhon na handshake successful hoy. Tomar kichu korte hobe na!

### Jodi 5 minute-eo reconnect na hoy:
VPS-e SSH kore journal log check koro:
```bash
journalctl -u arun-market-bot -n 50 --no-pager
```
Jodi dekho Binance IP rate-limit dieche (HTTP 429 ba 418), tahole bot-ke restart koro:
```bash
systemctl restart arun-market-bot
```

---

## 2. Memory Spikes & RAM Leak Prevention

### Scenario:
Tumi `/memory` dile dekhle RAM 200MB-er opore uthe geche.

### Investigation:
Amader bot-e shob deques bounded (`maxlen` deoya ache), ebong shob class-e `__slots__` ache. Tai memory spike howar kotha na.
Kintu jodi system level-e onno kono process RAM khay:

1. VPS-e `htop` run koro:
   ```bash
   htop
   ```
2. Python process-er PID dekho ebong tar memory column (`RES`) check koro.
3. Garbage collector run korar jonno VPS reboot ba service restart koro:
   ```bash
   systemctl restart arun-market-bot
   ```

---

## 3. How to Restart the Bot (Clean Restart Procedure)

Kono code update korle ba `.env` modify korle:

```bash
# 1. Project directory-te jao
cd /root/ARUN-THE-BEST-STUDENT-OF-MARKET

# 2. Latest code pull koro
git pull origin main

# 3. Systemd daemon reload koro
systemctl daemon-reload

# 4. Service restart koro
systemctl restart arun-market-bot

# 5. Status verify koro
systemctl status arun-market-bot

# 6. Live logs follow koro
journalctl -u arun-market-bot -n 30 -f
```

---

## 4. Telegram Command-e Bot Reply Na Dile Ki Korbe?

Jodi Telegram-e `/status` ba `/ping` pathanor por bot kono reply na dey:

1. **Check Chat ID:**
   Nishchit koro tumi je Telegram account theke command pathaccho, tar numerical ID `.env` file-er `TELEGRAM_CHAT_ID`-r shathe match kore.
2. **Check Bot Token:**
   `.env` file-e token thik ache kina:
   ```bash
   cat /root/ARUN-THE-BEST-STUDENT-OF-MARKET/.env
   ```
3. **Check Service Running:**
   ```bash
   systemctl status arun-market-bot
   ```
   Jodi crash kore thake, `journalctl -u arun-market-bot -n 50` die exact traceback dekho.
4. **Telegram Rate Limits:**
   Telegram bot API per second-e matro 1 message allow kore per chat. Ekshonge 10-ta command pathale token-bucket back-off pause nite pare. 2 second wait kore abar command dao.

---

## 5. Summary Cheat Sheet

| Task | Command |
|---|---|
| Service Status | `systemctl status arun-market-bot` |
| Service Restart | `systemctl restart arun-market-bot` |
| Service Stop | `systemctl stop arun-market-bot` |
| View Live Logs | `journalctl -u arun-market-bot -n 50 -f` |
| Check RAM | `free -h` ba `htop` |
| Check Swap | `swapon --show` |
