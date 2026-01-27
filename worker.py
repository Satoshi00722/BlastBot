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
    "bitswap chat"
]


async def spam_worker(user_dir, stop_flag, progress_cb):
    settings = json.load(open(f"{user_dir}/settings.json"))
    message = open(f"{user_dir}/message.txt", encoding="utf-8").read()
    sessions_dir = f"{user_dir}/sessions"

    delay_groups = settings["delay_between_groups"]
    groups_per_account = settings["groups_per_account"]
    delay_cycle = settings["delay_between_cycles"]

    sent = 0
    errors_count = 0

    blocked_accounts = set()  # 🚫 больше не используем

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

            # ⛔ аккаунт уже исключён
            if acc_name in blocked_accounts:
                continue

            client = TelegramClient(
                f"{sessions_dir}/{acc_name}",
                API_ID,
                API_HASH
            )

            try:
                await client.start()
                sent_from_account = 0

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

                        await client.send_message(dialog.id, message)
                        sent += 1
                        sent_from_account += 1
                        await progress_cb(sent, errors_count)

                        await asyncio.sleep(
                            random.randint(delay_groups, delay_groups + 3)
                        )

                    # 🚫 СПАМ-БЛОК — АККАУНТ УМЕР
                    except errors.PeerFloodError:
                        errors_count += 1
                        blocked_accounts.add(acc_name)

                        await progress_cb(
                            sent,
                            errors_count,
                            f"🚫 СПАМ-БЛОК → {acc_name} (Нужно удалить)"
                        )
                        break

                    # ❄️ ПОЛНАЯ ЗАМОРОЗКА
                    except errors.FloodWaitError:
                        errors_count += 1
                        blocked_accounts.add(acc_name)

                        await progress_cb(
                            sent,
                            errors_count,
                            f"❄️ ЗАМОРОЖЕН → {acc_name} (Нужно удалить)"
                        )
                        break

                    # ❌ нет прав / приват / бан в чате — ПРОСТО СКИП
                    except (
                        errors.ChatWriteForbiddenError,
                        errors.ChannelPrivateError,
                        errors.UserBannedInChannelError
                    ):
                        continue

                    # ⚠️ прочая мелочь
                    except Exception:
                        errors_count += 1
                        await asyncio.sleep(2)

            except Exception:
                errors_count += 1
                blocked_accounts.add(acc_name)

                await progress_cb(
                    sent,
                    errors_count,
                    f"❌ АККАУНТ {acc_name} УПАЛ ПРИ СТАРТЕ (исключён)"
                )

            finally:
                try:
                    await client.disconnect()
                except:
                    pass

        if not stop_flag["stop"]:
            await asyncio.sleep(delay_cycle)

    return sent, errors_count







