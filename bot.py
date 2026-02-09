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
import traceback

from telethon import TelegramClient
from config import ADMIN_CHANNEL_ID
from telethon.errors import SessionPasswordNeededError
from config import BOT_TOKEN, API_ID, API_HASH
from worker import spam_worker

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

# Имя вашего бота для реферальных ссылок
BOT_USERNAME = "BlastTGService_bot"

# Ваш ID админа
ADMIN_IDS = [7447763153]

# ======================
# REFERRAL SYSTEM FUNCTIONS
# ======================
def get_user_data(uid):
    """Получить данные пользователя"""
    path = user_dir(uid)
    file = f"{path}/user_data.json"
    
    if not os.path.exists(file):
        data = {
            "user_id": uid,
            "referrer_id": None,
            "trial_start_time": None,
            "trial_completed": False,
            "accounts_connected_count": 0,
            "referrals_count": 0,
            "discount_50": False,
            "discount_used": False,
            "referral_credited": False,
            "created_at": time.time(),
            "first_start": True,
            "start_work_clicked": False
        }
        save_user_data(uid, data)
        return data
    
    with open(file, "r") as f:
        return json.load(f)

def save_user_data(uid, data):
    """Сохранить данные пользователя"""
    path = user_dir(uid)
    file = f"{path}/user_data.json"
    with open(file, "w") as f:
        json.dump(data, f, indent=2)

def update_referral_count(referrer_id):
    """Обновить счетчик рефералов и проверить награду"""
    data = get_user_data(referrer_id)
    data["referrals_count"] += 1
    save_user_data(referrer_id, data)
    
    # Проверяем, достиг ли пользователь 3 рефералов
    if data["referrals_count"] == 3 and not data["discount_50"]:
        data["discount_50"] = True
        save_user_data(referrer_id, data)
        
        # Уведомляем пользователя
        asyncio.create_task(
            send_notification(referrer_id, 
                "🎉 Поздравляем! Вы пригласили 3 друзей!\n\n"
                "✅ Вы получили одноразовую скидку 50% на любой тариф!\n"
                "💳 Скидка будет применена автоматически при следующей оплате."
            )
        )
    
    # Уведомляем о новом реферале
    asyncio.create_task(
        send_notification(referrer_id,
            f"🎯 Новый реферал засчитан!\n"
            f"👥 Всего рефералов: {data['referrals_count']}/3\n\n"
            f"{'🎉 Вы получили скидку 50%!' if data['referrals_count'] == 3 else f'📈 До скидки осталось: {3 - data[\"referrals_count\"]} рефералов'}"
        )
    )

async def send_notification(uid, message):
    """Отправить уведомление пользователю"""
    try:
        await bot.send_message(uid, message)
    except Exception as e:
        print(f"Ошибка отправки уведомления {uid}: {e}")

def check_referral_conditions(uid):
    """Проверить все условия для засчета реферала"""
    user_data = get_user_data(uid)
    
    # 1. Проверяем, есть ли реферер
    if not user_data["referrer_id"]:
        return False
    
    # 2. Проверяем, не был ли уже засчитан
    if user_data["referral_credited"]:
        return False
    
    # 3. Проверяем, что нажал "Начать работу"
    if not user_data["start_work_clicked"]:
        return False
    
    # 4. Проверяем, что подключен минимум 1 аккаунт
    if user_data["accounts_connected_count"] < 1:
        return False
    
    # 5. Проверяем, что триал завершен
    if not user_data["trial_completed"]:
        return False
    
    # 6. Проверяем, что прошло минимум 24 часа с начала триала
    if user_data["trial_start_time"]:
        trial_duration = time.time() - user_data["trial_start_time"]
        if trial_duration < 24 * 60 * 60:  # 24 часа
            return False
    
    return True

def mark_referral_credited(uid):
    """Пометить реферала как засчитанного"""
    data = get_user_data(uid)
    data["referral_credited"] = True
    save_user_data(uid, data)

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
        
        # Устанавливаем время начала триала
        user_data = get_user_data(uid)
        if not user_data["trial_start_time"]:
            user_data["trial_start_time"] = time.time()
            save_user_data(uid, user_data)
        
        return data

    with open(tf, "r") as f:
        return json.load(f)

def is_tariff_active(uid):
    tariff = get_tariff(uid)
    return tariff["expires"] and time.time() < tariff["expires"]

def activate_tariff(uid, tariff_key, apply_discount=False):
    tariff = TARIFFS[tariff_key]
    path = user_dir(uid)
    
    # Проверяем скидку
    user_data = get_user_data(uid)
    final_price = tariff["price"]
    discount_applied = False
    
    if apply_discount and user_data["discount_50"] and not user_data["discount_used"]:
        final_price = round(tariff["price"] * 0.5, 2)
        discount_applied = True
        user_data["discount_50"] = False
        user_data["discount_used"] = True
        save_user_data(uid, user_data)
    
    # Обновляем тариф
    data = {
        "name": tariff["name"],
        "expires": int(time.time()) + tariff["days"] * 86400,
        "max_accounts": tariff["max_accounts"]
    }

    with open(f"{path}/tariff.json", "w") as f:
        json.dump(data, f)
    
    # Помечаем триал как завершенный при покупке тарифа
    if not user_data["trial_completed"]:
        user_data["trial_completed"] = True
        save_user_data(uid, user_data)
    
    return discount_applied, final_price

def menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🔓 Подключить", "📝 Текст")
    kb.row("⚙️ Настройки", "👤 Личный кабинет")
    kb.row("💳 Тарифы", "👥 Реферальная программа")
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
# START (С РЕФЕРАЛЬНОЙ СИСТЕМОЙ)
# ======================
@dp.message_handler(commands=["start"], state="*")
async def start(msg: types.Message, state):
    await state.finish()

    user = msg.from_user
    uid = user.id
    username = f"@{user.username}" if user.username else "нет"
    
    # Получаем аргументы из команды /start
    args = msg.get_args()
    referrer_id = None
    
    # Проверяем, есть ли реферальная ссылка
    if args and args.startswith("ref_"):
        try:
            referrer_id = int(args.split("_")[1])
            
            # Проверяем, что пользователь не приглашает сам себя
            if referrer_id == uid:
                referrer_id = None
            else:
                # Проверяем, существует ли реферер
                referrer_path = user_dir(referrer_id)
                if not os.path.exists(referrer_path):
                    referrer_id = None
        except:
            referrer_id = None
    
    # Получаем или создаем данные пользователя
    user_data = get_user_data(uid)
    
    # Если это первый старт и есть валидный реферер
    if user_data["first_start"] and referrer_id:
        user_data["referrer_id"] = referrer_id
        user_data["first_start"] = False
        save_user_data(uid, user_data)
        
        # Уведомляем реферера о новом реферале
        try:
            await bot.send_message(
                referrer_id,
                f"🎯 По вашей ссылке зарегистрировался новый пользователь!\n"
                f"👤 @{username if user.username else user.first_name}\n"
                f"🆔 ID: {uid}\n\n"
                f"📊 Статус: ожидает завершения триала"
            )
        except:
            pass
    elif not user_data["first_start"]:
        # Обновляем флаг first_start если нужно
        user_data["first_start"] = False
        save_user_data(uid, user_data)

    # уведомление админу
    try:
        await bot.send_message(
            ADMIN_CHANNEL_ID,
            f"🚀 Новый старт бота\n\n"
            f"👤 User ID: {uid}\n"
            f"👀 Username: {username}\n"
            f"📛 Имя: {user.first_name}\n"
            f"🎯 Реферер: {referrer_id if referrer_id else 'нет'}"
        )
    except:
        pass

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
        "👥 <b>Реферальная программа:</b>\n"
        "• Пригласите 3 друзей\n"
        "• Получите скидку 50% на любой тариф!\n\n"
        "⬇️ Выберите действие ниже"
    )

    try:
        with open("welcome.jpg", "rb") as photo:
            await bot.send_photo(
                chat_id=msg.chat.id,
                photo=photo,
                caption=text,
                parse_mode="HTML",
                reply_markup=menu()
            )
    except:
        await msg.answer(text, parse_mode="HTML", reply_markup=menu())

# ======================
# BACK
# ======================
@dp.message_handler(lambda m: m.text == "⬅️ Назад", state="*")
async def back(msg: types.Message, state):
    await reset_login(msg.from_user.id)
    await state.finish()
    await msg.answer("↩️ Возврат в меню", reply_markup=menu())

# ======================
# РЕФЕРАЛЬНАЯ ПРОГРАММА
# ======================
@dp.message_handler(lambda m: m.text == "👥 Реферальная программа", state="*")
async def referral_program(msg: types.Message, state):
    await state.finish()
    
    uid = msg.from_user.id
    user_data = get_user_data(uid)
    
    # Создаем реферальную ссылку с вашим именем бота
    referral_link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
    
    text = (
        "👥 <b>Реферальная программа</b>\n\n"
        f"🎯 Ваши рефералы: <b>{user_data['referrals_count']}/3</b>\n\n"
        
        "🔗 <b>Ваша реферальная ссылка:</b>\n"
        f"<code>{referral_link}</code>\n\n"
        
        "📋 <b>Условия засчета реферала:</b>\n"
        "1. Пользователь впервые зашел по вашей ссылке\n"
        "2. Нажал «▶️ Начать работу»\n"
        "3. Подключил минимум 1 аккаунт\n"
        "4. Пользовался ботом не менее 24 часов\n"
        "5. Полностью завершил 24-часовой триал\n\n"
        
        "🎁 <b>Награда:</b>\n"
        "✅ <b>50% скидка</b> на любой тариф!\n"
        "• Скидка одноразовая\n"
        "• Применяется автоматически\n"
        "• Действует на любой тариф\n\n"
    )
    
    # Добавляем статус скидки
    if user_data['discount_50'] and not user_data['discount_used']:
        text += "💰 <b>Текущий статус:</b> 🎉 Скидка 50% доступна!"
    elif user_data['discount_used']:
        text += "💰 <b>Текущий статус:</b> ✅ Скидка 50% использована"
    else:
        needed = 3 - user_data['referrals_count']
        text += f"💰 <b>Текущий статус:</b> 🔒 Пригласите еще {needed} друзей"
    
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📤 Поделиться ссылкой", 
           url=f"https://t.me/share/url?url={referral_link}&text=Присоединяйся%20к%20BlastBot%20—%20мощный%20бот%20для%20рассылок%20в%20Telegram!%20🎯"))
    
    await msg.answer(text, parse_mode="HTML", reply_markup=kb)

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
# АККАУНТЫ
# ======================
@dp.message_handler(lambda m: m.text == "🔓 Подключить", state="*")
async def add_account(msg: types.Message, state):
    uid = msg.from_user.id
    
    if not is_tariff_active(uid):
        await msg.answer(
            "⛔ <b>Тестовый период закончился</b>\n\n"
            "💳 Купите тариф, чтобы добавлять аккаунты",
            parse_mode="HTML",
            reply_markup=menu()
        )
        return
    
    tariff = get_tariff(uid)
    accounts = get_sessions(uid)

    if len(accounts) >= tariff["max_accounts"]:
        await msg.answer(
            f"❌ Лимит аккаунтов достигнут\n\n"
            f"💳 Тариф: {tariff['name']}\n"
            f"🔢 Максимум: {tariff['max_accounts']} аккаунтов\n\n"
            f"👉 Обновите тариф",
            reply_markup=menu()
        )
        return
    
    await reset_login(uid)
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
    uid = msg.from_user.id
    path = user_dir(uid)
    session_file = f"{path}/sessions/{phone}"

    client = create_custom_telegram_client(session_file)
    await client.connect()
    await client.send_code_request(phone)

    login_clients[uid] = client
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
        
        # Обновляем счетчик подключенных аккаунтов
        user_data = get_user_data(uid)
        user_data["accounts_connected_count"] = len(accounts)
        save_user_data(uid, user_data)

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
        
        data = await state.get_data()
        accounts_file = f"{user_dir(uid)}/accounts.json"
        accounts = []

        if os.path.exists(accounts_file):
            with open(accounts_file, "r") as f:
                accounts = json.load(f)

        me = await client.get_me()
        accounts.append({
            "phone": data["phone"],
            "username": me.username or "no_username"
        })

        with open(accounts_file, "w") as f:
            json.dump(accounts, f, indent=2)
        
        # Обновляем счетчик подключенных аккаунтов
        user_data = get_user_data(uid)
        user_data["accounts_connected_count"] = len(accounts)
        save_user_data(uid, user_data)
        
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
# ЛИЧНЫЙ КАБИНЕТ (ОБНОВЛЕН)
# ======================
@dp.message_handler(lambda m: m.text == "👤 Личный кабинет", state="*")
async def cabinet(msg: types.Message, state):
    await state.finish()

    uid = msg.from_user.id
    accounts = get_accounts_info(uid)
    tariff = get_tariff(uid)
    text_msg = get_user_text(uid)
    settings = get_settings(uid)
    user_data = get_user_data(uid)

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
    text += "👥 <b>Реферальная система:</b>\n"
    text += f"• 🎯 Рефералов: <b>{user_data['referrals_count']}/3</b>\n"
    if user_data['discount_50'] and not user_data['discount_used']:
        text += "• 🎁 <b>Скидка 50% доступна!</b>\n"
    elif user_data['discount_used']:
        text += "• ✅ Скидка 50% использована\n"
    else:
        text += f"• 📈 До скидки осталось: <b>{3 - user_data['referrals_count']}</b> рефералов\n"
    
    # Показываем реферальную ссылку
    referral_link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
    text += f"• 🔗 Ваша ссылка: <code>{referral_link}</code>\n"
    
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

    if uid in workers:
        workers[uid]["stop"] = True
        task = workers[uid].get("task")
        if task:
            task.cancel()
        workers.pop(uid, None)

    if uid in login_clients:
        try:
            await login_clients[uid].disconnect()
        except:
            pass
        login_clients.pop(uid, None)

    sessions_path = f"{path}/sessions"
    if os.path.exists(sessions_path):
        for file in os.listdir(sessions_path):
            try:
                os.remove(os.path.join(sessions_path, file))
            except:
                pass

    acc_file = f"{path}/accounts.json"
    if os.path.exists(acc_file):
        os.remove(acc_file)
    
    # Обновляем счетчик аккаунтов
    user_data = get_user_data(uid)
    user_data["accounts_connected_count"] = 0
    save_user_data(uid, user_data)

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

    for file in os.listdir(sessions_path):
        if file.startswith(phone):
            try:
                os.remove(os.path.join(sessions_path, file))
            except:
                pass

    accounts.pop(idx)
    with open(accounts_file, "w") as f:
        json.dump(accounts, f, indent=2)
    
    user_data = get_user_data(uid)
    user_data["accounts_connected_count"] = len(accounts)
    save_user_data(uid, user_data)

    if uid in workers and "logs" in workers[uid]:
        workers[uid]["logs"] = [
            l for l in workers[uid]["logs"]
            if l.get("phone") != phone
        ]

    if not accounts and uid in workers and "logs" in workers[uid]:
        workers[uid]["logs"].clear()

    await msg.answer("✅ Аккаунт полностью удалён", reply_markup=menu())

# ======================
# START / STOP WORK (ОБНОВЛЕНО)
# ======================
@dp.message_handler(lambda m: m.text == "▶️ Начать работу", state="*")
async def start_work(msg: types.Message, state):
    await state.finish()
    uid = msg.from_user.id

    # Обновляем флаг "Начать работу"
    user_data = get_user_data(uid)
    user_data["start_work_clicked"] = True
    
    # Устанавливаем время начала триала если еще не установлено
    if not user_data["trial_start_time"]:
        user_data["trial_start_time"] = time.time()
    
    save_user_data(uid, user_data)

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
# ТАРИФЫ (СО СКИДКОЙ)
# ======================
@dp.message_handler(lambda m: m.text == "💳 Тарифы")
async def tariffs(msg: types.Message):
    uid = msg.from_user.id
    user_data = get_user_data(uid)
    
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🥉 30 дней — 20 USDT")
    kb.add("🥈 90 дней — 35 USDT")
    kb.add("🥇 365 дней — 100 USDT")
    kb.add("⬅️ Назад")

    discount_info = ""
    if user_data["discount_50"] and not user_data["discount_used"]:
        discount_info = (
            "\n\n🎉 <b>У ВАС ЕСТЬ СКИДКУ 50%!</b>\n"
            "💰 Цены с учётом скидки:\n"
            f"• 30 дней — <b>10 USDT</b> (вместо 20)\n"
            f"• 90 дней — <b>17.5 USDT</b> (вместо 35)\n"
            f"• 365 дней — <b>50 USDT</b> (вместо 100)\n"
            "✅ Скидка применится автоматически!"
        )

    await msg.answer(
        "💳 <b>ВЫБЕРИТЕ ТАРИФ</b>\n\n"
        "🥉 <b>30 ДНЕЙ</b>\n"
        "— до <b>10 аккаунтов</b>\n"
        "💰 Цена: <b>20 USDT</b>\n\n"
        "🥈 <b>90 ДНЕЙ</b>\n"
        "— до <b>50 аккаунтов</b>\n"
        "💰 Цена: <b>35 USDT</b>\n\n"
        "🥇 <b>365 ДНЕЙ</b>\n"
        "— до <b>100 аккаунтов</b>\n"
        "💰 Цена: <b>100 USDT</b>\n\n"
        "✅ <b>После оплаты тариф активируется автоматически</b>\n\n"
        "🎁 <b>Бесплатный тестовый период:</b> 24 часа (1 день)"
        f"{discount_info}",
        parse_mode="HTML",
        reply_markup=kb
    )

@dp.message_handler(lambda m: "30 дней" in m.text)
async def buy_30(msg: types.Message):
    uid = msg.from_user.id
    user_data = get_user_data(uid)
    
    apply_discount = user_data["discount_50"] and not user_data["discount_used"]
    final_price = round(20 * 0.5, 2) if apply_discount else 20
    
    invoice = create_invoice(
        CRYPTOBOT_TOKEN,
        amount=final_price,
        description=f"Тариф 30 дней{' (со скидкой 50%)' if apply_discount else ''}",
        payload=f"tariff_30_{uid}"
    )

    inv = invoice["result"]

    save_payment(uid, {
        "invoice_id": inv["invoice_id"],
        "tariff_key": "30",
        "apply_discount": apply_discount,
        "original_price": 20,
        "final_price": final_price
    })

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("💸 Оплатить USDT", url=inv["pay_url"]),
        InlineKeyboardButton("🔄 Проверить оплату", callback_data="check_payment")
    )
    
    price_text = f"💰 Цена: {final_price} USDT"
    if apply_discount:
        price_text = f"💰 Цена: <s>20 USDT</s> <b>{final_price} USDT</b> (скидка 50%)"

    await msg.answer(
        f"📦 Тариф 30 дней\n"
        f"{price_text}\n\n"
        "1️⃣ Оплатите счёт\n"
        "2️⃣ Нажмите «Проверить оплату»",
        parse_mode="HTML",
        reply_markup=kb
    )

@dp.message_handler(lambda m: "90 дней" in m.text)
async def buy_90(msg: types.Message):
    uid = msg.from_user.id
    user_data = get_user_data(uid)
    
    apply_discount = user_data["discount_50"] and not user_data["discount_used"]
    final_price = round(35 * 0.5, 2) if apply_discount else 35
    
    invoice = create_invoice(
        CRYPTOBOT_TOKEN,
        amount=final_price,
        description=f"Тариф 90 дней{' (со скидкой 50%)' if apply_discount else ''}",
        payload=f"tariff_90_{uid}"
    )

    inv = invoice["result"]

    save_payment(uid, {
        "invoice_id": inv["invoice_id"],
        "tariff_key": "90",
        "apply_discount": apply_discount,
        "original_price": 35,
        "final_price": final_price
    })

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("💸 Оплатить USDT", url=inv["pay_url"]),
        InlineKeyboardButton("🔄 Проверить оплату", callback_data="check_payment")
    )
    
    price_text = f"💰 Цена: {final_price} USDT"
    if apply_discount:
        price_text = f"💰 Цена: <s>35 USDT</s> <b>{final_price} USDT</b> (скидка 50%)"

    await msg.answer(
        f"📦 Тариф 90 дней\n"
        f"{price_text}\n\n"
        "1️⃣ Оплатите счёт\n"
        "2️⃣ Нажмите «Проверить оплату»",
        parse_mode="HTML",
        reply_markup=kb
    )

@dp.message_handler(lambda m: "365 дней" in m.text)
async def buy_365(msg: types.Message):
    uid = msg.from_user.id
    user_data = get_user_data(uid)
    
    apply_discount = user_data["discount_50"] and not user_data["discount_used"]
    final_price = round(100 * 0.5, 2) if apply_discount else 100
    
    invoice = create_invoice(
        CRYPTOBOT_TOKEN,
        amount=final_price,
        description=f"Тариф 365 дней{' (со скидкой 50%)' if apply_discount else ''}",
        payload=f"tariff_365_{uid}"
    )

    inv = invoice["result"]

    save_payment(uid, {
        "invoice_id": inv["invoice_id"],
        "tariff_key": "365",
        "apply_discount": apply_discount,
        "original_price": 100,
        "final_price": final_price
    })

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("💸 Оплатить USDT", url=inv["pay_url"]),
        InlineKeyboardButton("🔄 Проверить оплату", callback_data="check_payment")
    )
    
    price_text = f"💰 Цена: {final_price} USDT"
    if apply_discount:
        price_text = f"💰 Цена: <s>100 USDT</s> <b>{final_price} USDT</b> (скидка 50%)"

    await msg.answer(
        f"📦 Тариф 365 дней\n"
        f"{price_text}\n\n"
        "1️⃣ Оплатите счёт\n"
        "2️⃣ Нажмите «Проверить оплату»",
        parse_mode="HTML",
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
    apply_discount = data.get("apply_discount", False)
    original_price = data.get("original_price", 0)
    final_price = data.get("final_price", 0)

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
    discount_applied, price_paid = activate_tariff(uid, tariff_key, apply_discount)
    delete_payment(uid)

    success_text = "✅ Оплата получена.\n🎉 Тариф активирован."
    
    if discount_applied:
        success_text += f"\n💰 Скидка 50% применена! Стоимость: {price_paid} USDT (вместо {original_price})"
        await send_notification(uid,
            "🎉 Вы использовали скидку 50%!\n"
            "✅ Скидка успешно применена к оплате.\n"
            "📊 Статус реферальной программы обновлён."
        )

    await call.message.answer(success_text)
    await call.message.edit_reply_markup()

# ======================
# АДМИН КОМАНДЫ
# ======================
@dp.message_handler(commands=["ref_test"], state="*")
async def ref_test(msg: types.Message, state):
    """Тест реферальной системы (только для админа)"""
    uid = msg.from_user.id
    
    # Проверяем админа
    if uid not in ADMIN_IDS:
        await msg.answer("⛔ Эта команда только для администраторов")
        return
    
    user_data = get_user_data(uid)
    
    text = (
        f"🧪 <b>Тест реферальной системы</b>\n\n"
        f"🆔 Ваш ID: <code>{uid}</code>\n"
        f"👥 Рефералов: {user_data['referrals_count']}/3\n"
        f"🎯 Реферер: {user_data['referrer_id'] if user_data['referrer_id'] else 'нет'}\n"
        f"⏱ Начало триала: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(user_data['trial_start_time'])) if user_data['trial_start_time'] else 'не установлено'}\n"
        f"✅ Триал завершён: {'да' if user_data['trial_completed'] else 'нет'}\n"
        f"🔧 Нажал 'Начать работу': {'да' if user_data['start_work_clicked'] else 'нет'}\n"
        f"📱 Аккаунтов: {user_data['accounts_connected_count']}\n"
        f"🎁 Скидка доступна: {'да' if user_data['discount_50'] else 'нет'}\n"
        f"💳 Скидка использована: {'да' if user_data['discount_used'] else 'нет'}\n"
        f"👥 Реферал засчитан: {'да' if user_data['referral_credited'] else 'нет'}\n\n"
        f"🔗 Реферальная ссылка:\n<code>https://t.me/{BOT_USERNAME}?start=ref_{uid}</code>"
    )
    
    await msg.answer(text, parse_mode="HTML")

@dp.message_handler(commands=["ref_reset"], state="*")
async def ref_reset(msg: types.Message, state):
    """Сброс реферальных данных (только для админа)"""
    uid = msg.from_user.id
    
    # Проверяем админа
    if uid not in ADMIN_IDS:
        await msg.answer("⛔ Эта команда только для администраторов")
        return
    
    user_data = get_user_data(uid)
    user_data["referrals_count"] = 0
    user_data["discount_50"] = False
    user_data["discount_used"] = False
    save_user_data(uid, user_data)
    
    await msg.answer("✅ Реферальные данные сброшены")

@dp.message_handler(commands=["ref_help"], state="*")
async def ref_help(msg: types.Message, state):
    """Помощь по реферальной системе (только для админа)"""
    uid = msg.from_user.id
    
    # Проверяем админа
    if uid not in ADMIN_IDS:
        await msg.answer("⛔ Эта команда только для администраторов")
        return
    
    help_text = (
        "🛠 <b>Команды админа для реферальной системы</b>\n\n"
        "📊 <code>/ref_test</code> - Просмотр всех данных реферальной системы\n"
        "🔄 <code>/ref_reset</code> - Сброс реферальных данных\n\n"
        "📈 <b>Как тестировать:</b>\n"
        "1. Откройте бота в двух разных аккаунтах\n"
        "2. Скопируйте реферальную ссылку из /ref_test\n"
        "3. Перейдите по ссылке со второго аккаунта\n"
        "4. Выполните все условия (аккаунт, работа, 24 часа)\n"
        "5. Проверьте /ref_test в первом аккаунте"
    )
    
    await msg.answer(help_text, parse_mode="HTML")

# ======================
# ПРОВЕРКА ТРИАЛА И РЕФЕРАЛОВ
# ======================
async def check_trial_completions():
    """Периодическая проверка завершения триалов"""
    while True:
        try:
            users_dir = "users"
            if os.path.exists(users_dir):
                for user_folder in os.listdir(users_dir):
                    if user_folder.startswith("user_"):
                        try:
                            uid = int(user_folder.split("_")[1])
                            user_data = get_user_data(uid)
                            
                            # Проверяем завершение 24-часового триала
                            if not user_data["trial_completed"] and user_data["trial_start_time"]:
                                if time.time() - user_data["trial_start_time"] >= 24 * 60 * 60:
                                    user_data["trial_completed"] = True
                                    save_user_data(uid, user_data)
                                    print(f"✅ Триал завершен для пользователя {uid}")
                                    
                                    # Проверяем условия для реферала сразу после завершения триала
                                    if check_referral_conditions(uid):
                                        referrer_id = user_data["referrer_id"]
                                        if referrer_id:
                                            update_referral_count(referrer_id)
                                            mark_referral_credited(uid)
                                            print(f"🎯 Реферал засчитан: {uid} -> {referrer_id}")
                                            
                                            await send_notification(uid,
                                                "✅ Ваш 24-часовой триал завершён!\n"
                                                "👥 Вы засчитаны как реферал.\n"
                                                "🎯 Ваш пригласитель получил уведомление."
                                            )
                        except Exception as e:
                            print(f"Ошибка проверки пользователя {user_folder}: {e}")
                            continue
        except Exception as e:
            print(f"Ошибка в check_trial_completions: {e}")
        
        await asyncio.sleep(60)  # Проверка каждую минуту

# ======================
# ЗАПУСК ПРОВЕРКИ ТРИАЛОВ
# ======================
async def on_startup(dp):
    """Запуск при старте бота"""
    print("=== REFERRAL SYSTEM STARTED ===")
    asyncio.create_task(check_trial_completions())

# ======================
# RUN
# ======================
if __name__ == "__main__":
    print("=== START POLLING ===", flush=True)
    try:
        executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
    except Exception as e:
        print("FATAL ERROR:", e, flush=True)
        traceback.print_exc()
        time.sleep(60)



















