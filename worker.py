import asyncio
import os
import json
import random
from telethon import TelegramClient, errors
from config import API_ID, API_HASH


async def spam_worker(user_dir, stop_flag, progress_cb, accounts):
    # ===== LOAD DATA =====
    settings = json.load(open(f"{user_dir}/settings.json"))
    message = open(f"{user_dir}/message.txt", encoding="utf-8").read()
    sessions_dir = f"{user_dir}/sessions"

    delay_groups = settings["delay_between_groups"]
    groups_per_account = settings["groups_per_account"]
    delay_cycle = settings["delay_between_cycles"]

    sent = 0
    errors_count = 0

    # 🔁 MAIN LOOP
    while not stop_flag["stop"]:

        # идём по аккаунтам В ТОМ ЖЕ ПОРЯДКЕ, что в кабинете
        for acc_index, acc in enumerate(accounts, start=1):
            if stop_flag["stop"]:
                break

            phone = acc["phone"]
            session_path = f"{sessions_dir}/{phone}"

            # если сессии нет — пропускаем
            if not os.path.exists(session_path + ".session"):
                continue

            client = TelegramClient(
                session_path,
                API_ID,
                API_HASH
            )

            try:
                await client.start()
            except Exception:
                errors_count += 1
                await progress_cb(sent, errors_count)
                continue

            sent_from_account = 0
            failed_attempts = 0

            dialogs = []
            async for d in client.iter_dialogs():
                if d.is_group or d.is_channel:
                    dialogs.append(d)

            random.shuffle(dialogs)

            for d in dialogs:
                if stop_flag["stop"]:
                    break

                if sent_from_account >= groups_per_account:
                    break

                try:
                    await client.send_message(d.id, message)
                    sent += 1
                    sent_from_account += 1
                    failed_attempts = 0

                    await progress_cb(sent, errors_count)
                    await asyncio.sleep(delay_groups)

                except errors.FloodWaitError as e:
                    await asyncio.sleep(e.seconds)

                except Exception:
                    failed_attempts += 1
                    errors_count += 1
                    await progress_cb(sent, errors_count)

                    # 🚫 SPAM-BLOCK: 15 ошибок подряд и 0 отправок
                    if failed_attempts >= 15 and sent_from_account == 0:
                        await progress_cb(
                            sent,
                            errors_count,
                            spam_index=acc_index  # 👈 НОМЕР АККАУНТА
                        )
                        break

            await client.disconnect()

        # ⏸ PAUSE BETWEEN CYCLES
        if not stop_flag["stop"]:
            await asyncio.sleep(delay_cycle)

    return sent, errors_count
