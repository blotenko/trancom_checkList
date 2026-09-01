# -*- coding: utf-8 -*-
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from checklists import STEPS_BY_ID, PAUSE_EVENT, EXTRA_STOP_EVENT, INCIDENT_LABEL


def _truncate(label, n=60):
    return label if len(label) <= n else label[: n - 3] + "..."


def optional_actions_row(kb: InlineKeyboardBuilder):
    kb.row(
        InlineKeyboardButton(text=PAUSE_EVENT["label"], callback_data="opt:pause"),
        InlineKeyboardButton(text=INCIDENT_LABEL, callback_data="opt:incident"),
        InlineKeyboardButton(text=EXTRA_STOP_EVENT["label"], callback_data="opt:extra_stop"),
    )


def phase_gate_keyboard(step_id: str):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="▶️ Розпочати цей етап", callback_data=f"begin:{step_id}"))
    return kb.as_markup()


def step_keyboard(step_id: str, checked_keys, all_done: bool):
    step = STEPS_BY_ID[step_id]
    kb = InlineKeyboardBuilder()

    for group in step["groups"]:
        done = all(k in checked_keys for k in group["items"])
        prefix = "✅" if done else ("📷" if group["photo"] else "⬜")
        label = _truncate(f"{prefix} {group['label']}")
        kb.row(InlineKeyboardButton(text=label, callback_data=f"chk:{step_id}:{group['key']}"))

    if all_done:
        kb.row(InlineKeyboardButton(text="➡️ Далі", callback_data=f"next:{step_id}"))
    else:
        kb.row(InlineKeyboardButton(text="🔒 Відмітьте всі пункти, щоб продовжити", callback_data="noop"))

    optional_actions_row(kb)
    return kb.as_markup()


def rules_ack_keyboard():
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="✅ Ознайомлений(а), почати рейс", callback_data="rules_ack"))
    return kb.as_markup()


def vehicle_decision_keyboard(tractor, trailer):
    label = tractor
    if trailer:
        label += f" / {trailer}"
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=f"🚛 Той самий: {label}", callback_data="veh:same"))
    kb.row(InlineKeyboardButton(text="✏️ Інший автомобіль", callback_data="veh:other"))
    return kb.as_markup()


def cargo_decision_keyboard(cargo, route):
    label = cargo if len(cargo) <= 40 else cargo[:37] + "..."
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=f"📦 Той самий вантаж: {label}", callback_data="cargo:same"))
    kb.row(InlineKeyboardButton(text="✏️ Інший вантаж", callback_data="cargo:other"))
    return kb.as_markup()


def route_decision_keyboard(route):
    label = route if len(route) <= 40 else route[:37] + "..."
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=f"🗺️ Той самий маршрут: {label}", callback_data="route:same"))
    kb.row(InlineKeyboardButton(text="✏️ Інший маршрут", callback_data="route:other"))
    return kb.as_markup()


def project_decision_keyboard(project_name: str):
    label = project_name if len(project_name) <= 40 else project_name[:37] + "..."
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=f"🏗️ Той самий проєкт: {label}", callback_data="proj:same"))
    kb.row(InlineKeyboardButton(text="🔀 Інший проєкт", callback_data="proj:other"))
    return kb.as_markup()


def project_list_keyboard(projects):
    kb = InlineKeyboardBuilder()
    for p in projects:
        kb.row(InlineKeyboardButton(text=f"🏗️ {p['name']}", callback_data=f"projsel:{p['key']}"))
    return kb.as_markup()


def main_menu_keyboard(has_active_trip: bool):
    kb = InlineKeyboardBuilder()
    if has_active_trip:
        kb.row(InlineKeyboardButton(text="▶️ Продовжити поточний рейс", callback_data="continue_trip"))
    else:
        kb.row(InlineKeyboardButton(text="🚀 Почати новий рейс", callback_data="new_trip"))
    return kb.as_markup()


def optional_event_keyboard(event_type, checked_keys):
    event = PAUSE_EVENT if event_type == "pause" else EXTRA_STOP_EVENT
    kb = InlineKeyboardBuilder()
    for item in event["items"]:
        key = item["key"]
        done = key in checked_keys
        prefix = "✅" if done else "⬜"
        kb.row(InlineKeyboardButton(text=_truncate(f"{prefix} {item['text']}"), callback_data=f"optchk:{key}"))
    kb.row(InlineKeyboardButton(text="✅ Завершити", callback_data=f"optdone:{event_type}"))
    return kb.as_markup()


def skip_photo_keyboard():
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="Без фото ➡️", callback_data="skip_photo"))
    return kb.as_markup()
