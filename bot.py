from info import SESSION, API_ID, API_HASH, BOT_TOKEN, LOG_STR, LOG_CHANNEL, PORT
from info import MONGO_URL, DATABASE_URI, DATABASE_NAME, COLLECTION_NAME
from utils import temp
from typing import Union, Optional, AsyncGenerator
from pyrogram import types, __version__
from pyrogram import Client
from Script import script 
from datetime import date, datetime 
import pytz
from aiohttp import web
from plugins import web_server
import logging
import os

# Database models / client from your database module
from database.ia_filterdb import db, Media

# Use motor to create/ensure index at startup (only used locally in start)
from motor.motor_asyncio import AsyncIOMotorClient

class Bot(Client):

    def __init__(self):
        super().__init__(
            name=SESSION,
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            workers=150,
            plugins={"root": "plugins"},
            sleep_threshold=5,
        )

    async def start(self):
        # Ensure db is referenced from the database module
        # b_users, b_chats are fetched from the db helper (db should implement get_banned)
        try:
            b_users, b_chats = await db.get_banned()
        except Exception as e:
            logging.exception("Failed to get banned lists from db: %s", e)
            b_users, b_chats = [], []

        temp.BANNED_USERS = b_users
        temp.BANNED_CHATS = b_chats

        await super().start()

        # If your Document class has ensure_indexes, keep this; otherwise it's safe to ignore
        try:
            await Media.ensure_indexes()
        except Exception:
            # Some ORMs expose different index methods; ignore if not present
            pass

        # Ensure text index on the collection (run once; safe to call every start)
        try:
            mongo_url = MONGO_URL or DATABASE_URI or os.environ.get("MONGO_URL") or os.environ.get("DATABASE_URI")
            if not mongo_url:
                logging.warning("No MONGO_URL/DATABASE_URI provided — skipping index creation.")
            else:
                # Create a short-lived motor client for index creation (re-using existing client is fine if available)
                client = AsyncIOMotorClient(mongo_url, maxPoolSize=100)
                db_col = client[DATABASE_NAME][COLLECTION_NAME]
                # Text index on file_name and caption. Weights give higher importance to file_name.
                await db_col.create_index(
                    [("file_name", "text"), ("caption", "text")],
                    default_language="english",
                    weights={"file_name": 5, "caption": 1},
                    background=True,
                    name="file_name_caption_text_idx"
                )
                logging.info("Ensured text index on %s.%s", DATABASE_NAME, COLLECTION_NAME)
                try:
                    client.close()
                except Exception:
                    pass
        except Exception as e:
            logging.exception("Failed to create/ensure text index: %s", e)

        me = await self.get_me()
        temp.ME = me.id
        temp.U_NAME = me.username
        temp.B_NAME = me.first_name
        self.username = '@' + me.username
        # Avoid referencing an undefined `layer` variable in logs
        logging.info(f"{me.first_name} started on {me.username} (Pyrogram {__version__})")
        logging.info(LOG_STR)
        tz = pytz.timezone('Asia/Kolkata')
        today = date.today()
        now = datetime.now(tz)
        time = now.strftime("%H:%M:%S %p")
        await self.send_message(chat_id=LOG_CHANNEL, text=script.RESTART_TXT.format(today, time))
        app = web.AppRunner(await web_server())
        await app.setup()
        bind_address = "0.0.0.0"
        await web.TCPSite(app, bind_address, PORT).start()

    async def stop(self, *args):
        await super().stop()
        logging.info("Bot stopped. Bye.")
    
    async def iter_messages(
        self,
        chat_id: Union[int, str],
        limit: int,
        offset: int = 0,
    ) -> Optional[AsyncGenerator["types.Message", None]]:
        current = offset
        while True:
            new_diff = min(200, limit - current)
            if new_diff <= 0:
                return
            messages = await self.get_messages(chat_id, list(range(current, current+new_diff+1)))
            for message in messages:
                yield message
                current += 1




app = Bot()
app.run()
