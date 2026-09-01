# -*- coding: utf-8 -*-
from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    waiting_name = State()
    waiting_phone = State()


class NewTrip(StatesGroup):
    waiting_vehicle_decision = State()   # "той самий" / "інший"
    waiting_new_tractor = State()
    waiting_new_trailer = State()
    waiting_cargo_decision = State()     # "той самий вантаж" / "інший"
    waiting_new_cargo = State()
    waiting_route_decision = State()     # "той самий маршрут" / "інший"
    waiting_route = State()


class Checklist(StatesGroup):
    waiting_photo_for_item = State()


class Incident(StatesGroup):
    waiting_description = State()
    waiting_photo = State()


class OptionalEvent(StatesGroup):
    filling = State()          # чекбокси паузи в пам'яті (FSM), не в БД
    waiting_note = State()     # причина додаткової зупинки
    waiting_photo = State()    # необов'язкове фото до події


class AdminReset(StatesGroup):
    waiting_confirm = State()
