import os
from dotenv import load_dotenv
import asyncio
import aiohttp
import datetime
import logging
import re
import sys
import csv
from zoneinfo import ZoneInfo
from telegram.ext import ApplicationBuilder

# Determine base directory (where script resides)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load environment variables from .env in BASE_DIR
load_dotenv(os.path.join(BASE_DIR, '.env'))

# Configuration
# The log file is managed by a different software and must not change
LOGFILE = '/var/log/YSFReflector/YSFReflector.log'
# Telegram token must be provided via environment
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
if not TELEGRAM_BOT_TOKEN:
    logging.error("Umgebungsvariable TELEGRAM_BOT_TOKEN fehlt!")
    sys.exit(1)
# Chat ID, default remains as before
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '@TG264555')
# Talkgroup and thresholds
MIN_DURATION = int(os.environ.get('MIN_DURATION', 4))        # seconds
TIMER_DURATION = int(os.environ.get('TIMER_DURATION', 900))  # seconds (15 min)
TALKGROUP = os.environ.get('TALKGROUP', '264555')
TALKGROUP_URL = os.environ.get(
    'TALKGROUP_URL',
    'https://w0chp.radio/brandmeister-talkgroups/brandmeister-talkgroups.csv'
)
# Store CSV locally within the project directory
LOCAL_TG_FILE = os.path.join(BASE_DIR, 'brandmeister_talkgroups.csv')

# Logging setup
global_logger = logging.getLogger()
global_logger.setLevel(logging.DEBUG)

file_handler = logging.FileHandler(LOGFILE)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(
    logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(
    logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
)

global_logger.addHandler(file_handler)
global_logger.addHandler(console_handler)

# Time zones
tz_utc = ZoneInfo("UTC")
tz_local = ZoneInfo("Europe/Berlin")

# State tracking
last_activity = {}          # {callsign: last_end_epoch}
active_transmissions = {}   # {callsign: start_timestamp_str}

async def download_talkgroup_list():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(TALKGROUP_URL) as resp:
                if resp.status == 200:
                    content = await resp.text()
                    with open(LOCAL_TG_FILE, 'w', encoding='utf-8') as f:
                        f.write(content)
                    global_logger.info("Talkgroup-Liste erfolgreich aktualisiert.")
                else:
                    global_logger.error(f"Fehler beim Download TG-Liste: HTTP {resp.status}")
    except Exception as e:
        global_logger.error(f"Exception beim Download TG-Liste: {e}")


def load_talkgroup_mapping():
    mapping = {}
    try:
        with open(LOCAL_TG_FILE, newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            global_logger.info(f"CSV-Felder: {reader.fieldnames}")
            for row in reader:
                tg = row.get('Talkgroup Number', '').strip()
                name = row.get('Talkgroup Name', '').strip()
                if tg and name:
                    mapping[tg] = name
            global_logger.info(f"Talkgroup-Liste geladen, {len(mapping)} Einträge.")
    except FileNotFoundError:
        global_logger.error(f"TG-Liste nicht gefunden in {LOCAL_TG_FILE}, bitte herunterladen!")
    except Exception as e:
        global_logger.error(f"Fehler beim Laden TG-Liste: {e}")
    return mapping

async def fetch_user_info(session, callsign):
    url = f'https://radioid.net/api/dmr/user/?callsign={callsign}'
    try:
        async with session.get(url, timeout=5) as resp:
            data = await resp.json()
            if data.get('count', 0) > 0:
                user = data['results'][0]
                return (
                    user.get('fname', 'Unbekannt'),
                    user.get('id', 'Unbekannt'),
                    user.get('city', 'Unbekannt'),
                    user.get('country', 'Unbekannt')
                )
    except Exception as e:
        global_logger.error(f"Fehler beim Abruf von Informationen für {callsign}: {e}")
    return ('Unbekannt', 'Unbekannt', 'Unbekannt', 'Unbekannt')

async def send_telegram_message(app, callsign, name, dmrid, city, country, start_ts, tg_mapping):
    dt = datetime.datetime.fromisoformat(start_ts).replace(tzinfo=tz_utc).astimezone(tz_local)
    date_str = dt.strftime("%d.%m.%Y %H:%M")
    tg_name = tg_mapping.get(str(TALKGROUP), f'TG {TALKGROUP}')
    message = (
        f"📅 {date_str}\n"
        f"📻 [{callsign}](https://www.qrz.com/lookup/{callsign})  👤 {name} ([{dmrid}](https://radioid.net/database/view?id={dmrid}))\n"
        f"📍 [{city} / {country}](https://www.openstreetmap.org/search?query={city}%2C+{country})\n"
        f"👥 [Talkgroup '{tg_name}' ({TALKGROUP})](https://hose.brandmeister.network/?subscribe={TALKGROUP})"
    )
    try:
        await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='Markdown')
        global_logger.info(f"Telegram-Nachricht gesendet für {callsign}")
    except Exception as e:
        global_logger.error(f"Fehler beim Senden der Telegram-Nachricht für {callsign}: {e}")

async def process_line(line, app, session, tg_mapping):
    m_start = re.match(r'^M: (\S+ \S+) Received data from (\S+)', line)
    m_end = re.match(r'^M: (\S+ \S+) Received end of transmission', line)
    if m_start:
        timestamp, callsign = m_start.groups()
        active_transmissions[callsign] = timestamp
        global_logger.debug(f"Start Transmission von {callsign} um {timestamp}")
    elif m_end:
        timestamp = m_end.group(1)
        end_epoch = datetime.datetime.fromisoformat(timestamp).timestamp()
        to_remove = []
        for callsign, start_ts in active_transmissions.items():
            start_epoch = datetime.datetime.fromisoformat(start_ts).timestamp()
            duration = end_epoch - start_epoch
            if duration >= MIN_DURATION:
                last = last_activity.get(callsign, 0)
                if end_epoch - last >= TIMER_DURATION:
                    name, dmrid, city, country = await fetch_user_info(session, callsign)
                    await send_telegram_message(app, callsign, name, dmrid, city, country, start_ts, tg_mapping)
                last_activity[callsign] = end_epoch
            to_remove.append(callsign)
        for c in to_remove:
            active_transmissions.pop(c, None)
        global_logger.debug(f"Ende Transmission um {timestamp}")

async def monitor_logfile(app, tg_mapping):
    async with aiohttp.ClientSession() as session:
        with open(LOGFILE, 'r') as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if not line:
                    await asyncio.sleep(0.5)
                    continue
                await process_line(line.strip(), app, session, tg_mapping)

async def main():
    await download_talkgroup_list()
    tg_mapping = load_talkgroup_mapping()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    await monitor_logfile(app, tg_mapping)

if __name__ == '__main__':
    asyncio.run(main())
