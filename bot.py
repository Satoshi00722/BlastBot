from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import ReplyKeyboardMarkup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from cryptobot import create_invoice, get_invoice
from config import CRYPTOBOT_TOKEN
import os, json, asyncio, re
import time
import requests
import uuid

from telethon import TelegramClient
from config import ADMIN_CHANNEL_ID
from telethon.errors import SessionPasswordNeededError
from config import BOT_TOKEN, API_ID, API_HASH
from worker import spam_worker
import os, sys, time
print("=== BOT.PY STARTED ===", flush=True)
print("CWD:", os.getcwd(), flush=True)
print("FILES:", os.listdir("."), flush=True)
os.makedirs("users", exist_ok=True)
os.makedirs("payments", exist_ok=True)
# ======================
# TARIFFS
# ======================
TARIFFS = {
    "30": {
        "name": "30 дней",
        "days": 30,
        "max_accounts": 10,
        "price": 20
    },
    "90": {
        "name": "90 дней",
        "days": 90,
        "max_accounts": 50,
        "price": 35
    },
    "365": {
        "name": "365 дней",
        "days": 365,
        "max_accounts": 100,
        "price": 100
    }
}

# ======================
# INIT
# ======================
bot = Bot(BOT_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

workers = {}
pending_payments = {}
login_clients = {}

PHONE_RE = re.compile(r"^\+\d{10,15}$")

# ======================
# HELPERS
# ======================
def get_settings(uid):
    path = user_dir(uid)
    file = f"{path}/settings.json"
    if not os.path.exists(file):
        return None
    with open(file, "r") as f:
        return json.load(f)

def get_user_text(uid):
    path = user_dir(uid)
    file = f"{path}/message.txt"
    if not os.path.exists(file):
        return None
    with open(file, "r", encoding="utf-8") as f:
        return f.read()

def save_payment(user_id, data):
    os.makedirs("payments", exist_ok=True)
    with open(f"payments/{user_id}.json", "w") as f:
        json.dump(data, f)

def load_payment(user_id):
    path = f"payments/{user_id}.json"
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return json.load(f)

def delete_payment(user_id):
    path = f"payments/{user_id}.json"
    if os.path.exists(path):
        os.remove(path)

def user_dir(uid):
    path = f"users/user_{uid}"
    os.makedirs(f"{path}/sessions", exist_ok=True)
    return path

def get_sessions(uid):
    path = user_dir(uid)
    return [f for f in os.listdir(f"{path}/sessions") if f.endswith(".session")]

def get_accounts_info(uid):
    path = user_dir(uid)
    file = f"{path}/accounts.json"
    if not os.path.exists(file):
        return []
    with open(file, "r") as f:
        return json.load(f)

def get_tariff(uid):
        path = user_dir(uid)
        tf = f"{path}/tariff.json"

        # если тарифа нет — создаём FREE ОДИН РАЗ
        if not os.path.exists(tf):
            data = {
                "name": "FREE",
                "expires": int(time.time()) + 4 * 60 * 60,
                "max_accounts": 5
            }
            with open(tf, "w") as f:
                json.dump(data, f)
            return data

        # если есть — просто читаем
        with open(tf, "r") as f:
            return json.load(f)

def is_tariff_active(uid):
    tariff = get_tariff(uid)
    return tariff["expires"] and time.time() < tariff["expires"]

def activate_tariff(uid, tariff_key):
    tariff = TARIFFS[tariff_key]
    path = user_dir(uid)

    data = {
        "name": tariff["name"],
        "expires": int(time.time()) + tariff["days"] * 86400,
        "max_accounts": tariff["max_accounts"]
    }

    with open(f"{path}/tariff.json", "w") as f:
        json.dump(data, f)



def menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🔓 Подключить", "📝 Текст")
    kb.row("⚙️ Настройки", "👤 Личный кабинет")
    kb.row("💳 Тарифы")
    kb.row("📘 Для Новичка", "🛒 Купить аккаунты")
    kb.add("▶️ Начать работу")
    kb.add("⛔ Остановить")
    return kb

def back_kb():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("⬅️ Назад")
    return kb

async def reset_login(uid):
    client = login_clients.get(uid)
    if client:
        await client.disconnect()
        login_clients.pop(uid, None)

# ======================
# STATES
# ======================
class TextState(StatesGroup):
    waiting = State()

class PhoneState(StatesGroup):
    phone = State()
    code = State()
    password = State()

class SettingsFSM(StatesGroup):
    delay_groups = State()
    groups_count = State()
    delay_cycle = State()

# ======================
# START
# ======================
@dp.message_handler(commands=["start"], state="*")
async def start(msg: types.Message, state):
    await state.finish()

    user = msg.from_user
    username = f"@{user.username}" if user.username else "нет"

    # уведомление админу (оставляем)
    await bot.send_message(
        ADMIN_CHANNEL_ID,
        f"🚀 Новый старт бота\n\n"
        f"👤 User ID: {user.id}\n"
        f"👀 Username: {username}\n"
        f"📛 Имя: {user.first_name}"
    )

    text = (
        "👋 <b>Добро пожаловать в BlastBot</b>\n\n"
        "🚀 Telegram-сервис для автоматической рассылки сообщений\n"
        "в чаты и группы с нескольких аккаунтов.\n\n"
        "⚙️ <b>Возможности бота:</b>\n"
        "• рассылка в группы и чаты\n"
        "• работа с несколькими аккаунтами\n"
        "• гибкие настройки скорости и лимитов\n"
        "• защита от спам-блоков\n"
        "• удобный личный кабинет\n\n"
        "🎁 <b>Бесплатный тест — 5 часов</b>\n"
        "Попробуйте сервис без оплаты.\n\n"
        "⬇️ Выберите действие ниже"
    )

    with open("welcome.jpg", "rb") as photo:
        await bot.send_photo(
            chat_id=msg.chat.id,
            photo=photo,
            caption=text,
            parse_mode="HTML",
            reply_markup=menu()
        )

# ======================
# BACK
# ======================
@dp.message_handler(lambda m: m.text == "⬅️ Назад", state="*")
async def back(msg: types.Message, state):
    await reset_login(msg.from_user.id)
    await state.finish()
    await msg.answer("↩️ Возврат в меню", reply_markup=menu())

# ======================
# ПОЛЬЗОВАНИЕ
# ======================
@dp.message_handler(lambda m: m.text == "📘 Для Новичка", state="*")
async def usage(msg: types.Message, state):
    await state.finish()

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton(
            text="📖 Открыть инструкцию",
            url="https://telegra.ph/BlastBot--rukovodstvo-dlya-novichkov-01-27-2"
        )
    )

    await msg.answer(
        "📘 <b>Инструкция по использованию</b>\n\n"
        "Нажмите кнопку ниже, чтобы открыть полное руководство:",
        parse_mode="HTML",
        reply_markup=kb
    )

# ======================
# КУПИТЬ АККАУНТЫ
# ======================
@dp.message_handler(lambda m: m.text == "🛒 Купить аккаунты", state="*")
async def buy_accounts(msg: types.Message, state):
    await state.finish()

    text = (
        "💸 <b>Прайс Telegram аккаунтов</b> 💸\n\n"

        "🇪🇸 Испания — <b>500 грн</b>\n"
        "🇩🇪 Германия — <b>450 грн</b>\n"
        "🇬🇷 Греция — <b>300 грн</b>\n"
        "🇵🇱 Польша — <b>220 грн</b>\n"
        "🇰🇿 Казахстан — <b>270 грн</b>\n"
        "🇷🇴 Румыния — <b>290 грн</b>\n"
        "🇸🇰 Словакия — <b>280 грн</b>\n"
        "🇯🇵 Япония — <b>300 грн</b>\n"
        "🇲🇩 Молдова — <b>280 грн</b>\n"
        "🇺🇦 Украина — <b>250 грн</b>\n"
        "🇨🇳 Китай — <b>2100 грн</b>\n"
        "🇮🇱 Израиль — <b>240 грн</b>\n"
        "🇺🇸 USA (физ.) — <b>230 грн</b>\n"
        "🇹🇭 Таиланд — <b>200 грн</b>\n"
        "🇺🇿 Узбекистан — <b>210 грн</b>\n"
        "🏴 Англия — <b>210 грн</b>\n"
        "🇵🇭 Филиппины — <b>220 грн</b>\n"
        "🇹🇷 Турция — <b>190 грн</b>\n"
        "🇦🇷 Аргентина — <b>180 грн</b>\n"
        "🇮🇳 Индия — <b>180 грн</b>\n"
        "🇿🇦 Африка — <b>180 грн</b>\n"
        "🇻🇳 Вьетнам — <b>160 грн</b>\n"
        "🇵🇰 Пакистан — <b>150 грн</b>\n"
        "🇪🇬 Египет — <b>130 грн</b>\n"
        "🇨🇴 Колумбия — <b>130 грн</b>\n"
        "🇮🇩 Индонезия — <b>120 грн</b>\n"
        "🇨🇦 Канада — <b>120 грн</b>\n"
        "🇺🇸 Америка — <b>110 грн</b>\n\n"

        "⭐️ <b>Аккаунты с большой отлежкой</b> ⭐️\n\n"
        "🇺🇸 США 10 лет (2015) — <b>2300 грн</b>\n"
        "🇺🇸 США 11 лет (2014) — <b>12000 грн</b>\n"
        "🏁 Микс гео 12 лет (2013) — <b>23000 грн</b>\n\n"

        "🇺🇸 США 8 лет (2017) — <b>1800 грн</b>\n"
        "🇺🇸 США 7 лет (2018) — <b>1400 грн</b>\n"
        "🇺🇸 США 6 лет (2019) — <b>1000 грн</b>\n"
        "🇺🇸 США 5 лет (2020) — <b>800 грн</b>\n"
        "🇺🇸 США 2 года (2022) — <b>600 грн</b>\n\n"

        "🇮🇳 Индия 4 года — <b>1500 грн</b>\n"
        "🇮🇩 Индонезия 3 года — <b>1200 грн</b>\n"
        "🇵🇭 Филиппины 3 года — <b>1200 грн</b>\n"
        "🇨🇴 Колумбия 3 года — <b>700 грн</b>\n\n"

        "🛫 <b>Выдача:</b> в течение <b>10 минут</b> после оплаты\n\n"
        "✅ Без спам-блока и заморозки\n"
        "⚠️ Передача строго в одни руки\n\n"

        "🗂 Форматы выдачи:\n"
        "• номер + код\n"
        "• json + session\n"
        "• tdata\n\n"

        "💰 <b>Оплата:</b>\n"
        "🪙 Крипта (USDT, TON, BTC, ETH)\n"
        "💎 CryptoBot\n"
        "💳 Карта\n"
        "👛 PayPal\n"
        "🌟 Звёзды (x3)\n\n"

        "📲 <b>Покупка:</b> @illy228"
    )

    await msg.answer(text, parse_mode="HTML", reply_markup=menu())

# ======================
# АККАУНТЫ
# ======================
@dp.message_handler(lambda m: m.text == "🔓 Подключить", state="*")
async def add_account(msg: types.Message, state):
    if not is_tariff_active(msg.from_user.id):
        await msg.answer(
            "⛔ <b>Тестовый период закончился</b>\n\n"
            "💳 Купите тариф, чтобы добавлять аккаунты",
            parse_mode="HTML",
            reply_markup=menu()
        )
        return
    tariff = get_tariff(msg.from_user.id)
    accounts = get_sessions(msg.from_user.id)

    if len(accounts) >= tariff["max_accounts"]:
        await msg.answer(
            f"❌ Лимит аккаунтов достигнут\n\n"
            f"💳 Тариф: {tariff['name']}\n"
            f"🔢 Максимум: {tariff['max_accounts']} аккаунтов\n\n"
            f"👉 Обновите тариф",
            reply_markup=menu()
        )
        return
    await reset_login(msg.from_user.id)
    await state.finish()
    await msg.answer(
        "📱 Введи номер телефона: (+1)\nЖди код ",
        reply_markup=back_kb()
    )
    await PhoneState.phone.set()

@dp.message_handler(state=PhoneState.phone)
async def get_phone(msg: types.Message, state):
    if not PHONE_RE.match(msg.text.strip()):
        await msg.answer(
            "❌ Неверный формат номера\nПример: +1XXXXXXXX",
            reply_markup=back_kb()
        )
        return

    phone = msg.text.strip()
    path = user_dir(msg.from_user.id)

    client = TelegramClient(f"{path}/sessions/{phone}", API_ID, API_HASH)
    await client.connect()
    await client.send_code_request(phone)

    login_clients[msg.from_user.id] = client
    await state.update_data(phone=phone)

    await msg.answer(
        "🔐 Введи код из Telegram\n",
        reply_markup=back_kb()
    )
    await PhoneState.code.set()

@dp.message_handler(state=PhoneState.code)
async def get_code(msg: types.Message, state):
    if not msg.text.isdigit():
        await msg.answer("❌ Код должен быть числом", reply_markup=back_kb())
        return

    uid = msg.from_user.id
    data = await state.get_data()
    client = login_clients.get(uid)

    try:
        await client.sign_in(phone=data["phone"], code=msg.text)

        me = await client.get_me()

        accounts_file = f"{user_dir(uid)}/accounts.json"
        accounts = []

        if os.path.exists(accounts_file):
            with open(accounts_file, "r") as f:
                accounts = json.load(f)

        accounts.append({
            "phone": data["phone"],
            "username": me.username or "no_username"
        })

        with open(accounts_file, "w") as f:
            json.dump(accounts, f, indent=2)

        await msg.answer("✅ Аккаунт успешно добавлен", reply_markup=menu())
    except SessionPasswordNeededError:
        await msg.answer("🔑 Включена 2FA. Введи пароль", reply_markup=back_kb())
        await PhoneState.password.set()
        return
    except Exception as e:
        await msg.answer(f"❌ Ошибка входа: {e}", reply_markup=menu())

    await reset_login(uid)
    await state.finish()

@dp.message_handler(state=PhoneState.password)
async def get_password(msg: types.Message, state):
    uid = msg.from_user.id
    client = login_clients.get(uid)

    try:
        await client.sign_in(password=msg.text.strip())
        await msg.answer("✅ Аккаунт добавлен (2FA)", reply_markup=menu())
    except Exception as e:
        await msg.answer(f"❌ Ошибка 2FA: {e}", reply_markup=menu())

    await reset_login(uid)
    await state.finish()

# ======================
# ТЕКСТ
# ======================
@dp.message_handler(lambda m: m.text == "📝 Текст", state="*")
async def text(msg: types.Message, state):
    await state.finish()
    await msg.answer("✍️ Отправь текст рассылки", reply_markup=back_kb())
    await TextState.waiting.set()

@dp.message_handler(state=TextState.waiting)
async def save_text(msg: types.Message, state):
    path = user_dir(msg.from_user.id)
    with open(f"{path}/message.txt", "w", encoding="utf-8") as f:
        f.write(msg.text)

    await msg.answer("✅ Текст сохранён", reply_markup=menu())
    await state.finish()

# ======================
# НАСТРОЙКИ
# ======================
@dp.message_handler(lambda m: m.text == "⚙️ Настройки", state="*")
async def settings_start(msg: types.Message, state):
    await state.finish()
    await msg.answer(
        "⏱ Введите задержку между отправкой в группы (сек):",
        reply_markup=back_kb()
    )
    await SettingsFSM.delay_groups.set()

@dp.message_handler(state=SettingsFSM.delay_groups)
async def set_delay_groups(msg: types.Message, state):
    if not msg.text.isdigit():
        await msg.answer("❌ Нужно число", reply_markup=back_kb())
        return
    await state.update_data(delay_between_groups=int(msg.text))
    await msg.answer("👥 Сколько групп брать с одного аккаунта?", reply_markup=back_kb())
    await SettingsFSM.groups_count.set()

@dp.message_handler(state=SettingsFSM.groups_count)
async def set_groups(msg: types.Message, state):
    if not msg.text.isdigit():
        await msg.answer("❌ Нужно число", reply_markup=back_kb())
        return
    await state.update_data(groups_per_account=int(msg.text))
    await msg.answer("⏳ Задержка после всех аккаунтов (Минуты):", reply_markup=back_kb())
    await SettingsFSM.delay_cycle.set()

@dp.message_handler(state=SettingsFSM.delay_cycle)
async def set_cycle(msg: types.Message, state):
    if not msg.text.isdigit():
        await msg.answer("❌ Нужно число", reply_markup=back_kb())
        return

    data = await state.get_data()
    path = user_dir(msg.from_user.id)

    settings = {
        "delay_between_groups": data["delay_between_groups"],
        "groups_per_account": data["groups_per_account"],
        "delay_between_cycles": int(msg.text) * 60
    }

    with open(f"{path}/settings.json", "w") as f:
        json.dump(settings, f, indent=2)

    await msg.answer("✅ Настройки сохранены", reply_markup=menu())
    await state.finish()

# ======================
# ЛИЧНЫЙ КАБИНЕТ
# ======================
@dp.message_handler(lambda m: m.text == "👤 Личный кабинет", state="*")
async def cabinet(msg: types.Message, state):
    await state.finish()

    uid = msg.from_user.id
    accounts = get_accounts_info(uid)
    tariff = get_tariff(uid)
    text_msg = get_user_text(uid)
    settings = get_settings(uid)

    text = "👤 <b>Личный кабинет</b>\n\n"

    # АККАУНТЫ
    text += f"🔢 Аккаунтов подключено: <b>{len(accounts)}</b>\n"

    if not accounts:
        text += "❌ Аккаунты не подключены\n"
    else:
        text += "📱 Подключённые аккаунты:\n"
        for i, acc in enumerate(accounts, 1):
            phone = acc.get("phone", "-")
            username = acc.get("username", "-")
            text += f"• {i}. <b>{phone}</b> — @{username}\n"

    text += "\n"

    # ТАРИФ
    text += "💳 <b>Тариф:</b>\n"
    text += f"• План: <b>{tariff['name']}</b>\n"
    if tariff["expires"]:
        left = int((tariff["expires"] - time.time()) / 3600)
        text += f"• ⏳ Осталось: ~{left} ч.\n"
    text += "\n"

    # ТЕКСТ РАССЫЛКИ
    text += "📄 <b>Текст рассылки:</b>\n"
    if text_msg:
        preview = text_msg[:300]
        text += f"<code>{preview}</code>\n"
        if len(text_msg) > 300:
            text += "…\n"
    else:
        text += "❌ Текст не задан\n"
    text += "\n"

    # НАСТРОЙКИ
    text += "⚙️ <b>Настройки:</b>\n"
    if settings:
        text += (
            f"• ⏱ Задержка между группами: <b>{settings['delay_between_groups']} сек</b>\n"
            f"• 👥 Групп с аккаунта: <b>{settings['groups_per_account']}</b>\n"
            f"• 🔁 Пауза между циклами: <b>{settings['delay_between_cycles']//60} мин</b>\n"
        )
    else:
        text += "❌ Настройки не заданы\n"

    text += (
        "\n❌ Удаление аккаунта:\n"
        "<code>del 1</code> - удалить нужный аккаунт 1,2,3...\n"
        "<code>del all</code> - удалить все аккаунты полностью"
    )

    await msg.answer(text, parse_mode="HTML", reply_markup=menu())

@dp.message_handler(lambda m: m.text.lower() in ["del all", "del_all"], state="*")
async def delete_all_accounts(msg: types.Message, state):
    await state.finish()
    uid = msg.from_user.id
    path = user_dir(uid)

    # ⛔ останавливаем рассылку
    if uid in workers:
        workers[uid]["stop"] = True
        task = workers[uid].get("task")
        if task:
            task.cancel()
        workers.pop(uid, None)

    # 🧹 отключаем login client
    if uid in login_clients:
        try:
            await login_clients[uid].disconnect()
        except:
            pass
        login_clients.pop(uid, None)

    # 🧹 удаляем sessions
    sessions_path = f"{path}/sessions"
    if os.path.exists(sessions_path):
        for file in os.listdir(sessions_path):
            try:
                os.remove(os.path.join(sessions_path, file))
            except:
                pass

    # 🧹 удаляем accounts.json
    acc_file = f"{path}/accounts.json"
    if os.path.exists(acc_file):
        os.remove(acc_file)

    # 🧹 чистим telethon journal
    for file in os.listdir(path):
        if file.endswith(".session-journal"):
            try:
                os.remove(os.path.join(path, file))
            except:
                pass

    await msg.answer(
        "🧹 <b>Все аккаунты полностью удалены</b>\n\n"
        "✅ Session-файлы\n"
        "✅ accounts.json\n"
        "✅ кеш и память\n"
        "✅ активные задачи\n\n"
        "Можно подключать аккаунты заново 👌",
        parse_mode="HTML",
        reply_markup=menu()
    )

@dp.message_handler(
    lambda m: m.text.lower().startswith("del ")
    and len(m.text.split()) == 2
    and m.text.split()[1].isdigit(),
    state="*"
)
async def delete_account(msg: types.Message, state):
    await state.finish()

    idx = int(msg.text.split()[1]) - 1
    uid = msg.from_user.id
    path = user_dir(uid)

    accounts_file = f"{path}/accounts.json"
    sessions_path = f"{path}/sessions"

    if not os.path.exists(accounts_file):
        await msg.answer("❌ Нет аккаунтов")
        return

    with open(accounts_file, "r") as f:
        accounts = json.load(f)

    if idx < 0 or idx >= len(accounts):
        await msg.answer("❌ Неверный номер аккаунта")
        return

    phone = accounts[idx]["phone"]

    # удаляем session файлы
    for file in os.listdir(sessions_path):
        if file.startswith(phone):
            try:
                os.remove(os.path.join(sessions_path, file))
            except:
                pass

    # удаляем из accounts.json
    accounts.pop(idx)
    with open(accounts_file, "w") as f:
        json.dump(accounts, f, indent=2)

    # чистим логи
    if uid in workers and "logs" in workers[uid]:
        workers[uid]["logs"] = [
            l for l in workers[uid]["logs"]
            if l.get("phone") != phone
        ]

    if not accounts and uid in workers and "logs" in workers[uid]:
        workers[uid]["logs"].clear()

    await msg.answer("✅ Аккаунт полностью удалён", reply_markup=menu())

# ======================
# START / STOP WORK
# ======================
@dp.message_handler(lambda m: m.text == "▶️ Начать работу", state="*")
async def start_work(msg: types.Message, state):
    await state.finish()
    uid = msg.from_user.id

    if not is_tariff_active(uid):
        await msg.answer("⛔ Тариф не активен", reply_markup=menu())
        return

    path = user_dir(uid)

    if uid in workers and not workers[uid]["stop"]:
        await msg.answer("⚠️ Рассылка уже запущена", reply_markup=menu())
        return

    accounts = get_accounts_info(uid)
    if not accounts:
        await msg.answer("❌ Нет подключённых аккаунтов", reply_markup=menu())
        return
    if not os.path.exists(f"{path}/message.txt"):
        await msg.answer("❌ Нет текста", reply_markup=menu())
        return
    if not os.path.exists(f"{path}/settings.json"):
        await msg.answer("❌ Нет настроек", reply_markup=menu())
        return

    # 🧹 если уже был воркер — очищаем старые логи
    if uid in workers:
        # всегда чистый старт
        workers.pop(uid, None)

    stop_flag = {
        "stop": False,
        "logs": []  # чистый лог при новом старте
    }
    workers[uid] = stop_flag

    status = await msg.answer("🚀 Рассылка запущена\n📤 Отправлено: 0")

    async def progress(sent, errors, info=None):
        try:
            # 🧾 сохраняем лог (теперь dict)
            if isinstance(info, dict):
                phone = info.get("phone")

                # не дублируем один и тот же аккаунт
                if phone and phone not in [l["phone"] for l in workers[uid]["logs"]]:
                    workers[uid]["logs"].append(info)

            logs_text = ""
            if workers[uid]["logs"]:
                lines = []
                for i, log in enumerate(workers[uid]["logs"], 1):
                    emoji = {
                        "spam_block": "🚫 СПАМ-БЛОК",
                        "freeze": "❄️ ЗАМОРОЖЕН",
                        "dead": "❌ МЁРТВЫЙ",
                        "error": "⚠️ ОШИБКА"
                    }.get(log.get("reason"), "❓ ПРОБЛЕМА")

                    lines.append(f"{i}. {emoji} — <b>{log['phone']}</b>")

                logs_text = (
                        "\n\n🧾 <b>Проблемные аккаунты:</b>\n"
                        + "\n".join(lines) +
                        "\n\n<i>👉 Зайдите в личный кабинет и удалите замороженный аккаунт</i>"
                )

            text = (
                "🚀 <b>Рассылка запущена</b>\n"
                f"📤 Отправлено: <b>{sent}</b>\n"
                f"❌ Ошибки: <b>{errors}</b>"
                f"{logs_text}"
            )

            await status.edit_text(text, parse_mode="HTML")
        except Exception:
            pass

    task = asyncio.create_task(
        spam_worker(path, stop_flag, progress)
    )

    workers[uid]["task"] = task

@dp.message_handler(lambda m: m.text == "⛔ Остановить", state="*")
async def stop(msg: types.Message, state):
    await state.finish()
    uid = msg.from_user.id
    if uid in workers:
        workers[uid]["stop"] = True
        await msg.answer("⛔ Рассылка остановлена", reply_markup=menu())

# ======================
# ТАРИФЫ
# ======================
@dp.message_handler(lambda m: m.text == "💳 Тарифы")
async def tariffs(msg: types.Message):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🥉 30 дней — 20 USDT")
    kb.add("🥈 90 дней — 35 USDT")
    kb.add("🥇 365 дней — 100 USDT")
    kb.add("⬅️ Назад")

    await msg.answer(
        "💳 <b>ВЫБЕРИТЕ ТАРИФ</b>\n\n"
        "🥉 <b>30 ДНЕЙ</b>\n"
        "— до <b>10 аккаунтов</b>\n\n"
        "🥈 <b>90 ДНЕЙ</b>\n"
        "— до <b>50 аккаунтов</b>\n\n"
        "🥇 <b>365 ДНЕЙ</b>\n"
        "— до <b>100 аккаунтов</b>\n\n"
        "✅ <b>После оплаты тариф активируется автоматически</b>",
        parse_mode="HTML",
        reply_markup=kb
    )

@dp.message_handler(lambda m: "30 дней" in m.text)
async def buy_30(msg: types.Message):
    invoice = create_invoice(
        CRYPTOBOT_TOKEN,
        amount=20,
        description="Тариф 30 дней",
        payload=f"tariff_30_{msg.from_user.id}"
    )

    inv = invoice["result"]

    save_payment(msg.from_user.id, {
        "invoice_id": inv["invoice_id"],
        "tariff_key": "30"
    })

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("💸 Оплатить USDT", url=inv["pay_url"]),
        InlineKeyboardButton("🔄 Проверить оплату", callback_data="check_payment")
    )

    await msg.answer(
        "📦 Тариф 30 дней\n"
        "💰 Цена: 20 USDT\n\n"
        "1️⃣ Оплатите счёт\n"
        "2️⃣ Нажмите «Проверить оплату»",
        reply_markup=kb
    )
@dp.message_handler(lambda m: "90 дней" in m.text)
async def buy_90(msg: types.Message):
    invoice = create_invoice(
        CRYPTOBOT_TOKEN,
        amount=35,
        description="Тариф 90 дней",
        payload=f"tariff_90_{msg.from_user.id}"
    )

    inv = invoice["result"]

    save_payment(msg.from_user.id, {
        "invoice_id": inv["invoice_id"],
        "tariff_key": "90"
    })

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("💸 Оплатить USDT", url=inv["pay_url"]),
        InlineKeyboardButton("🔄 Проверить оплату", callback_data="check_payment")
    )

    await msg.answer(
        "📦 Тариф 90 дней\n"
        "💰 Цена: 35 USDT\n\n"
        "1️⃣ Оплатите счёт\n"
        "2️⃣ Нажмите «Проверить оплату»",
        reply_markup=kb
    )
@dp.message_handler(lambda m: "365 дней" in m.text)
async def buy_365(msg: types.Message):
    invoice = create_invoice(
        CRYPTOBOT_TOKEN,
        amount=100,
        description="Тариф 365 дней",
        payload=f"tariff_365_{msg.from_user.id}"
    )

    inv = invoice["result"]

    save_payment(msg.from_user.id, {
        "invoice_id": inv["invoice_id"],
        "tariff_key": "365"
    })

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("💸 Оплатить USDT", url=inv["pay_url"]),
        InlineKeyboardButton("🔄 Проверить оплату", callback_data="check_payment")
    )

    await msg.answer(
        "📦 Тариф 365 дней\n"
        "💰 Цена: 100 USDT\n\n"
        "1️⃣ Оплатите счёт\n"
        "2️⃣ Нажмите «Проверить оплату»",
        reply_markup=kb
    )
@dp.callback_query_handler(lambda c: c.data == "check_payment", state="*")
async def check_payment(call: types.CallbackQuery):
    await call.answer("Проверяю оплату...")

    uid = call.from_user.id
    data = load_payment(uid)

    if not data:
        await call.message.answer("❌ Оплаты нет. Счёт не найден.")
        return

    invoice_id = data["invoice_id"]
    tariff_key = data["tariff_key"]

    import requests

    url = "https://pay.crypt.bot/api/getInvoices"
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN
    }
    params = {
        "invoice_ids": invoice_id
    }

    resp = requests.get(url, headers=headers, params=params, timeout=10).json()

    if not resp.get("ok"):
        await call.message.answer("❌ Ошибка проверки оплаты.")
        return

    items = resp.get("result", {}).get("items", [])

    if not items:
        await call.message.answer("❌ Оплаты нет.")
        return

    invoice = items[0]

    if invoice["status"] != "paid":
        await call.message.answer("❌ Оплаты нет.")
        return

    # ✅ ТОЛЬКО ТУТ
    activate_tariff(uid, tariff_key)
    delete_payment(uid)

    await call.message.answer(
        "✅ Оплата получена.\n🎉 Тариф активирован."
    )
    await call.message.edit_reply_markup()

# ======================
# RUN
# ======================
if __name__ == "__main__":
    print("=== START POLLING ===", flush=True)
    try:
        executor.start_polling(dp, skip_updates=True)
    except Exception as e:
        import traceback
        print("FATAL ERROR:", e, flush=True)
        traceback.print_exc()
        time.sleep(60)
















