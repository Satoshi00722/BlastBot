import asyncio
import os
import json
import random
from telethon import TelegramClient, errors
from config import API_ID, API_HASH


blacklist_keywords = [
    "запрещена реклама",
    "реклама запрещена",
    "no ads",
    "без рекламы",
    "no advertising",
]


async def spam_worker(user_dir, stop_flag, progress_cb):
    settings = json.load(open(f"{user_dir}/settings.json"))
    message_data = json.load(
        open(f"{user_dir}/message.json", encoding="utf-8")
    )
    sessions_dir = f"{user_dir}/sessions"

    delay_groups = settings["delay_between_groups"]
    groups_per_account = settings["groups_per_account"]
    delay_cycle = settings["delay_between_cycles"]

    sent = 0
    errors_count = 0

    blocked_accounts = set()

    while not stop_flag["stop"]:
        session_files = [
            f for f in os.listdir(sessions_dir)
            if f.endswith(".session")
        ]
        random.shuffle(session_files)

        for sess in session_files:
            if stop_flag["stop"]:
                break

            acc_name = sess.replace(".session", "")

            if acc_name in blocked_accounts:
                continue

            client = TelegramClient(
                f"{sessions_dir}/{acc_name}",
                API_ID,
                API_HASH
            )

            sent_from_account = 0  # 🔥 КЛЮЧЕВО

            try:
                await client.start()

                async for dialog in client.iter_dialogs():
                    if stop_flag["stop"]:
                        break

                    if sent_from_account >= groups_per_account:
                        break

                    if not (dialog.is_group or dialog.is_channel):
                        continue

                    try:
                        chat = await client.get_entity(dialog.id)
                        chat_name = (dialog.name or "").lower()
                        chat_about = getattr(chat, "about", "") or ""

                        if any(k in chat_name for k in blacklist_keywords) or \
                           any(k in chat_about.lower() for k in blacklist_keywords):
                            continue

                        if message_data["type"] == "forward":
                            await client.forward_messages(
                                dialog.id,
                                message_data["message_id"],
                                message_data["chat_id"]
                            )
                        else:
                            await client.send_message(
                                dialog.id,
                                message_data["text"]
                            )

                        sent += 1
                        sent_from_account += 1

                        await progress_cb(sent, errors_count)

                        await asyncio.sleep(
                            random.randint(delay_groups, delay_groups + 3)
                        )

                    # 🚷 нельзя писать — просто пропускаем
                    except (
                        errors.ChatWriteForbiddenError,
                        errors.ChannelPrivateError,
                        errors.UserBannedInChannelError
                    ):
                        continue

                    # ⚠️ любая другая ошибка — считаем
                    except Exception:
                        errors_count += 1
                        continue

                # 🔥 ВОТ ОНО — ГЛАВНОЕ МЕСТО
                if sent_from_account == 0:
                    blocked_accounts.add(acc_name)

                    await progress_cb(
                        sent,
                        errors_count,
                        {
                            "phone": acc_name,
                            "reason": "spam_block"
                        }
                    )

            except Exception:
                errors_count += 1
                blocked_accounts.add(acc_name)

                await progress_cb(
                    sent,
                    errors_count,
                    {
                        "phone": acc_name,
                        "reason": "error"
                    }
                )

            finally:
                try:
                    await client.disconnect()
                except:
                    pass

        if not stop_flag["stop"]:
            await asyncio.sleep(delay_cycle)

    return sent, errors_count

















