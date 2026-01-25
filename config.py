import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN")

API_ID = os.getenv("API_ID")
API_ID = int(API_ID) if API_ID else None

API_HASH = os.getenv("API_HASH")

ADMIN_CHANNEL_ID = os.getenv("ADMIN_CHANNEL_ID")
ADMIN_CHANNEL_ID = int(ADMIN_CHANNEL_ID) if ADMIN_CHANNEL_ID else None
