# 🖥️ 01. VPS Setup & Live Deployment Guide (1GB RAM Linux VPS)

> **Audience:** Beginner jara nijeder 1GB RAM-er chotto Ubuntu VPS-e kono paid tool chara production bot run korte chay.  
> **Language:** Benglish (Bengali + English Technical Terms).

---

## 1. Introduction: Keno 1GB VPS?

Amra jokhon kono institutional crypto signal bot banai, retail trader-ra bhabe er jonno 32GB RAM ba expensive cloud server dorkar. Kintu amader architecture emon bhabe design kora hoyeche jate **Zero NumPy, Zero Pandas, ebong 100% `__slots__` memory optimization** thake.

Ei karone shob milie bot-ti matro **80MB theke 150MB RAM** bebohar kore! Er mane tumi ekta simple **\$4-\$5/month er 1GB RAM Ubuntu VPS**-e (DigitalOcean, Hetzner, Linode ba AWS EC2 t4g.micro) 24/7 bina crash-e eta run korte parbe.

---

## 2. Step 1: VPS Initial Setup & System Updates

SSH die prothome tomar VPS-e login koro:

```bash
ssh root@YOUR_VPS_IP_ADDRESS
```

System update kore nao:

```bash
apt update && apt upgrade -y
```

Python 3.11+, Git, ebong essential build tools install koro:

```bash
apt install -y python3 python3-pip python3-venv git htop curl
```

Python version verify koro (must be 3.11 ba tar beshi):

```bash
python3 --version
# Expected: Python 3.11.x ba 3.12.x
```

---

## 3. Step 2: Swap Memory Configure Kora (Safety Net)

> [!IMPORTANT]
> 1GB RAM-e OS background task ebong memory spikes handle korar jonno at least **2GB Swap space** thaka khub dorkar. Eta VPS-ke crash theke bachay.

```bash
# 2GB Swap file banano
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile

# Permanent kora jate reboot-er por na haray
echo '/swapfile none swap sw 0 0' | tee -a /etc/fstab

# Verify koro
free -h
```

---

## 4. Step 3: Codebase Clone & Virtual Environment

Tomar project repository clone koro:

```bash
cd /root
git clone https://github.com/arunabhamaity148-cell/ARUN-THE-BEST-STUDENT-OF-MARKET.git
cd ARUN-THE-BEST-STUDENT-OF-MARKET
```

Virtual environment toiri koro:

```bash
python3 -m venv venv
source venv/bin/activate
```

Dependencies install koro:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

> [!NOTE]
> Linux VPS-e `uvloop` automatically compile hoye jabe ja asyncio event loop-ke C-speed-e fast kore dey!

---

## 5. Step 4: Environment Variables (`.env`) Configuration

Tomar Telegram credentials configure korte hobe:

```bash
cp .env.example .env
nano .env
```

`.env` file-er moddhe ei duiti values dao:

```ini
TELEGRAM_BOT_TOKEN="1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
TELEGRAM_CHAT_ID="987654321"
```

- `TELEGRAM_BOT_TOKEN`: Telegram-e `@BotFather` theke create kora bot-er HTTP API token.
- `TELEGRAM_CHAT_ID`: Telegram-e `@userinfobot`-ke message dile je numerical ID pabe (e.g. `987654321`). Bot shudhu ei ID-te signal pathabe ebong ei ID chara onno karor command accept korbe na!

File save kore beriye asho (`CTRL + O`, `Enter`, `CTRL + X`).

---

## 6. Step 5: Test Run & Verification

Direct terminal-e run kore dekho shob WebSocket connect hochhe kina:

```bash
python main.py
```

Tomar terminal-e emon log dekha jabe:
```text
[INFO] Starting SignalBotOrchestrator across 10 symbols
[INFO] [usdm] connected → wss://fstream.binance.com/stream?streams=...
[INFO] [spot] connected → wss://stream.binance.com:9443/stream?streams=...&timeUnit=MICROSECOND
[INFO] [coinm] connected → wss://dstream.binance.com/stream?streams=...
[INFO] [deribit] connected → wss://www.deribit.com/ws/api/v2
[INFO] alt_data_veto: Updated DefiLlama stablecoin 24h changes for 170+ chains
```

Shob thik thakle `CTRL + C` chepe bondho koro.

---

## 7. Step 6: Production Systemd Service Setup (`arun-market-bot`)

Terminal bondho korle ba VPS theke logout korleo bot jate 24/7 background-e cholte thake, tar jonno ekta `systemd` service banano dorkar:

```bash
nano /etc/systemd/system/arun-market-bot.service
```

Nicher content-ti paste koro:

```ini
[Unit]
Description=Arun Institutional Crypto Microstructure Signal Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/ARUN-THE-BEST-STUDENT-OF-MARKET
ExecStart=/root/ARUN-THE-BEST-STUDENT-OF-MARKET/venv/bin/python main.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
LimitNOFILE=65536

# Environment variables
EnvironmentFile=/root/ARUN-THE-BEST-STUDENT-OF-MARKET/.env

[Install]
WantedBy=multi-user.target
```

Save koro (`CTRL + O`, `Enter`, `CTRL + X`).

Service enable ebong start koro:

```bash
systemctl daemon-reload
systemctl enable arun-market-bot
systemctl start arun-market-bot
```

Status check koro:

```bash
systemctl status arun-market-bot
```

`Active: active (running)` dekhale bujhbe bot shundor bhabe background-e run korche!

---

## 8. Step 7: How to Check Live Logs with `journalctl`

Live real-time streaming logs dekhte ei command bebohar koro:

```bash
# Real-time tailing (last 100 lines + continuous stream)
journalctl -u arun-market-bot -n 100 -f
```

Recent error dekhar jonno:

```bash
journalctl -u arun-market-bot -p err..emerg -n 50
```

Service restart korar command:

```bash
systemctl restart arun-market-bot
```

Service stop korar command:

```bash
systemctl stop arun-market-bot
```

---

## 9. Step 8: Memory & Resource Monitoring

VPS-e memory monitoring-er jonno `htop` run korte paro:

```bash
htop
```

Tumi dekhbe python process-ti shob milie matro **80MB theke 140MB RAM** bebohar korche. Swap memory 0MB-te thakbe karon kono memory leak nei!
Ekhon tumi tomar Telegram-e giye `/status` command dile bot live reply debe!
