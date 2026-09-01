# -*- coding: utf-8 -*-
"""
TRANCOM · Telegram-бот оперативних чек-листів водія.

Обов'язковий каркас рейсу: Завантаження → Старт → Стоп → Вивантаження.
У будь-який момент рейсу доступні необов'язкові дії, які можна повторювати:
☕ Пауза, 🚨 ЧП, ➕ Додаткова зупинка.

Машина і вантаж запам'ятовуються на водієві — наступного разу бот питає
"той самий / інший" замість повторного вводу всього наново.

Запуск:  python bot.py   (потрібен BOT_TOKEN у .env)
"""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from fsm_storage import SQLiteStorage
from aiogram.types import (
    Message, CallbackQuery, FSInputFile,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
)

import config
import database as db
import keyboards as kb
import reports
import onedrive
from checklists import (
    STEPS_BY_ID, PERMANENT_RULES,
    PAUSE_EVENT, EXTRA_STOP_EVENT, next_step_id,
    is_first_step_of_phase, PHASE_LABELS,
)
from states import Registration, NewTrip, Checklist, Incident, OptionalEvent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("trancom_bot")

router = Router()


# ---------------------------------------------------------------------------
# Допоміжні функції
# ---------------------------------------------------------------------------

def step_message_text(step_id: str) -> str:
    step = STEPS_BY_ID[step_id]
    return f"<b>{step['title']}</b>\n<i>{step['subtitle']}</i>\n\n⚠️ {step['warning']}"


async def show_step(target_message: Message, trip_id: int, step_id: str, edit: bool = False):
    checked = db.get_checked_keys(trip_id)
    step_keys = {i["key"] for i in STEPS_BY_ID[step_id]["items"]}
    all_done = step_keys.issubset(checked)
    markup = kb.step_keyboard(step_id, checked, all_done)
    text = step_message_text(step_id)
    if edit:
        try:
            await target_message.edit_text(text, reply_markup=markup)
            return
        except Exception:
            pass
    await target_message.answer(text, reply_markup=markup)


async def show_step_or_gate(target_message: Message, trip_id: int, step_id: str, edit: bool = False):
    """Якщо це перший крок НОВОЇ фази (Старт/Стоп/Вивантаження) і водій ще
    не відмітив жодного пункту — спершу показуємо коротке підтвердження
    "Розпочати етап", а не одразу довгий чек-лист. Це природна пауза для
    випадків, коли між фазами минають години (завантажились — і тільки
    за 4-5 годин виїзд)."""
    checked = db.get_checked_keys(trip_id)
    step = STEPS_BY_ID[step_id]
    step_keys = {i["key"] for i in step["items"]}
    started = bool(step_keys & checked)

    if is_first_step_of_phase(step_id) and not started:
        phase_label = PHASE_LABELS[step["phase"]]
        text = f"Наступний етап — <b>{phase_label}</b>.\nНатисніть, коли будете готові його розпочати."
        markup = kb.phase_gate_keyboard(step_id)
        if edit:
            try:
                await target_message.edit_text(text, reply_markup=markup)
                return
            except Exception:
                pass
        await target_message.answer(text, reply_markup=markup)
        return

    await show_step(target_message, trip_id, step_id, edit=edit)


async def notify_managers(bot: Bot, text: str, document_path: str | None = None):
    for chat_id in config.MANAGER_CHAT_IDS:
        try:
            await bot.send_message(chat_id, text)
            if document_path and os.path.exists(document_path):
                await bot.send_document(chat_id, FSInputFile(document_path))
        except Exception as e:
            log.error("Не вдалось надіслати менеджеру %s: %s", chat_id, e)


async def finalize_trip(bot: Bot, trip_id: int):
    db.mark_trip_finished(trip_id)  # спершу статус/дата — щоб звіт вже бачив фінальний стан
    report_path = reports.build_trip_report(trip_id)
    onedrive_url = onedrive.upload_trip_report(trip_id, report_path)
    db.set_trip_report(trip_id, report_path=report_path, onedrive_url=onedrive_url)
    onedrive.update_dashboard_row(trip_id, onedrive_url)

    trip = db.get_trip(trip_id)
    driver = db.get_driver(trip["driver_id"])
    incidents = db.get_incidents(trip_id)
    pauses = db.get_optional_events(trip_id, "pause")

    summary = (
        f"✅ Рейс №{trip_id} завершено.\n"
        f"Водій: {driver['full_name']}\n"
        f"Авто: {trip['tractor_number']}"
        + (f" / {trip['trailer_number']}" if trip["trailer_number"] else "")
        + f"\nВантаж: {trip['cargo_description'] or '—'}\n"
        + (f"☕ Пауз: {len(pauses)}\n" if pauses else "")
        + (f"⚠️ ЧП: {len(incidents)}\n" if incidents else "")
        + (f"OneDrive: {onedrive_url}" if onedrive_url else "Звіт додано файлом (OneDrive не налаштований)")
    )
    await notify_managers(bot, summary, document_path=report_path)


def permanent_rules_text() -> str:
    lines = [f"<b>{PERMANENT_RULES['title']}</b>", f"<i>{PERMANENT_RULES['subtitle']}</i>", ""]
    for r in PERMANENT_RULES["items"]:
        lines.append(f"• {r}")
    lines.append("")
    lines.append(f"<b>{PERMANENT_RULES['principle']}</b>")
    return "\n".join(lines)


async def show_main_menu(message: Message, driver_id: int):
    active_trip = db.get_active_trip_for_driver(driver_id)
    text = "Головне меню:"
    if active_trip:
        text = f"У вас є активний рейс №{active_trip['id']}. Продовжимо?"
    await message.answer(text, reply_markup=kb.main_menu_keyboard(bool(active_trip)))


# ---------------------------------------------------------------------------
# /start та реєстрація водія
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    driver = db.get_driver_by_tg(message.from_user.id)
    if not driver:
        await message.answer(
            "Вітаю в системі TRANCOM! 👋\n"
            "Це бот оперативних чек-листів водія.\n\n"
            "Як вас звати? (Прізвище Ім'я)"
        )
        await state.set_state(Registration.waiting_name)
        return
    await show_main_menu(message, driver["id"])


@router.message(Registration.waiting_name)
async def reg_got_name(message: Message, state: FSMContext):
    full_name = message.text.strip()
    if len(full_name) < 3:
        await message.answer("Введіть, будь ласка, повне ім'я (мінімум 3 символи).")
        return
    await state.update_data(full_name=full_name)
    phone_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Надіслати номер телефону", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )
    await message.answer("Дякую! Надішліть, будь ласка, номер телефону.", reply_markup=phone_kb)
    await state.set_state(Registration.waiting_phone)


@router.message(Registration.waiting_phone, F.contact)
async def reg_got_phone(message: Message, state: FSMContext):
    data = await state.get_data()
    driver_id = db.create_driver(message.from_user.id, data["full_name"], message.contact.phone_number)
    await state.clear()
    await message.answer("Реєстрацію завершено ✅", reply_markup=ReplyKeyboardRemove())
    await show_main_menu(message, driver_id)


@router.message(Registration.waiting_phone)
async def reg_phone_text_fallback(message: Message, state: FSMContext):
    data = await state.get_data()
    driver_id = db.create_driver(message.from_user.id, data["full_name"], message.text.strip())
    await state.clear()
    await message.answer("Реєстрацію завершено ✅", reply_markup=ReplyKeyboardRemove())
    await show_main_menu(message, driver_id)


# ---------------------------------------------------------------------------
# Новий рейс: авто (той самий/інший) → вантаж (той самий/інший) → старт
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "new_trip")
async def cb_new_trip(call: CallbackQuery, state: FSMContext):
    driver = db.get_driver_by_tg(call.from_user.id)
    if driver["last_tractor"]:
        await call.message.edit_text(
            "Авто таке ж, як минулого разу?",
            reply_markup=kb.vehicle_decision_keyboard(driver["last_tractor"], driver["last_trailer"]),
        )
        await state.set_state(NewTrip.waiting_vehicle_decision)
    else:
        await call.message.edit_text("Введіть номер тягача:")
        await state.set_state(NewTrip.waiting_new_tractor)
    await call.answer()


@router.callback_query(F.data == "continue_trip")
async def cb_continue_trip(call: CallbackQuery, state: FSMContext):
    driver = db.get_driver_by_tg(call.from_user.id)
    trip = db.get_active_trip_for_driver(driver["id"])
    if not trip:
        await call.answer("Активного рейсу не знайдено.", show_alert=True)
        return
    if not trip["rules_ack_at"]:
        await call.message.edit_text(permanent_rules_text(), reply_markup=kb.rules_ack_keyboard())
    else:
        await show_step_or_gate(call.message, trip["id"], trip["current_step"], edit=True)
    await call.answer()


@router.callback_query(NewTrip.waiting_vehicle_decision, F.data.startswith("veh:"))
async def cb_vehicle_decision(call: CallbackQuery, state: FSMContext):
    driver = db.get_driver_by_tg(call.from_user.id)
    choice = call.data.split(":", 1)[1]
    if choice == "same":
        await state.update_data(tractor=driver["last_tractor"], trailer=driver["last_trailer"])
        await proceed_to_cargo_step(call.message, state, driver)
    else:
        await call.message.edit_text("Введіть номер тягача:")
        await state.set_state(NewTrip.waiting_new_tractor)
    await call.answer()


@router.message(NewTrip.waiting_new_tractor)
async def new_tractor(message: Message, state: FSMContext):
    await state.update_data(tractor=message.text.strip())
    await message.answer("Введіть номер причепа/платформи (або «-», якщо немає):")
    await state.set_state(NewTrip.waiting_new_trailer)


@router.message(NewTrip.waiting_new_trailer)
async def new_trailer(message: Message, state: FSMContext):
    trailer = message.text.strip()
    trailer = None if trailer == "-" else trailer
    await state.update_data(trailer=trailer)
    driver = db.get_driver_by_tg(message.from_user.id)
    await proceed_to_cargo_step(message, state, driver)


async def proceed_to_cargo_step(target: Message, state: FSMContext, driver):
    if driver["last_cargo"]:
        await target.answer(
            "Вантаж такий самий, як минулого разу?",
            reply_markup=kb.cargo_decision_keyboard(driver["last_cargo"], driver["last_route"] or ""),
        )
        await state.set_state(NewTrip.waiting_cargo_decision)
    else:
        await target.answer("Опишіть вантаж (тип, вага/габарити, особливості):")
        await state.set_state(NewTrip.waiting_new_cargo)


@router.callback_query(NewTrip.waiting_cargo_decision, F.data.startswith("cargo:"))
async def cb_cargo_decision(call: CallbackQuery, state: FSMContext):
    driver = db.get_driver_by_tg(call.from_user.id)
    choice = call.data.split(":", 1)[1]
    if choice == "same":
        await state.update_data(cargo=driver["last_cargo"], route=driver["last_route"] or "")
        await finalize_new_trip(call.message, call.from_user.id, state)
    else:
        await call.message.edit_text("Опишіть вантаж (тип, вага/габарити, особливості):")
        await state.set_state(NewTrip.waiting_new_cargo)
    await call.answer()


@router.message(NewTrip.waiting_new_cargo)
async def new_cargo(message: Message, state: FSMContext):
    await state.update_data(cargo=message.text.strip())
    await message.answer("Вкажіть маршрут (звідки-куди), або «-» якщо ще невідомо:")
    await state.set_state(NewTrip.waiting_route)


@router.message(NewTrip.waiting_route)
async def got_route(message: Message, state: FSMContext):
    route = message.text.strip()
    route = "" if route == "-" else route
    await state.update_data(route=route)
    await finalize_new_trip(message, message.from_user.id, state)


async def finalize_new_trip(target: Message, telegram_user_id: int, state: FSMContext):
    """Створює рейс і показує екран постійних правил. `target` — будь-яке
    Message, в чат якого можна відповісти (з callback чи зі звичайного повідомлення)."""
    data = await state.get_data()
    driver = db.get_driver_by_tg(telegram_user_id)

    tractor = data.get("tractor")
    trailer = data.get("trailer")
    cargo = data.get("cargo")
    route = data.get("route", "")

    trip_id = db.create_trip(driver["id"], tractor, trailer, cargo, route)
    db.update_driver_last_vehicle_cargo(driver["id"], tractor, trailer, cargo, route)
    await state.clear()

    await notify_managers(
        target.bot,
        f"🚀 Новий рейс №{trip_id}\nВодій: {driver['full_name']}\n"
        f"Авто: {tractor}" + (f" / {trailer}" if trailer else "")
        + f"\nВантаж: {cargo}\nМаршрут: {route or '—'}",
    )

    await target.answer(permanent_rules_text(), reply_markup=kb.rules_ack_keyboard())


@router.callback_query(F.data == "rules_ack")
async def cb_rules_ack(call: CallbackQuery, state: FSMContext):
    driver = db.get_driver_by_tg(call.from_user.id)
    trip = db.get_active_trip_for_driver(driver["id"])
    if not trip:
        await call.answer("Рейс не знайдено, почніть заново через /start", show_alert=True)
        return
    db.ack_rules(trip["id"])
    await state.clear()
    await call.message.edit_text("Рейс розпочато! Йдемо по чек-листу крок за кроком. 👇")
    await show_step(call.message, trip["id"], trip["current_step"])  # loading_1 — гейт тут зайвий, старт уже підтверджено
    await call.answer()


# ---------------------------------------------------------------------------
# Обов'язковий чек-лист: перемикання пунктів, фото, перехід далі
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery):
    await call.answer("Спочатку відмітьте всі пункти етапу.", show_alert=False)


@router.callback_query(F.data.startswith("chk:"))
async def cb_toggle_item(call: CallbackQuery, state: FSMContext):
    _, step_id, group_key = call.data.split(":", 2)
    driver = db.get_driver_by_tg(call.from_user.id)
    trip = db.get_active_trip_for_driver(driver["id"])
    if not trip:
        await call.answer("Рейс не знайдено.", show_alert=True)
        return

    group = next(g for g in STEPS_BY_ID[step_id]["groups"] if g["key"] == group_key)
    checked = db.get_checked_keys(trip["id"])
    already_done = all(k in checked for k in group["items"])

    if group["photo"] and not already_done:
        item_key = group["items"][0]  # фото-групи завжди містять рівно один пункт
        await state.set_state(Checklist.waiting_photo_for_item)
        await state.update_data(trip_id=trip["id"], step_id=step_id, item_key=item_key)
        await call.answer()
        await call.message.answer(f"📷 Надішліть, будь ласка, фото:\n«{group['label']}»")
        return

    db.toggle_group(trip["id"], step_id, group["items"])
    await show_step(call.message, trip["id"], step_id, edit=True)
    await call.answer()


@router.message(Checklist.waiting_photo_for_item, F.photo)
async def got_checklist_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    trip_id, step_id, item_key = data["trip_id"], data["step_id"], data["item_key"]

    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    local_path = os.path.join(config.PHOTOS_DIR, f"reys{trip_id}_{item_key}_{photo.file_unique_id}.jpg")
    await message.bot.download_file(file.file_path, destination=local_path)

    onedrive_url = onedrive.upload_trip_photo(trip_id, local_path)
    db.add_photo(trip_id, step_id, item_key, local_path, kind="checklist", onedrive_url=onedrive_url)
    db.mark_item_checked(trip_id, step_id, item_key)

    await state.clear()
    await message.answer("Фото збережено ✅")
    await show_step(message, trip_id, step_id)


@router.message(Checklist.waiting_photo_for_item)
async def checklist_photo_missing(message: Message):
    await message.answer("Потрібне саме фото 📷 — надішліть, будь ласка, знімок.")


@router.callback_query(F.data.startswith("next:"))
async def cb_next_step(call: CallbackQuery):
    step_id = call.data.split(":", 1)[1]
    driver = db.get_driver_by_tg(call.from_user.id)
    trip = db.get_active_trip_for_driver(driver["id"])
    if not trip:
        await call.answer("Рейс не знайдено.", show_alert=True)
        return

    db.complete_step(trip["id"], step_id)
    nxt = next_step_id(step_id)

    if nxt:
        db.set_trip_step(trip["id"], nxt)
        await call.message.edit_text(f"«{STEPS_BY_ID[step_id]['title']}» завершено ✅")
        await show_step_or_gate(call.message, trip["id"], nxt)
    else:
        await call.message.edit_text("Останній етап завершено ✅\nФормую підсумковий звіт і надсилаю менеджеру...")
        await finalize_trip(call.bot, trip["id"])
        await call.message.answer(
            "🎉 Рейс повністю завершено. Дякую за чітку роботу за чек-листом!\n\n"
            "Щоб почати новий рейс — натисніть кнопку нижче.",
            reply_markup=kb.main_menu_keyboard(False),
        )
    await call.answer()


@router.callback_query(F.data.startswith("begin:"))
async def cb_begin_phase(call: CallbackQuery):
    """Водій натиснув 'Розпочати цей етап' на екрані-паузі між фазами."""
    step_id = call.data.split(":", 1)[1]
    driver = db.get_driver_by_tg(call.from_user.id)
    trip = db.get_active_trip_for_driver(driver["id"])
    if not trip:
        await call.answer("Рейс не знайдено.", show_alert=True)
        return
    await show_step(call.message, trip["id"], step_id, edit=True)
    await call.answer()


# ---------------------------------------------------------------------------
# Необов'язкові дії: Пауза / ЧП / Додаткова зупинка (доступні завжди, повторювані)
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("opt:"))
async def cb_optional_start(call: CallbackQuery, state: FSMContext):
    event_type = call.data.split(":", 1)[1]
    driver = db.get_driver_by_tg(call.from_user.id)
    trip = db.get_active_trip_for_driver(driver["id"])
    if not trip:
        await call.answer("Рейс не знайдено.", show_alert=True)
        return

    if event_type == "incident":
        await state.set_state(Incident.waiting_description)
        await state.update_data(trip_id=trip["id"], context_step=trip["current_step"])
        await call.message.answer("🚨 Опишіть коротко, що сталося (пошкодження, несправність, ДТП тощо):")
        await call.answer()
        return

    # pause / extra_stop — показуємо короткий чек-лист, який можна проходити повторно
    await state.set_state(OptionalEvent.filling)
    await state.update_data(
        trip_id=trip["id"], context_step=trip["current_step"], event_type=event_type, checked=[]
    )
    event = PAUSE_EVENT if event_type == "pause" else EXTRA_STOP_EVENT
    await call.message.answer(
        f"<b>{event['title']}</b>\n<i>{event['subtitle']}</i>",
        reply_markup=kb.optional_event_keyboard(event_type, set()),
    )
    await call.answer()


@router.callback_query(OptionalEvent.filling, F.data.startswith("optchk:"))
async def cb_optional_toggle(call: CallbackQuery, state: FSMContext):
    key = call.data.split(":", 1)[1]
    data = await state.get_data()
    checked = set(data.get("checked", []))
    if key in checked:
        checked.discard(key)
    else:
        checked.add(key)
    await state.update_data(checked=list(checked))
    try:
        await call.message.edit_reply_markup(reply_markup=kb.optional_event_keyboard(data["event_type"], checked))
    except Exception:
        pass
    await call.answer()


@router.callback_query(OptionalEvent.filling, F.data.startswith("optdone:"))
async def cb_optional_done(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("Коментар/причина? (Напишіть текстом, або «-» якщо не потрібно)")
    await state.set_state(OptionalEvent.waiting_note)
    await call.answer()


@router.message(OptionalEvent.waiting_note)
async def optional_got_note(message: Message, state: FSMContext):
    note = message.text.strip()
    note = "" if note == "-" else note
    await state.update_data(note=note)
    await message.answer("Якщо є фото — надішліть зараз. Якщо немає, напишіть «-».")
    await state.set_state(OptionalEvent.waiting_photo)


@router.message(OptionalEvent.waiting_photo, F.photo)
async def optional_got_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    local_path = None
    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    local_path = os.path.join(
        config.PHOTOS_DIR, f"{data['event_type']}_reys{data['trip_id']}_{photo.file_unique_id}.jpg"
    )
    await message.bot.download_file(file.file_path, destination=local_path)
    onedrive_url = onedrive.upload_trip_photo(data["trip_id"], local_path)
    db.add_photo(data["trip_id"], data["context_step"], None, local_path, kind=data["event_type"], onedrive_url=onedrive_url)
    await finish_optional_event(message, state, data, photo_path=local_path)


@router.message(OptionalEvent.waiting_photo)
async def optional_no_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    await finish_optional_event(message, state, data, photo_path=None)


async def finish_optional_event(message: Message, state: FSMContext, data: dict, photo_path):
    event = PAUSE_EVENT if data["event_type"] == "pause" else EXTRA_STOP_EVENT
    checked_keys = set(data.get("checked", []))
    checked_texts = [i["text"] for i in event["items"] if i["key"] in checked_keys]

    db.add_optional_event(
        data["trip_id"], data["event_type"], data["context_step"], data.get("note", ""), checked_texts
    )

    driver = db.get_driver_by_tg(message.from_user.id)
    trip = db.get_trip(data["trip_id"])
    label = event["label"]
    text = (
        f"{label} — рейс №{data['trip_id']}\n"
        f"Водій: {driver['full_name']}\n"
        f"Авто: {trip['tractor_number']}"
        + (f" / {trip['trailer_number']}" if trip["trailer_number"] else "")
    )
    if data.get("note"):
        text += f"\nКоментар: {data['note']}"
    await notify_managers(message.bot, text, document_path=photo_path)

    await state.clear()
    await message.answer("Записав ✅ Продовжуємо чек-лист 👇")
    await show_step(message, data["trip_id"], trip["current_step"])


# ---------------------------------------------------------------------------
# ЧП (інцидент) — опис + необов'язкове фото
# ---------------------------------------------------------------------------

@router.message(Incident.waiting_description)
async def incident_got_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await message.answer("Якщо є фото — надішліть зараз. Якщо немає, напишіть «-».")
    await state.set_state(Incident.waiting_photo)


@router.message(Incident.waiting_photo, F.photo)
async def incident_got_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    trip_id, context_step, description = data["trip_id"], data["context_step"], data["description"]

    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    local_path = os.path.join(config.PHOTOS_DIR, f"incident_reys{trip_id}_{photo.file_unique_id}.jpg")
    await message.bot.download_file(file.file_path, destination=local_path)
    onedrive_url = onedrive.upload_trip_photo(trip_id, local_path)
    db.add_photo(trip_id, context_step, None, local_path, kind="incident", onedrive_url=onedrive_url)

    await finish_incident(message, trip_id, context_step, description, local_path)
    await state.clear()


@router.message(Incident.waiting_photo)
async def incident_no_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    trip_id, context_step, description = data["trip_id"], data["context_step"], data["description"]
    await finish_incident(message, trip_id, context_step, description, None)
    await state.clear()


async def finish_incident(message: Message, trip_id: int, context_step: str, description: str, photo_path):
    db.add_incident(trip_id, context_step, description)
    driver = db.get_driver_by_tg(message.from_user.id)
    trip = db.get_trip(trip_id)
    text = (
        f"🚨 ЧП — рейс №{trip_id}\n"
        f"Водій: {driver['full_name']}\n"
        f"Авто: {trip['tractor_number']}"
        + (f" / {trip['trailer_number']}" if trip["trailer_number"] else "")
        + f"\nОпис: {description}"
    )
    await notify_managers(message.bot, text, document_path=photo_path)
    await message.answer("Дякую, менеджера повідомлено. Продовжуємо чек-лист 👇")
    await show_step(message, trip_id, trip["current_step"])


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------

async def main():
    if not config.BOT_TOKEN:
        raise SystemExit("Не задано BOT_TOKEN у .env — див. README.md")

    db.init_db()
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=SQLiteStorage())
    dp.include_router(router)

    log.info("Бот запущено. OneDrive: %s", "увімкнено" if config.ONEDRIVE_ENABLED else "вимкнено (тільки локально)")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
