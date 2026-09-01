# -*- coding: utf-8 -*-
import os
from dotenv import load_dotenv

load_dotenv()

# --- Telegram ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# ID чатів/користувачів-менеджерів, яким бот шле сповіщення та звіти.
# Формат в .env: MANAGER_CHAT_IDS=123456789,987654321
MANAGER_CHAT_IDS = [
    int(x) for x in os.getenv("MANAGER_CHAT_IDS", "").split(",") if x.strip()
]

# --- Локальні шляхи ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "trancom.db")
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
REPORTS_DIR = os.path.join(DATA_DIR, "reports")
DASHBOARD_PATH = os.path.join(DATA_DIR, "dashboard.xlsx")

for d in (DATA_DIR, PHOTOS_DIR, REPORTS_DIR):
    os.makedirs(d, exist_ok=True)

# --- Microsoft OneDrive (Graph API, app-only / client credentials) ---
MS_TENANT_ID = os.getenv("MS_TENANT_ID", "")
MS_CLIENT_ID = os.getenv("MS_CLIENT_ID", "")
MS_CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET", "")

# Посилання на спільну папку OneDrive/SharePoint, куди складати звіти.
# Просто вставте сюди звичайне посилання "Поділитися" на папку — бот сам
# визначить, в якому диску вона лежить, і щодня буде створювати підпапку
# з датою (YYYY-MM-DD), якщо її ще немає.
MS_SHARE_URL = os.getenv(
    "MS_SHARE_URL",
    "https://trancom9999-my.sharepoint.com/:f:/g/personal/vlad_b_trancom_org_ua/"
    "IgA8YPa4R3aSRaR7QXbNtF47AVkK_ZgtEv-q8ZN1EOlJGG8",
)

# Якщо OneDrive ще не налаштований — бот все одно працює,
# просто файли лишаються тільки локально (в data/) до налаштування.
ONEDRIVE_ENABLED = bool(MS_TENANT_ID and MS_CLIENT_ID and MS_CLIENT_SECRET and MS_SHARE_URL)
