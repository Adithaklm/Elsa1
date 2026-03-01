import re
import logging
from pyrogram import Client, filters

from database.channel_sync_db import channel_sync_db
from database.ia_filterdb import save_file
from info import CHANNELS, FILE_INFO_CHANNEL
from utils import get_size

media_filter = filters.document | filters.video | filters.audio

LANGUAGE_PATTERNS = [
    r"malayalam", r"mal", r"tamil", r"tam", r"telugu", r"tel", r"hindi", r"hin", r"english", r"eng",
    r"kannada", r"kan", r"bengali", r"ben", r"marathi", r"gujarati", r"punjabi", r"urdu", r"arabic",
    r"french", r"spanish", r"korean", r"japanese", r"chinese", r"dual[ ._-]?audio", r"multi[ ._-]?audio",
]
LANGUAGE_REGEX = re.compile(rf"(?<![a-z0-9])({'|'.join(LANGUAGE_PATTERNS)})(?![a-z0-9])", re.IGNORECASE)
LANGUAGE_MAP = {
    "mal": "Malayalam",
    "tam": "Tamil",
    "tel": "Telugu",
    "hin": "Hindi",
    "eng": "English",
    "kan": "Kannada",
    "ben": "Bengali",
}


def detect_language(file_name: str) -> str:
    if not file_name:
        return "Unknown"

    matched_languages = []
    for match in LANGUAGE_REGEX.findall(file_name.lower()):
        key = match.lower()
        normalized = LANGUAGE_MAP.get(key, key)
        matched_languages.append(normalized.capitalize())

    if not matched_languages:
        return "Unknown"

    deduplicated = []
    for item in matched_languages:
        if item not in deduplicated:
            deduplicated.append(item)

    return ", ".join(deduplicated)

@Client.on_message(filters.chat(CHANNELS) & media_filter)
async def media(bot, message):
    """Media Handler"""
    for file_type in ("document", "video", "audio"):
        media = getattr(message, file_type, None)
        if media is not None:
            break
    else:
        return

    media.file_type = file_type
    media.caption = message.caption
    await save_file(media)
    
    if not FILE_INFO_CHANNEL:
        return

    file_name = media.file_name or "Unknown"
    file_size = get_size(media.file_size or 0)
    language = detect_language(file_name)

    info_text = (
        "<b>📁 New File Added</b>\n\n"
        f"<b>File Name:</b> <code>{file_name}</code>\n"
        f"<b>Language:</b> {language}\n"
        f"<b>Size:</b> {file_size}\n"
        f"<b>Type:</b> {file_type.title()}"
    )

    await bot.send_message(FILE_INFO_CHANNEL, info_text)

    await channel_sync_db.add_synced_file(
        source_chat_id=message.chat.id,
        source_message_id=message.id,
        destination_chat_id=FILE_INFO_CHANNEL,
        file_name=file_name,
        language=language,
        file_size=file_size,
        file_type=file_type,
    )
