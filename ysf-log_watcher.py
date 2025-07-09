#!/usr/bin/env python3
import os
import sys
import re
import csv
import asyncio
import logging
import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import aiohttp
from telegram import Bot

# --- Setup ---

# Basisverzeichnis und .env laden
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

# Konstanten
LOGFILE = '/var/log/YSFReflector/YSFReflector.log'
LOCAL_TG_FILE = os.path.join(BASE_DIR, 'brandmeister_talkgroups.csv')

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
if not TELEGRAM_BOT_TOKEN:
    print("FEHLER: TELEGRAM_BOT_TOKEN fehlt!", file=sys.stderr)
    sys.exit(1)

TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '@TG264555')
MIN_DURATION    = int(os.environ.get('MIN_DURATION', 4))
TIMER_DURATION  = int(os.environ.get('TIMER_DURATION', 900))
TALKGROUP       = os.environ.get('TALKGROUP', '264555')
TALKGROUP_URL   = os.environ.get(
    'TALKGROUP_URL',
    'https://w0chp.radio/brandmeister-talkgroups/brandmeister-talkgroups.csv'
)

tz_utc   = ZoneInfo("UTC")
tz_local = ZoneInfo("Europe/Berlin")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOGFILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# State
last_activity = {}        # {callsign: last_end_epoch}
active_tx      = {}       # {callsign: start_iso_ts}

# --- Helfer ---

async def download_talkgroup_list():
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(TALKGROUP_URL) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    with open(LOCAL_TG_FILE, 'w', encoding='utf-8') as f:
                        f.write(text)
                    logger.info("Talkgroup-Liste aktualisiert.")
                else:
                    logger.error(f"TG-Download fehlgeschlagen: HTTP {resp.status}")
    except Exception as e:
        logger.error(f"Exception beim TG-Download: {e}")

def load_talkgroup_mapping():
    m = {}
    try:
        with open(LOCAL_TG_FILE, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                tg = row.get('Talkgroup Number','').strip()
                name = row.get('Talkgroup Name','').strip()
                if tg and name:
                    m[tg] = name
        logger.info(f"TG-Mapping geladen: {len(m)} Einträge")
    except FileNotFoundError:
        logger.error(f"TG-Datei nicht gefunden: {LOCAL_TG_FILE}")
    except Exception as e:
        logger.error(f"Fehler Laden TG-Datei: {e}")
    return m

async def fetch_user_info(session, callsign):
    url = f'https://radioid.net/api/dmr/user/?callsign={callsign}'
    try:
        async with session.get(url, timeout=5) as resp:
            data = await resp.json()
            if data.get('count',0) > 0:
                u = data['results'][0]
                return (
                    u.get('fname','Unbekannt'),
                    u.get('id','Unbekannt'),
                    u.get('city','Unbekannt'),
                    u.get('country','Unbekannt')
                )
    except Exception as e:
        logger.error(f"Fetch user {callsign} fehlgeschlagen: {e}")
    return ('Unbekannt','Unbekannt','Unbekannt','Unbekannt')

def format_local(ts_iso: str):
    dt_utc = datetime.datetime.fromisoformat(ts_iso).replace(tzinfo=tz_utc)
    dt_loc = dt_utc.astimezone(tz_local)
    return dt_loc.strftime("%d.%m.%Y"), dt_loc.strftime("%H:%M")

async def send_telegram(bot: Bot, callsign, name, dmrid, city, country, start_ts, tg_map):
    date_part, time_part = format_local(start_ts)
    tg_name = tg_map.get(TALKGROUP, f"TG {TALKGROUP}")
    msg = (
        f"📅 {date_part}  🕒 {time_part}\n"
        f"📻 [{callsign}](https://www.qrz.com/lookup/{callsign})  "
        f"👤 {name} ([{dmrid}](https://radioid.net/database/view?id={dmrid}))\n"
        f"📍 [{city} / {country}](https://www.openstreetmap.org/search?query="
        f"{city}%2C+{country})\n"
        f"👥 [Talkgroup '{tg_name}' ({TALKGROUP})]"
        f"(https://hose.brandmeister.network/?subscribe={TALKGROUP})"
    )
    try:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=msg,
            parse_mode='Markdown'
        )
        logger.info(f"Nachricht gesendet für {callsign}")
    except Exception as e:
        logger.error(f"Send Telegram failed for {callsign}: {e}")

# --- Hauptlogik ---

async def process_line(line, bot: Bot, session, tg_map):
    m1 = re.match(r'^M: (\S+ \S+) Received data from (\S+)', line)
    m2 = re.match(r'^M: (\S+ \S+) Received end of transmission', line)
    if m1:
        ts, cs = m1.groups()
        active_tx[cs] = ts
        logger.debug(f"Start {cs}@{ts}")
    elif m2:
        ts = m2.group(1)
        end_epoch = datetime.datetime.fromisoformat(ts).timestamp()
        to_del = []
        for cs, start_ts in active_tx.items():
            dur = end_epoch - datetime.datetime.fromisoformat(start_ts).timestamp()
            if dur >= MIN_DURATION:
                last = last_activity.get(cs, 0)
                if end_epoch - last >= TIMER_DURATION:
                    name, dmrid, city, country = await fetch_user_info(session, cs)
                    await send_telegram(bot, cs, name, dmrid, city, country, start_ts, tg_map)
                last_activity[cs] = end_epoch
            to_del.append(cs)
        for cs in to_del:
            active_tx.pop(cs, None)
        logger.debug(f"Ende Transmission um {ts}")

async def monitor_logfile(bot: Bot, tg_map):
    await download_talkgroup_list()
    tg_map = load_talkgroup_mapping()
    async with aiohttp.ClientSession() as session:
        with open(LOGFILE, 'r') as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if not line:
                    await asyncio.sleep(0.5)
                    continue
                await process_line(line.strip(), bot, session, tg_map)

async def main():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    await monitor_logfile(bot, {})

if __name__ == '__main__':
    asyncio.run(main())