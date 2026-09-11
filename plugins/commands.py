import os
import logging
import random
import asyncio
from Script import script
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from database.ia_filterdb import Media, get_file_details, unpack_new_file_id
from info import CHANNELS, ADMINS, AUTH_CHANNEL, AUTH_CHANNEL_LINK, AUTH_GROUPS, LOG_CHANNEL, PICS, BATCH_FILE_CAPTION, CUSTOM_FILE_CAPTION, PROTECT_CONTENT, MSG_ALRT, MAIN_CHANNEL, FILE_CHANNEL, MOVIE_WEB_URL
from utils import get_settings, get_size, is_subscribed, save_group_settings, temp
from database.connections_mdb import active_connection
import re
import json
import base64
logger = logging.getLogger(__name__)

BATCH_FILES = {}

def get_file_send_chat(message):
    """Return the group/channel where files should always be sent."""
    return FILE_CHANNEL or message.chat.id

def get_auth_channel_link():
    if AUTH_CHANNEL_LINK:
        return AUTH_CHANNEL_LINK
    if isinstance(AUTH_CHANNEL, str) and AUTH_CHANNEL.startswith("@"):
        return f"https://t.me/{AUTH_CHANNEL[1:]}"
    return "https://t.me"


def is_batch_payload(payload):
    return payload.startswith("BATCH-") or payload.startswith("DSTORE-")
    
@Client.on_message(filters.command("start") & filters.incoming)
async def start(client, message):
    if message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        buttons = [
            [InlineKeyboardButton('🎬 Movie Portal', url=MOVIE_WEB_URL)] if MOVIE_WEB_URL else [],
            [InlineKeyboardButton('🤖 Updates', url=(MAIN_CHANNEL))],
            [InlineKeyboardButton('ʜᴇʟᴘ', url=f"https://t.me/{temp.U_NAME}?start=help")]
            ]
        buttons = [row for row in buttons if row]
        reply_markup = InlineKeyboardMarkup(buttons)
        await message.reply(script.START_TXT.format(message.from_user.mention if message.from_user else message.chat.title, temp.U_NAME, temp.B_NAME), reply_markup=reply_markup)
        await asyncio.sleep(2)
        if not await db.get_chat(message.chat.id):
            total=await client.get_chat_members_count(message.chat.id)
            await client.send_message(LOG_CHANNEL, script.LOG_TEXT_G.format(message.chat.title, message.chat.id, total, "Unknown"))       
            await db.add_chat(message.chat.id, message.chat.title)
        return 
    if not await db.is_user_exist(message.from_user.id):
        await db.add_user(message.from_user.id, message.from_user.first_name)
        await client.send_message(LOG_CHANNEL, script.LOG_TEXT_P.format(message.from_user.id, message.from_user.mention))
    if len(message.command) != 2:
        buttons = [[InlineKeyboardButton('💢 Cʟɪᴄᴋ Tᴏ Vɪᴇᴡ Mᴏʀᴇ Bᴜᴛᴛᴏɴs 💢', callback_data='start')]]
        if MOVIE_WEB_URL:
            buttons.insert(0, [InlineKeyboardButton('🎬 Movie Portal', url=MOVIE_WEB_URL)])
        reply_markup = InlineKeyboardMarkup(buttons)
        await message.reply_photo(
            photo=random.choice(PICS),
            caption=script.SUR_TXT.format(message.from_user.mention, temp.U_NAME, temp.B_NAME),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        return
    start_payload = message.command[1] if len(message.command) > 1 else "subscribe"
    force_sub_chat = get_force_sub_chat()
    should_force_sub = force_sub_chat and is_batch_payload(start_payload)
    if should_force_sub and not await is_user_in_force_sub_chat(client, message.from_user.id):         
        btn = [[InlineKeyboardButton("🤖 Join Updates Channel", url=get_auth_channel_link())]]
        if start_payload != "subscribe":
            try:
                kk, file_id = start_payload.split("_", 1)
                pre = 'checksubp' if kk == 'filep' else 'checksub' 
                btn.append([InlineKeyboardButton(" 🔄 Try Again", callback_data=f"{pre}#{file_id}")])
            except (IndexError, ValueError):
                btn.append([InlineKeyboardButton(" 🔄 Try Again", url=f"https://t.me/{temp.U_NAME}?start={start_payload}")])
        await client.send_message(chat_id=message.from_user.id, text="**Please Join My Updates Channel to use this Bot!**", reply_markup=InlineKeyboardMarkup(btn), parse_mode=enums.ParseMode.MARKDOWN)
        return
    if len(message.command) == 2 and message.command[1] in ["subscribe", "error", "okay", "help"]:
        buttons = [[InlineKeyboardButton('sᴜʀᴘʀɪsᴇ', callback_data='start')]]
        if MOVIE_WEB_URL:
            buttons.insert(0, [InlineKeyboardButton('🎬 Movie Portal', url=MOVIE_WEB_URL)])
        reply_markup = InlineKeyboardMarkup(buttons)
        await message.reply_photo(photo=random.choice(PICS), caption=script.SUR_TXT.format(message.from_user.mention, temp.U_NAME, temp.B_NAME), reply_markup=reply_markup, parse_mode=enums.ParseMode.HTML)
        return
    data = message.command[1]
    try:
        pre, file_id = data.split('_', 1)
    except:
        file_id = data
        pre = ""
    if data.split("-", 1)[0] == "BATCH":
        sts = await message.reply("Please wait")
        file_id = data.split("-", 1)[1]
        msgs = BATCH_FILES.get(file_id)
        if not msgs:
            file = await client.download_media(file_id)
            try: 
                with open(file) as file_data:
                    msgs=json.loads(file_data.read())
            except:
                await sts.edit("FAILED")
                return await client.send_message(LOG_CHANNEL, "UNABLE TO OPEN FILE.")
            os.remove(file)
            BATCH_FILES[file_id] = msgs
        for msg in msgs:
            title = msg.get("title")
            size=get_size(int(msg.get("size", 0)))
            f_caption=msg.get("caption", "")
            if BATCH_FILE_CAPTION:
                try:
                    f_caption=BATCH_FILE_CAPTION.format(file_name= '' if title is None else title, file_size='' if size is None else size, file_caption='' if f_caption is None else f_caption)
                except Exception as e:
                    logger.exception(e)
                    f_caption=f_caption
            if f_caption is None:
                f_caption = f"{title}"
            try:
                await client.send_cached_media(chat_id=get_file_send_chat(message), file_id=msg.get("file_id"), caption=f_caption, protect_content=msg.get('protect', False))
            except FloodWait as e:
                await asyncio.sleep(e.x)
                logger.warning(f"Floodwait of {e.x} sec.")
                await client.send_cached_media(chat_id=get_file_send_chat(message), file_id=msg.get("file_id"), caption=f_caption, protect_content=msg.get('protect', False))
            except Exception as e:
                logger.exception(e)
        await sts.delete()
