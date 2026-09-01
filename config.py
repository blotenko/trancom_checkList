# -*- coding: utf-8 -*-
import os
import json
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

for d in (DATA_DIR, PHOTOS_DIR, REPORTS_DIR):
    os.makedirs(d, exist_ok=True)


def dashboard_path(project_key: str) -> str:
    """Кожен проєкт має свій локальний dashboard.xlsx (потім вантажиться
    в корінь папки ЦЬОГО проєкту на OneDrive)."""
    safe_key = project_key or "default"
    return os.path.join(DATA_DIR, f"dashboard_{safe_key}.xlsx")

# --- Microsoft OneDrive (Graph API, app-only / client credentials) ---
MS_TENANT_ID = os.getenv("MS_TENANT_ID", "")
MS_CLIENT_ID = os.getenv("MS_CLIENT_ID", "")
MS_CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET", "")

# Посилання на спільну папку OneDrive/SharePoint одного проєкту (старий,
# однопроєктний спосіб налаштування — лишається для сумісності).
MS_SHARE_URL = os.getenv(
    "MS_SHARE_URL",
    "https://trancom9999-my.sharepoint.com/:f:/g/personal/vlad_b_trancom_org_ua/"
    "IgA8YPa4R3aSRaR7QXbNtF47AVkK_ZgtEv-q8ZN1EOlJGG8",
)

# --- Проєкти (масштабування на кілька об'єктів/клієнтів) ---
#
# PROJECTS_JSON у .env — список проєктів, кожен зі своєю папкою OneDrive:
#   PROJECTS_JSON=[{"key":"elementum","name":"Elementum September","share_url":"https://..."},
#                  {"key":"other","name":"Інший проєкт","share_url":"https://..."}]
#
# Щоб додати новий проєкт — просто додайте ще один об'єкт у цей список і
# перезапустіть бота (env var на Render можна редагувати без git push,
# сервіс сам перезапуститься). Водіям НЕ потрібно нічого перевстановлювати —
# новий проєкт просто з'явиться кнопкою при виборі проєкту для рейсу.
#
# Якщо PROJECTS_JSON не задано — автоматично створюється один проєкт
# "за замовчуванням" з MS_SHARE_URL (старий, однопроєктний режим; кнопка
# вибору проєкту водієві тоді взагалі не показується).
def _load_projects():
    raw = os.getenv("PROJECTS_JSON", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            out = []
            for p in parsed:
                if p.get("key") and p.get("name") and p.get("share_url"):
                    out.append({"key": p["key"], "name": p["name"], "share_url": p["share_url"]})
            if out:
                return out
        except Exception as e:
            print(f"⚠️ PROJECTS_JSON: помилка розбору ({e}), використовую MS_SHARE_URL як єдиний проєкт")
    if MS_SHARE_URL:
        return [{"key": "default", "name": "Основний проєкт", "share_url": MS_SHARE_URL}]
    return []


PROJECTS = _load_projects()
PROJECTS_BY_KEY = {p["key"]: p for p in PROJECTS}
MULTI_PROJECT = len(PROJECTS) > 1

# Якщо OneDrive ще не налаштований — бот все одно працює,
# просто файли лишаються тільки локально (в data/) до налаштування.
ONEDRIVE_ENABLED = bool(MS_TENANT_ID and MS_CLIENT_ID and MS_CLIENT_SECRET and PROJECTS)
