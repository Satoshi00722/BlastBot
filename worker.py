import asyncio
import os
import json
import random
from telethon import TelegramClient, errors
from config import API_ID, API_HASH


async def spam_worker(user_dir, stop_flag, progress_cb):
    settings = json.load(open(f"{user_dir}/settings.json"))
    message = open(f"{user_dir}/message.txt", encoding="utf-8").read()
    sessions_dir = f"{user_dir}/sessions"

    delay_groups = settings["delay_between_groups"]
    groups_per_account = settings["groups_per_account"]
    delay_cycle = settings["delay_between_cycles"]

    sent = 0
    errors_count = 0

    # 🔁 БЕСКОНЕЧНЫЙ ЦИКЛ
    while not stop_flag["stop"]:

        session_files = os.listdir(sessions_dir)
        random.shuffle(session_files)  # случайный порядок аккаунтов

        for sess in session_files:
            attempts = 0  # попытки отправки
            success = 0  # успешные отправки
            if stop_flag["stop"]:
                break

            client = TelegramClient(
                f"{sessions_dir}/{sess.replace('.session','')}",
                API_ID,
                API_HASH
            )
            await client.start()

            dialogs = []
            async for d in client.iter_dialogs():
                if d.is_group or d.is_channel:
                    dialogs.append(d)

            random.shuffle(dialogs)  # 🔥 случайные группы каждый круг

            sent_from_account = 0

            for d in dialogs:
                if stop_flag["stop"]:
                    break

                if sent_from_account >= groups_per_account:
                    break

                try:
                    await client.send_message(d.id, message)
                    sent += 1
                    sent_from_account += 1
                    success += 1
                    attempts += 1
                    await progress_cb(sent, errors_count)

                    await asyncio.sleep(delay_groups)

                except errors.FloodWaitError as e:
                    await asyncio.sleep(e.seconds)

                except Exception:
                    errors_count += 1
                    attempts += 1
                    await progress_cb(sent, errors_count)

                    if attempts >= 15 and success == 0:
                        await progress_cb(
                            sent,
                            errors_count,
                            spam_account=sess.replace(".session", "")
                        )
                        break

            await client.disconnect()


        # ⏸ ПАУЗА ПОСЛЕ ВСЕХ АККАУНТОВ
        if not stop_flag["stop"]:
            await asyncio.sleep(delay_cycle)

    return sent, errors_count
