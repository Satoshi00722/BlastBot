from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import ReplyKeyboardMarkup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from cryptobot import create_invoice, get_invoice
from config import CRYPTOBOT_TOKEN, ADMIN_CHANNEL_ID, BOT_TOKEN, API_ID, API_HASH
from worker import spam_worker
from referral import referral_system
import os, json, asyncio, re
import time
import requests
import uuid

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

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
# CUSTOM TELEGRAM CLIENT CONFIGURATION
# ======================
def create_custom_telegram_client(session_file):
    """Создает кастомизированный TelegramClient с параметрами Android-устройства"""
    return TelegramClient(
        session_file,
        API_ID,
        API_HASH,
        device_model="Samsung Galaxy S21",
        system_version="Android 13",
        app_version="9.6.3",
        lang_code="ru",
        system_lang_code="ru"
    )

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
    file = f"{path}/message.json"

    if not os.path.exists(file):
        return None

    with open(file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if data["type"] == "forward":
        return "✨ Пересланное сообщение\n(Premium-стикеры сохранятся)"

    return data.get("text", "")

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

    if not os.path.exists(tf):
        data = {
            "name": "FREE",
            "expires": int(time.time()) + 24 * 60 * 60,
            "max_accounts": 5
        }
        with open(tf, "w") as f:
            json.dump(data, f)
        return data

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
    kb.row("💳 Тарифы", "📊 Реферальная программа")
    kb.row("📘 Для Новичка", "📢 Канал | Отзывы")
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
# ФОНТАЯ ПРОВЕРКА ТРИАЛОВ И РЕФЕРАЛОВ
# ======================
async def check_referrals_periodically():
    """Периодическая проверка завершения триалов и начисления рефералов"""
    while True:
        try:
            await asyncio.sleep(300)  # Проверяем каждые 5 минут
            
            # Получаем список всех пользователей
            users_dir = "users"
            if not os.path.exists(users_dir):
                continue
                
            for folder in os.listdir(users_dir):
                if not folder.startswith("user_"):
                    continue
                    
                try:
                    user_id = int(folder[5:])  # Извлекаем ID из "user_123"
                    
                    # Проверяем завершение триала
                    if referral_system.check_trial_completion(user_id):
                        user_data = referral_system.get_user_data(user_id)
                        
                        # Если триал завершен и есть реферер
                        if user_data and user_data.referrer_id:
                            # Засчитываем реферала
                            if referral_system.add_referral(user_data.referrer_id, user_id):
                                # Отправляем уведомление рефереру
                                try:
                                    referrer_data = referral_system.get_user_data(user_data.referrer_id)
                                    await bot.send_message(
                                        user_data.referrer_id,
                                        f"🎉 <b>Новый засчитанный реферал!</b>\n\n"
                                        f"👤 Пользователь <code>{user_id}</code> выполнил все условия.\n"
                                        f"📊 Теперь у вас: <b>{referrer_data.referrals_count}/3</b> рефералов\n\n"
                                        f"{'🏆 <b>Поздравляем!</b> Вы достигли цели! 🎁 Вам доступна скидка <b>50%</b> на любой тариф!' if referrer_data.referrals_count >= 3 else f'🎯 Осталось пригласить: <b>{3 - referrer_data.referrals_count}</b> человек для получения скидки'}",
                                        parse_mode="HTML"
                                    )
                                except Exception as e:
                                    print(f"Ошибка отправки уведомления рефереру {user_data.referrer_id}: {e}")
                                
                                # Отправляем уведомление пользователю
                                try:
                                    await bot.send_message(
                                        user_id,
                                        "🎉 <b>Поздравляем!</b>\n\n"
                                        "✅ Вы успешно завершили 24-часовой триал!\n"
                                        "🎯 Ваш реферал засчитан пригласившему вас пользователю.\n\n"
                                        "💡 <i>Хотите продолжить? Выберите подходящий тариф!</i>",
                                        parse_mode="HTML"
                                    )
                                except Exception as e:
                                    print(f"Ошибка отправки уведомления пользователю {user_id}: {e}")
                                    
                except (ValueError, TypeError) as e:
                    print(f"Ошибка обработки пользователя {folder}: {e}")
                    continue
                    
        except Exception as e:
            print(f"Ошибка в check_referrals_periodically: {e}")
            await asyncio.sleep(60)

# ======================
# START (ОБНОВЛЕНО ДЛЯ РЕФЕРАЛОВ)
# ======================
@dp.message_handler(commands=["start"], state="*")
async def start(msg: types.Message, state):
    await state.finish()
    
    user = msg.from_user
    username = f"@{user.username}" if user.username else "нет"
    
    # Извлекаем реферальный ID из команды /start
    referrer_id = None
    args = msg.get_args()
    if args and args.startswith("ref_"):
        try:
            referrer_id = int(args[4:])
            
            # Проверяем, что пользователь не приглашает сам себя
            if referrer_id == user.id:
                referrer_id = None
            else:
                # Проверяем существование реферера
                referrer_data = referral_system.get_user_data(referrer_id)
                if not referrer_data:
                    # Создаем запись для реферера если её нет
                    referral_system.create_user(referrer_id)
        except (ValueError, IndexError):
            referrer_id = None
    
    # Получаем или создаем данные пользователя
    user_data = referral_system.get_user_data(user.id)
    is_new_user = False
    
    if not user_data:
        user_data = referral_system.create_user(user.id, referrer_id)
        is_new_user = True
        
        # Отправляем уведомление рефереру о новом реферале
        if referrer_id:
            try:
                referrer_data = referral_system.get_user_data(referrer_id)
                await bot.send_message(
                    referrer_id,
                    "🎉 <b>Новый реферал!</b>\n\n"
                    f"👤 Пользователь начал работу по вашей ссылке\n"
                    f"📊 Всего приглашено: <b>{referrer_data.referrals_count}/3</b>\n\n"
                    f"<i>Реферал будет засчитан после выполнения всех условий:</i>\n"
                    f"• Нажал «Начать работу»\n"
                    f"• Подключил 1+ аккаунт\n"
                    f"• Завершил 24-часовой триал</i>",
                    parse_mode="HTML"
                )
            except Exception:
                pass
    
    # Уведомление админу
    await bot.send_message(
        ADMIN_CHANNEL_ID,
        f"🚀 Новый старт бота\n\n"
        f"👤 User ID: {user.id}\n"
        f"👀 Username: {username}\n"
        f"📛 Имя: {user.first_name}\n"
        f"🎯 Реферал от: {referrer_id if referrer_id else 'нет'}\n"
        f"🆕 Новый пользователь: {'Да' if is_new_user else 'Нет'}"
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
        "🎁 <b>Бесплатный тест — 24 часа</b>\n"
        "Попробуйте сервис без оплаты.\n\n"
        "🎯 <b>Реферальная программа</b>\n"
        "Пригласите 3 друзей и получите скидку 50% на любой тариф!\n\n"
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
            url="https://satoshi00722.github.io/BlastBotSite/"
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
@dp.message_handler(lambda m: m.text == "📢 Канал | Отзывы", state="*")
async def channel_reviews(msg: types.Message, state):
    await state.finish()

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton(
            text="📢 Перейти в канал",
            url="https://t.me/DigitaI_Services"
        )
    )

    text = (
        "🔥 <b>Наш канал — всё для упрощения твоей работы</b>\n\n"
        "Тут ты найдёшь:\n\n"
        "✅ <b>Кейсы и отзывы</b> — реальные результаты наших клиентов\n"
        "🤖 <b>Полезных ботов и инструменты</b> — автоматизация для твоего бизнеса\n"
        "💡 <b>Готовые решения</b> под разные задачи — от простого к сложному\n\n"
        "Можно просто посмотреть, а можно <b>заказать услугу</b> и получить результат "
        "без лишней головной боли.\n\n"
        "✨ <b>Подписывайся и бери то, что реально работает</b> 🚀"
    )

    await msg.answer(text, parse_mode="HTML", reply_markup=kb)

# ======================
# АККАУНТЫ (ОБНОВЛЕНО ДЛЯ РЕФЕРАЛОВ)
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
    session_file = f"{path}/sessions/{phone}"

    client = create_custom_telegram_client(session_file)
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
        
        # ОБНОВЛЕНИЕ: Обновляем счетчик подключенных аккаунтов
        referral_system.update_accounts_count(uid, len(accounts))

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
        
        # Получаем обновленный список аккаунтов
        accounts = get_accounts_info(uid)
        
        # ОБНОВЛЕНИЕ: Обновляем счетчик подключенных аккаунтов
        referral_system.update_accounts_count(uid, len(accounts))
        
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

@dp.message_handler(
    state=TextState.waiting,
    content_types=types.ContentTypes.ANY
)
async def save_text(msg: types.Message, state):
    path = user_dir(msg.from_user.id)

    if msg.forward_from_chat:
        if msg.forward_from_chat.type != "channel":
            await msg.answer(
                "❌ Перешли сообщение ИМЕННО ИЗ КАНАЛА",
                reply_markup=menu()
            )
            await state.finish()
            return

        data = {
            "type": "forward",
            "from_chat_id": msg.forward_from_chat.id,
            "message_id": msg.forward_from_message_id
        }
    else:
        data = {
            "type": "copy",
            "text": msg.text or msg.caption or ""
        }

    with open(f"{path}/message.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    await msg.answer("✅ Сообщение сохранено", reply_markup=menu())
    await state.finish()

# =====================
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
# ЛИЧНЫЙ КАБИНЕТ (ОБНОВЛЕНО ДЛЯ РЕФЕРАЛОВ)
# ======================
@dp.message_handler(lambda m: m.text == "👤 Личный кабинет", state="*")
async def cabinet(msg: types.Message, state):
    await state.finish()

    uid = msg.from_user.id
    accounts = get_accounts_info(uid)
    tariff = get_tariff(uid)
    text_msg = get_user_text(uid)
    settings = get_settings(uid)
    
    # Получаем реферальные данные
    user_data = referral_system.get_user_data(uid)
    if not user_data:
        user_data = referral_system.create_user(uid)

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
        seconds_left = tariff["expires"] - time.time()
        if seconds_left > 0:
            hours_left = int(seconds_left / 3600)
            minutes_left = int((seconds_left % 3600) / 60)
            if tariff["name"] == "FREE":
                if hours_left >= 1:
                    text += f"• ⏳ Осталось: <b>{hours_left} ч. {minutes_left} мин.</b>\n"
                else:
                    text += f"• ⏳ Осталось: <b>{minutes_left} мин.</b>\n"
            else:
                days_left = int(seconds_left / 86400)
                if days_left > 0:
                    text += f"• ⏳ Осталось: <b>{days_left} д.</b>\n"
                else:
                    text += f"• ⏳ Осталось: <b>{hours_left} ч.</b>\n"
        else:
            text += "• ⏳ <b>Срок истёк</b>\n"
    text += "\n"

    # РЕФЕРАЛЬНАЯ СИСТЕМА
    text += "🎯 <b>Реферальная программа:</b>\n"
    if user_data.referrer_id:
        text += f"• 👥 Пригласил вас: <code>{user_data.referrer_id}</code>\n"
    text += f"• 👥 Ваших рефералов: <b>{user_data.referrals_count}/3</b>\n"
    if user_data.discount_50:
        text += "• 🎁 Доступна скидка: <b>50%</b> ✅\n"
    elif user_data.used_discount:
        text += "• 🎁 Скидка: <b>уже использована</b>\n"
    else:
        remaining = 3 - user_data.referrals_count
        text += f"• 🎯 До скидки осталось: <b>{remaining} рефералов</b>\n"
    
    # Информация о триале
    if user_data.referrer_id:
        if user_data.trial_completed:
            text += "• ✅ Ваш триал завершен (реферал засчитан)\n"
        elif user_data.trial_started:
            from config import TRIAL_DURATION
            time_passed = time.time() - user_data.trial_start_time
            hours_left = max(0, (TRIAL_DURATION - time_passed) / 3600)
            text += f"• ⏳ До засчёта реферала: <b>{hours_left:.1f} ч.</b>\n"
    
    text += "\n"

    # ТЕКСТ РАССЫЛКИ
    text += "📄 <b>Текст рассылки:</b>\n"
    if text_msg:
        preview = text_msg[:300]
        text += f"{preview}\n"
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
            f"• 🔁 Пауза между циклами: <b>{settings['delay_between_cycles'] // 60} мин</b>\n"
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
    
    # 🧹 обновляем счетчик аккаунтов в реферальной системе
    referral_system.update_accounts_count(uid, 0)

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
    
    # обновляем счетчик аккаунтов в реферальной системе
    referral_system.update_accounts_count(uid, len(accounts))

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
# START / STOP WORK (ОБНОВЛЕНО ДЛЯ РЕФЕРАЛОВ)
# ======================
@dp.message_handler(lambda m: m.text == "▶️ Начать работу", state="*")
async def start_work(msg: types.Message, state):
    await state.finish()
    uid = msg.from_user.id
    
    # ОБНОВЛЕНИЕ: Отмечаем, что пользователь начал работу
    referral_system.mark_work_started(uid)
    
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
    if not os.path.exists(f"{path}/message.json"):
        await msg.answer("❌ Нет текста", reply_markup=menu())
        return
    if not os.path.exists(f"{path}/settings.json"):
        await msg.answer("❌ Нет настроек", reply_markup=menu())
        return

    # 🧹 если уже был воркер — очищаем старые логи
    if uid in workers:
        workers.pop(uid, None)

    stop_flag = {
        "stop": False,
        "logs": []
    }
    workers[uid] = stop_flag

    status = await msg.answer("🚀 Рассылка запущена\n📤 Отправлено: 0")

    async def progress(sent, errors, info=None):
        try:
            if isinstance(info, dict):
                phone = info.get("phone")

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
# РЕФЕРАЛЬНАЯ ПРОГРАММА (НОВАЯ ФУНКЦИЯ)
# ======================
@dp.message_handler(lambda m: m.text == "📊 Реферальная программа", state="*")
async def referral_program(msg: types.Message, state):
    await state.finish()
    
    user_id = msg.from_user.id
    user_data = referral_system.get_user_data(user_id)
    
    if not user_data:
        user_data = referral_system.create_user(user_id)
    
    # Обновляем данные
    user_data = referral_system.get_user_data(user_id)
    
    # Получаем username бота
    bot_info = await bot.get_me()
    bot_username = bot_info.username
    
    # Формируем сообщение
    message = referral_system.format_progress_message(user_data, bot_username)
    
    # Создаем клавиатуру
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📋 Подробные условия", callback_data="ref_details"))
    if user_data.referred_users:
        kb.add(InlineKeyboardButton("👥 Мои рефералы", callback_data="my_referees"))
    kb.add(InlineKeyboardButton("🔄 Обновить", callback_data="refresh_ref"))
    
    await msg.answer(message, parse_mode="HTML", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "ref_details", state="*")
async def ref_details(call: types.CallbackQuery):
    text = (
        "📋 <b>Подробные условия реферальной программы</b>\n\n"
        "✅ <b>Как засчитывается реферал:</b>\n"
        "1. Пользователь заходит по вашей ссылке\n"
        "2. Нажимает кнопку «▶️ Начать работу»\n"
        "3. Подключает минимум 1 аккаунт\n"
        "4. Использует бота не менее 24 часов\n"
        "5. Полностью завершает 24-часовой триал\n\n"
        "⚠️ <b>Важно:</b>\n"
        "• Реферал засчитывается только после полного завершения триала\n"
        "• Один пользователь может быть засчитан только 1 раз\n"
        "• Самоприглашение не работает\n"
        "• Учитываются только активные аккаунты\n\n"
        "🎁 <b>Награда:</b>\n"
        "• После 3 засчитанных рефералов вы получаете скидку 50%\n"
        "• Скидка применяется автоматически при оплате\n"
        "• Скидка одноразовая\n\n"
        "💡 <b>Совет:</b>\n"
        "Делитесь ссылкой с теми, кому действительно интересен ваш бот!"
    )
    
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="back_to_ref"))
    
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "my_referees", state="*")
async def my_referees(call: types.CallbackQuery):
    user_id = call.from_user.id
    user_data = referral_system.get_user_data(user_id)
    
    if not user_data or not user_data.referred_users:
        text = "📭 У вас пока нет засчитанных рефералов"
    else:
        text = "👥 <b>Ваши засчитанные рефералы:</b>\n\n"
        for i, ref_id in enumerate(user_data.referred_users, 1):
            text += f"{i}. ID: <code>{ref_id}</code>\n"
        
        text += f"\n🎯 Всего: <b>{len(user_data.referred_users)}/3</b>"
    
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("⬅️ Назад", callback_data="back_to_ref"))
    
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await call.answer()

@dp.callback_query_handler(lambda c: c.data == "refresh_ref", state="*")
async def refresh_ref(call: types.CallbackQuery):
    user_id = call.from_user.id
    user_data = referral_system.get_user_data(user_id)
    
    # Обновляем данные
    user_data = referral_system.get_user_data(user_id)
    
    # Получаем username бота
    bot_info = await bot.get_me()
    bot_username = bot_info.username
    
    # Формируем сообщение
    message = referral_system.format_progress_message(user_data, bot_username)
    
    # Создаем клавиатуру
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📋 Подробные условия", callback_data="ref_details"))
    if user_data.referred_users:
        kb.add(InlineKeyboardButton("👥 Мои рефералы", callback_data="my_referees"))
    kb.add(InlineKeyboardButton("🔄 Обновить", callback_data="refresh_ref"))
    
    await call.message.edit_text(message, parse_mode="HTML", reply_markup=kb)
    await call.answer("✅ Обновлено")

@dp.callback_query_handler(lambda c: c.data == "back_to_ref", state="*")
async def back_to_ref(call: types.CallbackQuery):
    user_id = call.from_user.id
    user_data = referral_system.get_user_data(user_id)
    
    bot_info = await bot.get_me()
    bot_username = bot_info.username
    
    message = referral_system.format_progress_message(user_data, bot_username)
    
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📋 Подробные условия", callback_data="ref_details"))
    if user_data.referred_users:
        kb.add(InlineKeyboardButton("👥 Мои рефералы", callback_data="my_referees"))
    kb.add(InlineKeyboardButton("🔄 Обновить", callback_data="refresh_ref"))
    
    await call.message.edit_text(message, parse_mode="HTML", reply_markup=kb)
    await call.answer()

# ======================
# ТАРИФЫ С РЕФЕРАЛЬНОЙ СКИДКОЙ
# ======================
@dp.message_handler(lambda m: m.text == "💳 Тарифы")
async def tariffs(msg: types.Message):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🥉 30 дней — 20 USDT")
    kb.add("🥈 90 дней — 35 USDT")
    kb.add("🥇 365 дней — 100 USDT")
    kb.add("⬅️ Назад")

    # Проверяем доступность скидки
    user_id = msg.from_user.id
    user_data = referral_system.get_user_data(user_id)
    discount_info = ""
    
    if user_data and user_data.discount_50:
        discount_info = "\n\n🎁 <b>У вас доступна скидка 50%!</b>\nСкидка применится автоматически при оплате."
    elif user_data and user_data.used_discount:
        discount_info = "\n\n⏳ <b>Вы уже использовали свою скидку 50%</b>"
    elif user_data and user_data.referrals_count > 0:
        remaining = 3 - user_data.referrals_count
        discount_info = f"\n\n🎯 <b>До скидки 50% осталось:</b> {remaining} рефералов\nПриглашайте друзей и экономьте!"

    await msg.answer(
        "💳 <b>ВЫБЕРИТЕ ТАРИФ</b>\n\n"
        "🥉 <b>30 ДНЕЙ</b>\n"
        "— до <b>10 аккаунтов</b>\n"
        "— цена: <b>20 USDT</b>\n\n"
        "🥈 <b>90 ДНЕЙ</b>\n"
        "— до <b>50 аккаунтов</b>\n"
        "— цена: <b>35 USDT</b>\n\n"
        "🥇 <b>365 ДНЕЙ</b>\n"
        "— до <b>100 аккаунтов</b>\n"
        "— цена: <b>100 USDT</b>\n\n"
        "✅ <b>После оплаты тариф активируется автоматически</b>\n"
        "🎁 <b>Бесплатный тестовый период:</b> 24 часа"
        f"{discount_info}",
        parse_mode="HTML",
        reply_markup=kb
    )

# ======================
# ФУНКЦИИ ДЛЯ РАБОТЫ СО СКИДКАМИ
# ======================
async def create_discounted_invoice(user_id: int, tariff_key: str, description: str):
    """Создать счет со скидкой если доступно"""
    base_price = TARIFFS[tariff_key]["price"]
    discount_applied = False
    
    # Проверяем доступность скидки
    if referral_system.can_use_discount(user_id):
        final_price = round(base_price * 0.5, 2)  # 50% скидка
        description += " (со скидкой 50%)"
        discount_applied = True
    else:
        final_price = base_price
    
    invoice = create_invoice(
        CRYPTOBOT_TOKEN,
        amount=final_price,
        description=description,
        payload=f"tariff_{tariff_key}_{user_id}"
    )
    
    return invoice, final_price, discount_applied

@dp.message_handler(lambda m: "30 дней" in m.text)
async def buy_30(msg: types.Message):
    user_id = msg.from_user.id
    
    # Создаем счет со скидкой если доступно
    invoice_data, final_price, discount_applied = await create_discounted_invoice(
        user_id, "30", "Тариф 30 дней"
    )
    
    inv = invoice_data["result"]
    
    save_payment(user_id, {
        "invoice_id": inv["invoice_id"],
        "tariff_key": "30",
        "original_price": TARIFFS["30"]["price"],
        "final_price": final_price,
        "discount_applied": discount_applied
    })
    
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("💸 Оплатить USDT", url=inv["pay_url"]),
        InlineKeyboardButton("🔄 Проверить оплату", callback_data="check_payment")
    )
    
    price_text = f"💰 Цена: {final_price} USDT"
    if discount_applied:
        price_text += f" (скидка 50%! Было: {TARIFFS['30']['price']} USDT)"
    
    await msg.answer(
        f"📦 Тариф 30 дней\n"
        f"{price_text}\n\n"
        "1️⃣ Оплатите счёт\n"
        "2️⃣ Нажмите «Проверить оплату»",
        reply_markup=kb
    )

@dp.message_handler(lambda m: "90 дней" in m.text)
async def buy_90(msg: types.Message):
    user_id = msg.from_user.id
    
    invoice_data, final_price, discount_applied = await create_discounted_invoice(
        user_id, "90", "Тариф 90 дней"
    )
    
    inv = invoice_data["result"]
    
    save_payment(user_id, {
        "invoice_id": inv["invoice_id"],
        "tariff_key": "90",
        "original_price": TARIFFS["90"]["price"],
        "final_price": final_price,
        "discount_applied": discount_applied
    })
    
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("💸 Оплатить USDT", url=inv["pay_url"]),
        InlineKeyboardButton("🔄 Проверить оплату", callback_data="check_payment")
    )
    
    price_text = f"💰 Цена: {final_price} USDT"
    if discount_applied:
        price_text += f" (скидка 50%! Было: {TARIFFS['90']['price']} USDT)"
    
    await msg.answer(
        f"📦 Тариф 90 дней\n"
        f"{price_text}\n\n"
        "1️⃣ Оплатите счёт\n"
        "2️⃣ Нажмите «Проверить оплату»",
        reply_markup=kb
    )

@dp.message_handler(lambda m: "365 дней" in m.text)
async def buy_365(msg: types.Message):
    user_id = msg.from_user.id
    
    invoice_data, final_price, discount_applied = await create_discounted_invoice(
        user_id, "365", "Тариф 365 дней"
    )
    
    inv = invoice_data["result"]
    
    save_payment(user_id, {
        "invoice_id": inv["invoice_id"],
        "tariff_key": "365",
        "original_price": TARIFFS["365"]["price"],
        "final_price": final_price,
        "discount_applied": discount_applied
    })
    
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("💸 Оплатить USDT", url=inv["pay_url"]),
        InlineKeyboardButton("🔄 Проверить оплату", callback_data="check_payment")
    )
    
    price_text = f"💰 Цена: {final_price} USDT"
    if discount_applied:
        price_text += f" (скидка 50%! Было: {TARIFFS['365']['price']} USDT)"
    
    await msg.answer(
        f"📦 Тариф 365 дней\n"
        f"{price_text}\n\n"
        "1️⃣ Оплатите счёт\n"
        "2️⃣ Нажмите «Проверить оплату»",
        reply_markup=kb
    )

# ======================
# ПРОВЕРКА ОПЛАТЫ (ОБНОВЛЕНО ДЛЯ РЕФЕРАЛОВ)
# ======================
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
    discount_applied = data.get("discount_applied", False)
    original_price = data.get("original_price", TARIFFS[tariff_key]["price"])
    final_price = data.get("final_price", original_price)

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

    # ✅ Активируем тариф
    activate_tariff(uid, tariff_key)
    delete_payment(uid)
    
    # ✅ Если была применена скидка - отмечаем ее как использованную
    if discount_applied:
        referral_system.mark_discount_used(uid)
    
    # Формируем сообщение об успехе
    message_text = "✅ Оплата получена.\n🎉 Тариф активирован."
    if discount_applied:
        saved = original_price - final_price
        message_text += f"\n\n🎁 <b>Скидка 50% применена!</b>\n💰 Сэкономлено: {saved} USDT\n\n💡 Скидка была использована, но вы можете приглашать новых друзей!"
    
    await call.message.answer(message_text, parse_mode="HTML")
    await call.message.edit_reply_markup()

# ======================
# RUN
# ======================
if __name__ == "__main__":
    print("=== START POLLING ===", flush=True)
    
    # Запускаем фоновую задачу проверки рефералов
    asyncio.create_task(check_referrals_periodically())
    
    try:
        executor.start_polling(dp, skip_updates=True)
    except Exception as e:
        import traceback

        print("FATAL ERROR:", e, flush=True)
        traceback.print_exc()
        time.sleep(60)





























