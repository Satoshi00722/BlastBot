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
        try:
            session_files = [
                f for f in os.listdir(sessions_dir)
                if f.endswith(".session")
            ]
            random.shuffle(session_files)

            for sess in session_files:
                if stop_flag["stop"]:
                    break

                acc_name = sess.replace(".session", "")

                client = TelegramClient(
                    f"{sessions_dir}/{acc_name}",
                    API_ID,
                    API_HASH
                )

                try:
                    await client.start()

                    dialogs = []
                    async for d in client.iter_dialogs(limit=200):
                        if d.is_group or d.is_channel:
                            dialogs.append(d)

                    random.shuffle(dialogs)
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
                            await progress_cb(sent, errors_count)
                            await asyncio.sleep(delay_groups)

                        # 🚫 СПАМ-БЛОК — СРАЗУ СКИП
                        except errors.PeerFloodError:
                            errors_count += 1
                            await progress_cb(
                                sent,
                                errors_count,
                                f"Аккаунт {acc_name} 🚫 СПАМ-БЛОК"
                            )
                            break

                        # ⏳ FLOOD — ТОЖЕ СКИП
                        except errors.FloodWaitError as e:
                            errors_count += 1
                            await progress_cb(
                                sent,
                                errors_count,
                                f"Аккаунт {acc_name} ⏳ Flood {e.seconds}s"
                            )
                            break

                        # ❌ ЧАТ НЕДОСТУПЕН — ИДЁМ ДАЛЬШЕ
                        except (
                            errors.ChatWriteForbiddenError,
                            errors.ChannelPrivateError,
                            errors.UserBannedInChannelError
                        ):
                            errors_count += 1
                            await progress_cb(sent, errors_count)
                            continue

                        # ⚠️ ПРОЧЕЕ
                        except Exception:
                            errors_count += 1
                            await progress_cb(sent, errors_count)
                            await asyncio.sleep(2)

                except Exception:
                    errors_count += 1
                    await progress_cb(
                        sent,
                        errors_count,
                        f"Аккаунт {acc_name} ❌ Ошибка запуска"
                    )

                finally:
                    try:
                        await client.disconnect()
                    except:
                        pass

            # ⏸ ПАУЗА ПОСЛЕ ВСЕХ АККАУНТОВ
            if not stop_flag["stop"]:
                await asyncio.sleep(delay_cycle)

        except Exception:
            # 💀 защита от смерти воркера
            errors_count += 1
            await progress_cb(sent, errors_count)
            await asyncio.sleep(5)

    return sent, errors_count



