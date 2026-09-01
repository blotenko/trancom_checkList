# -*- coding: utf-8 -*-
"""
Інтеграція з OneDrive/SharePoint через Microsoft Graph (app-only, client
credentials) — на основі звичайного посилання "Поділитися" на папку
(MS_SHARE_URL в .env), а не UPN користувача.

Потрібні змінні оточення (див. .env.example):
    MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET, MS_SHARE_URL

Логіка дат: перед кожним завантаженням бот кладе файл у підпапку з
сьогоднішньою датою (YYYY-MM-DD) всередині вашої спільної папки. Якщо
такої підпапки ще немає — Graph API створює її автоматично (просте
завантаження через шлях створює всі відсутні проміжні папки, окремо
нічого створювати не потрібно).

Якщо OneDrive ще не налаштований — ONEDRIVE_ENABLED=False і всі функції
тихо пропускають завантаження (файли лишаються тільки локально в data/),
щоб бот залишався робочим без хмари під час першого тестування.
"""
import os
import base64
import logging
import time
from datetime import datetime

import msal
import requests
from openpyxl import Workbook, load_workbook

import config
import database as db

log = logging.getLogger(__name__)

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
DASHBOARD_HEADERS = [
    "Рейс №", "Водій", "Телефон", "Тягач", "Причіп", "Вантаж", "Маршрут",
    "Початок", "Завершення", "Статус", "Паузи", "Дод. зупинки", "ЧП", "Звіт (OneDrive)",
]

# Резолвимо посилання на папку один раз і кешуємо (drive_id, item_id) в пам'яті процесу
_base_folder_cache = None


def _get_token():
    app = msal.ConfidentialClientApplication(
        client_id=config.MS_CLIENT_ID,
        client_credential=config.MS_CLIENT_SECRET,
        authority=f"https://login.microsoftonline.com/{config.MS_TENANT_ID}",
    )
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        raise RuntimeError(f"Не вдалось отримати токен Graph API: {result.get('error_description')}")
    return result["access_token"]


def _encode_share_url(url: str) -> str:
    """Кодує звичайне посилання 'Поділитися' у формат, який приймає /shares/{id}."""
    b64 = base64.urlsafe_b64encode(url.encode("utf-8")).decode("utf-8")
    b64 = b64.rstrip("=")
    return "u!" + b64


def _resolve_base_folder(headers):
    """Повертає (drive_id, item_id) папки з MS_SHARE_URL. Кешується в пам'яті."""
    global _base_folder_cache
    if _base_folder_cache:
        return _base_folder_cache

    share_id = _encode_share_url(config.MS_SHARE_URL)
    url = f"{GRAPH_ROOT}/shares/{share_id}/driveItem"
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    item = resp.json()
    drive_id = item["parentReference"]["driveId"]
    item_id = item["id"]
    _base_folder_cache = (drive_id, item_id)
    log.info("OneDrive: цільову папку розпізнано (drive=%s, item=%s)", drive_id, item_id)
    return _base_folder_cache


def _today_folder_name() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def upload_file(local_path: str, remote_subpath: str) -> str | None:
    """
    Завантажує файл у {спільна папка}/{remote_subpath} на OneDrive.
    Проміжні папки (в т.ч. папка з датою) створюються автоматично, якщо
    їх ще немає — окремо створювати нічого не потрібно.
    Повертає webUrl файлу, або None якщо OneDrive не налаштований чи
    стався збій (файл лишається доступним локально — рейс не втрачається).

    На тимчасові помилки (423 Locked — файл на секунду зайнятий після
    створення/хтось відкрив у Word Online; 429 — перевищено ліміт запитів;
    503 — Graph тимчасово недоступний) робимо кілька повторних спроб із
    паузою, а не здаємось одразу.
    """
    if not config.ONEDRIVE_ENABLED:
        log.info("OneDrive не налаштований — файл %s лишається тільки локально", local_path)
        return None

    RETRYABLE_STATUSES = {423, 429, 503}
    MAX_ATTEMPTS = 4
    DELAYS = [3, 8, 20]  # секунди між спробами (зростаюча пауза)

    with open(local_path, "rb") as f:
        data = f.read()
    remote_subpath = remote_subpath.replace("//", "/").lstrip("/")

    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            token = _get_token()
            headers = {"Authorization": f"Bearer {token}"}
            drive_id, item_id = _resolve_base_folder(headers)

            url = f"{GRAPH_ROOT}/drives/{drive_id}/items/{item_id}:/{remote_subpath}:/content"
            resp = requests.put(url, headers=headers, data=data, timeout=60)

            if resp.status_code in RETRYABLE_STATUSES and attempt < MAX_ATTEMPTS:
                delay = DELAYS[attempt - 1]
                log.warning(
                    "OneDrive: тимчасова помилка %s для %s (спроба %d/%d), повтор через %ds",
                    resp.status_code, remote_subpath, attempt, MAX_ATTEMPTS, delay,
                )
                time.sleep(delay)
                continue

            resp.raise_for_status()
            return resp.json().get("webUrl")
        except Exception as e:
            last_error = e
            if attempt < MAX_ATTEMPTS:
                delay = DELAYS[attempt - 1]
                log.warning(
                    "OneDrive: помилка завантаження %s (спроба %d/%d): %s — повтор через %ds",
                    remote_subpath, attempt, MAX_ATTEMPTS, e, delay,
                )
                time.sleep(delay)

    log.error("Помилка завантаження на OneDrive (%s) після %d спроб: %s", local_path, MAX_ATTEMPTS, last_error)
    return None


def _ensure_local_dashboard():
    if not os.path.exists(config.DASHBOARD_PATH):
        wb = Workbook()
        ws = wb.active
        ws.title = "Рейси"
        ws.append(DASHBOARD_HEADERS)
        wb.save(config.DASHBOARD_PATH)


def update_dashboard_row(trip_id: int, onedrive_report_url: str | None):
    """Додає/оновлює рядок зведеної таблиці по рейсу і синхронізує з OneDrive.
    Дашборд лежить в корені спільної папки (не в підпапці з датою) —
    це один загальний звід по всіх рейсах."""
    _ensure_local_dashboard()

    trip = db.get_trip(trip_id)
    driver = db.get_driver(trip["driver_id"])
    incidents = db.get_incidents(trip_id)
    pauses = db.get_optional_events(trip_id, "pause")
    extra_stops = db.get_optional_events(trip_id, "extra_stop")

    wb = load_workbook(config.DASHBOARD_PATH)
    ws = wb["Рейси"]

    target_row = None
    for row in ws.iter_rows(min_row=2):
        if row[0].value == trip_id:
            target_row = row[0].row
            break

    values = [
        trip_id,
        driver["full_name"] if driver else "",
        driver["phone"] if driver and driver["phone"] else "",
        trip["tractor_number"] or "",
        trip["trailer_number"] or "",
        trip["cargo_description"] or "",
        trip["route"] or "",
        trip["started_at"] or "",
        trip["finished_at"] or "",
        "Завершено" if trip["status"] == "done" else trip["status"],
        len(pauses),
        len(extra_stops),
        len(incidents),
        onedrive_report_url or "",
    ]

    if target_row:
        for col, val in enumerate(values, start=1):
            ws.cell(row=target_row, column=col, value=val)
    else:
        ws.append(values)

    wb.save(config.DASHBOARD_PATH)
    return upload_file(config.DASHBOARD_PATH, "dashboard.xlsx")


def upload_trip_report(trip_id: int, report_path: str) -> str | None:
    """Кладе звіт у підпапку сьогоднішньої дати: {дата}/reports/reys_N.docx"""
    fname = os.path.basename(report_path)
    return upload_file(report_path, f"{_today_folder_name()}/reports/{fname}")


def upload_trip_photo(trip_id: int, photo_path: str) -> str | None:
    """Кладе фото у підпапку сьогоднішньої дати: {дата}/photos/reys_N/файл.jpg"""
    fname = os.path.basename(photo_path)
    return upload_file(photo_path, f"{_today_folder_name()}/photos/reys_{trip_id}/{fname}")
