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

# Use motor to create/ensure index at startup
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
        # existing startup tasks
        b_users, b_chats = await db.get_banned()
        temp.BANNED_USERS = b_users
        temp.BANNED_CHATS = b_chats
        await super().start()
        await Media.ensure_indexes()

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
                # close client to avoid unused connections (motor doesn't have close() but you can call client.close())
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
        logging.info(f"{me.first_name} with for Pyrogram v{__version__} (Layer {layer}) started on {me.username}.")
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
        """Iterate through a chat sequentially.
        This convenience method does the same as repeatedly calling :meth:`~pyrogram.Client.get_messages` in a loop, thus saving
        you from the hassle of setting up boilerplate code. It is useful for getting the whole chat messages with a
        single call.
        Parameters:
            chat_id (``int`` | ``str``):
                Unique identifier (int) or username (str) of the target chat.
                For your personal cloud (Saved Messages) you can simply use "me" or "self".
                For a contact that exists in your Telegram address book you can use his phone number (str).
                
            limit (``int``):
                Identifier of the last message to be returned.
                
            offset (``int``, *optional*):
                Identifier of the first message to be returned.
                Defaults to 0.
        Returns:
            ``Generator``: A generator yielding :obj:`~pyrogram.types.Message` objects.
        Example:
            .. code-block:: python
                for message in app.iter_messages("pyrogram", 1, 15000):
                    print(message.text)
        """
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
