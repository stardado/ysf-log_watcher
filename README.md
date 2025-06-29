
# YSF Log Watcher

Ein kleines Python-Tool, das das YSFReflector-Log überwacht, DMR-Übertragungen auf einer bestimmten Talkgroup erkennt und Benachrichtigungen in einen Telegram-Chat sendet.  
Der Watcher verhindert Mehrfachmeldungen für dasselbe Rufzeichen innerhalb von 15 Minuten und setzt den Timer zurück, wenn das Rufzeichen innerhalb dieser Zeit erneut sendet.

---

## Funktionen

- **Echtzeit-Überwachung** von `/var/log/YSFReflector/YSFReflector.log`
- **Automatischer Talkgroup-Name-Abgleich** über die BrandMeister CSV
- **DMR-Nutzer-Infos** (Name, DMR-ID, Stadt, Land) via radioid.net
- **Telegram-Benachrichtigungen** im Markdown-Format (Rufzeichen, Name, Standort, Talkgroup-Link)
- **Wiederholungsschutz**: nur eine Benachrichtigung pro Rufzeichen alle 15 Minuten (Reset bei Reaktivierung)
- Vollständig konfigurierbar über Umgebungsvariablen

---

## Voraussetzungen

- **Linux-Server** mit Python 3.8 oder neuer
- Git (zur Versionsverwaltung)
- Ein Telegram-Bot-Token und Chat-ID
- Python-Pakete:
  - `aiohttp`
  - `python-telegram-bot`
  - `python-dotenv`

---

## Installation

1. **Repository klonen (oder Dateien kopieren)** nach `/opt`:

   ```bash
   cd /opt
   git clone git@github.com:stardado/ysf-log_watcher.git
   cd ysf-log_watcher
   ```

2. **Python-Umgebung erstellen und aktivieren (optional, empfohlen):**

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Abhängigkeiten installieren:**

   ```bash
   pip install -r requirements.txt
   ```

Die Verzeichnisstruktur sollte nun so aussehen:

```
/opt/ysf-log_watcher/
├── ysf_log_watcher.py
├── requirements.txt
└── .env            ← (sollte durch .gitignore geschützt sein)
```

---

## Konfiguration

Alle Einstellungen werden über Umgebungsvariablen gesteuert.  
Lege die Datei `/opt/ysf-log_watcher/.env` an mit folgendem Inhalt:

```dotenv
# Telegram-Bot-Token (erforderlich)
TELEGRAM_BOT_TOKEN=DEIN_TELEGRAM_BOT_TOKEN

# Telegram-Chat-ID (erforderlich, kann auch ein Gruppenname sein)
TELEGRAM_CHAT_ID=@DEIN_CHAT_ID

# Talkgroup-Nummer (Standard: 264555)
TALKGROUP=264555

# Minimale Sendedauer (in Sekunden, Standard: 4)
MIN_DURATION=4

# Zeitfenster (in Sekunden) für Wiederholungsbenachrichtigungen (Standard: 900 = 15 Minuten)
TIMER_DURATION=900

# URL der BrandMeister Talkgroup CSV (Standard-URL wird genutzt)
TALKGROUP_URL=https://w0chp.radio/brandmeister-talkgroups/brandmeister-talkgroups.csv
```

> **Wichtig:** Die `.env`-Datei darf niemals in ein öffentliches Git-Repository gepusht werden.

---

## Manuelle Ausführung

Testweise kannst du den Watcher direkt ausführen:

```bash
python ysf_log_watcher.py
```

Das Skript:
- Lädt die aktuelle BrandMeister CSV herunter.
- Überwacht `/var/log/YSFReflector/YSFReflector.log`.
- Sendet Telegram-Benachrichtigungen bei jeder gültigen Übertragung (≥ `MIN_DURATION`), wenn das Rufzeichen innerhalb der letzten `TIMER_DURATION` Sekunden noch nicht gemeldet wurde.  
- Bei neuer Aktivität wird das Zeitfenster zurückgesetzt.

---

## Als systemd-Service einrichten

Erstelle die Datei `/etc/systemd/system/ysf-log_watcher.service`:

```ini
[Unit]
Description=YSF Log Watcher
After=network.target

[Service]
Type=simple
User=ysfuser
WorkingDirectory=/opt/ysf-log_watcher
EnvironmentFile=/opt/ysf-log_watcher/.env
ExecStart=/opt/ysf-log_watcher/venv/bin/python ysf_log_watcher.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Service aktivieren und starten

```bash
sudo systemctl daemon-reload
sudo systemctl enable ysf-log_watcher
sudo systemctl start ysf-log_watcher
```

### Service-Status prüfen

```bash
sudo systemctl status ysf-log_watcher
```

### Live-Logs anzeigen

```bash
sudo journalctl -u ysf-log_watcher -f
```

---

## Logging

- Das Skript loggt in die Datei:  
  `/var/log/log_watcher.log`
- Zusätzlich wird auf der Konsole ausgegeben (wenn manuell gestartet).
- Alle Fehler, Infos und Debug-Nachrichten sind mit Zeitstempel versehen.
- Der Service-User (z. B. `ysfuser`) muss Schreibrechte für das Log-Verzeichnis haben.

---

## Fehlerbehebung

### 1. Keine Telegram-Nachrichten erhalten
- Prüfe, ob das Telegram-Bot-Token in `.env` korrekt ist.
- Prüfe, ob die Telegram-Chat-ID korrekt ist.
- Prüfe die Service-Logs:
  
  ```bash
  sudo journalctl -u ysf-log_watcher -e
  ```

### 2. Talkgroup-Namen werden nicht angezeigt
- Prüfe, ob die CSV korrekt heruntergeladen wurde:
  
  ```bash
  head -n 5 /opt/ysf-log_watcher/brandmeister_talkgroups.csv
  ```

### 3. Service startet nicht wegen Berechtigungen
- Stelle sicher, dass der Service-User Zugriff auf das Projektverzeichnis hat:

  ```bash
  sudo chown -R ysfuser:ysfuser /opt/ysf-log_watcher
  ```


