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
# Use users_chats_db.Database (wrapper with get_banned, get_settings, etc.)
from database.users_chats_db import Database as UsersDatabase
# Media Document & underlying DB are still from ia_filterdb where appropriate
from database.ia_filterdb import Media

# Use motor to create/ensure index at startup (only used locally in start)
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import OperationFailure

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
        # instantiate the users/groups DB wrapper here so other code can use it
        mongo_url = MONGO_URL or DATABASE_URI or os.environ.get("MONGO_URL") or os.environ.get("DATABASE_URI")
        if mongo_url:
            # UsersDatabase expects (uri, database_name)
            try:
                self.userdb = UsersDatabase(mongo_url, DATABASE_NAME)
            except Exception:
                # fallback: keep attribute but it may raise later if used
                logging.exception("Failed to instantiate UsersDatabase wrapper.")
                self.userdb = None
        else:
            logging.warning("No MONGO_URL/DATABASE_URI provided; userdb not instantiated.")
            self.userdb = None

    async def start(self):
        # Use userdb.get_banned() (Database wrapper) instead of calling a Motor collection
        try:
            if self.userdb:
                b_users, b_chats = await self.userdb.get_banned()
            else:
                b_users, b_chats = [], []
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
                client = AsyncIOMotorClient(mongo_url, maxPoolSize=100)
                db_col = client[DATABASE_NAME][COLLECTION_NAME]
                try:
                    # Try create the text index. If an equivalent index exists with different options,
                    # catch OperationFailure and log instead of crashing.
                    await db_col.create_index(
                        [("file_name", "text"), ("caption", "text")],
                        default_language="english",
                        weights={"file_name": 5, "caption": 1},
                        background=True,
                        name="file_name_caption_text_idx"
                    )
                    logging.info("Ensured text index on %s.%s", DATABASE_NAME, COLLECTION_NAME)
                except OperationFailure as opf:
                    # IndexOptionsConflict (code 85) occurs when an equivalent text index exists with different name/options.
                    # Log and continue (do not abort startup).
                    logging.warning("Could not create text index (may already exist with different options): %s", opf)
                finally:
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
