import asyncio
import base64
import logging

from pyrogram import Client, filters, StopPropagation, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

from Script import script
from info import AUTH_CHANNEL, AUTH_CHANNEL_LINK, AUTH_GROUPS, BATCH_FILE_CAPTION, FILE_CHANNEL, PROTECT_CONTENT
from utils import get_size, is_subscribed, get_settings
from database.ia_filterdb import get_file_details

logger = logging.getLogger(__name__)


def _auth_link():
    if AUTH_CHANNEL_LINK:
        return AUTH_CHANNEL_LINK
    if isinstance(AUTH_CHANNEL, str) and AUTH_CHANNEL.startswith("@"):
        return f"https://t.me/{AUTH_CHANNEL[1:]}"
    return "https://t.me"


async def _deliver_to_file_channel(client, file_id, title, caption, protect=False):
    if not FILE_CHANNEL:
        raise RuntimeError("FILE_CHANNEL is not configured")
    return await client.send_cached_media(
        chat_id=FILE_CHANNEL,
        file_id=file_id,
        caption=caption or title or "",
        protect_content=protect,
    )


@Client.on_callback_query(filters.regex(r"^(?:filep?|files_?)#"), group=-1)
async def file_delivery_callback(client, query):
    """Intercept file buttons and always deliver the file to FILE_CHANNEL."""
    clicked = query.from_user.id
    try:
        typed = query.message.reply_to_message.from_user.id
    except Exception:
        typed = clicked

    if clicked != typed:
        await query.answer(
            f"അല്ലയോ {query.from_user.first_name}... സ്വന്തമായി റിക്യുസ്റ്റ് ചെയ്ത ഫയൽ മാത്രം തിരഞ്ഞെടുക്കുക 🤒",
            show_alert=True,
        )
        raise StopPropagation

    if AUTH_CHANNEL:
        try:
            subscribed = await is_subscribed(client, query)
        except Exception:
            subscribed = False
        if not subscribed:
            await query.answer("ആദ്യം Updates Channel join ചെയ്യുക. File Bot PM-ൽ അയക്കില്ല.", show_alert=True)
            try:
                await query.message.reply_text(
                    "**Please Join My Updates Channel to get the requested file.**",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🤖 Join Updates Channel", url=_auth_link())]
                    ]),
                )
            except Exception:
                pass
            raise StopPropagation

    try:
        ident, file_id = query.data.split("#", 1)
        files_ = await get_file_details(file_id)
        if not files_:
            await query.answer("No such file exist.", show_alert=True)
            raise StopPropagation

        file = files_[0]
        title = file.file_name or "Requested File"
        size = get_size(file.file_size)
        channel_caption = script.CHANNEL_CAP.format(
            query.from_user.mention,
            title,
            query.message.chat.title,
        )

        file_send = await _deliver_to_file_channel(
            client,
            file_id,
            title,
            channel_caption,
            protect=ident == "filep",
        )

        notice = await query.message.reply_text(
            script.FILE_MSG.format(query.from_user.mention, title, size, ""),
            parse_mode=enums.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 ᴍᴏᴠɪᴇ ᴅᴏᴡɴʟᴏᴀᴅ ʟɪɴᴋ 📥", url=file_send.link)]
            ]),
        )
        await query.answer("File FILE_CHANNEL-ൽ upload ചെയ്തു. താഴെയുള്ള button അമർത്തി file എടുക്കാം.", show_alert=True)

        # Only the temporary group notification is deleted. The channel file stays.
        try:
            settings = await get_settings(query.message.chat.id)
            if settings.get("auto_delete"):
                await asyncio.sleep(200)
                await notice.delete()
        except Exception:
            pass

    except FloodWait as e:
        await asyncio.sleep(e.x)
        await query.answer("Please wait a moment and try again.", show_alert=True)
    except Exception as e:
        logger.exception("FILE_CHANNEL delivery failed: %s", e)
        await query.answer(
            "File channel-ലേക്ക് file അയക്കാൻ കഴിഞ്ഞില്ല. Bot-ന് FILE_CHANNEL-ൽ admin permission ഉണ്ടോ എന്ന് പരിശോധിക്കുക.",
            show_alert=True,
        )
    raise StopPropagation


@Client.on_message(
    filters.command("start") & filters.incoming & filters.regex(r"^/start\s+DSTORE-"),
    group=-1,
)
async def dstore_to_file_channel(client, message):
    """Route DSTORE batch content to FILE_CHANNEL instead of the chat/PM."""
    try:
        data = message.command[1]
        encoded = data.split("-", 1)[1]
        decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("ascii")
        try:
            f_msg_id, l_msg_id, f_chat_id, protect = decoded.split("_", 3)
        except ValueError:
            f_msg_id, l_msg_id, f_chat_id = decoded.split("_", 2)
            protect = "/pbatch" if PROTECT_CONTENT else "batch"

        status = await message.reply("Please wait… Files are being moved to FILE_CHANNEL.")
        links = []

        async for msg in client.iter_messages(int(f_chat_id), int(l_msg_id), int(f_msg_id)):
            if msg.empty:
                continue
            try:
                if msg.media:
                    media_attr = msg.media.value if hasattr(msg.media, "value") else str(msg.media)
                    media = getattr(msg, media_attr, None)
                    if media is None:
                        continue
                    if BATCH_FILE_CAPTION:
                        try:
                            caption = BATCH_FILE_CAPTION.format(
                                file_name=getattr(media, "file_name", ""),
                                file_size=getattr(media, "file_size", ""),
                                file_caption=getattr(msg, "caption", ""),
                            )
                        except Exception:
                            caption = getattr(msg, "caption", "") or getattr(media, "file_name", "")
                    else:
                        caption = getattr(msg, "caption", "") or getattr(media, "file_name", "")
                else:
                    copied = await msg.copy(FILE_CHANNEL, protect_content=protect == "/pbatch")
                    if copied and getattr(copied, "link", None):
                        links.append(copied.link)
                    await asyncio.sleep(1)
                    continue

                copied = await msg.copy(
                    FILE_CHANNEL,
                    caption=caption,
                    protect_content=protect == "/pbatch",
                )
                if copied and getattr(copied, "link", None):
                    links.append(copied.link)
            except FloodWait as e:
                await asyncio.sleep(e.x)
                try:
                    copied = await msg.copy(FILE_CHANNEL, protect_content=protect == "/pbatch")
                    if copied and getattr(copied, "link", None):
                        links.append(copied.link)
                except Exception:
                    logger.exception("DSTORE retry failed")
            except Exception:
                logger.exception("DSTORE message delivery failed")
            await asyncio.sleep(1)

        if links:
            buttons = [[InlineKeyboardButton(f"📥 File {i + 1}", url=link)] for i, link in enumerate(links)]
            await message.reply_text(
                "✅ Files are ready in FILE_CHANNEL.\n\n👇 Download from the buttons below:",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        else:
            await message.reply_text("No files could be moved to FILE_CHANNEL.")
        await status.delete()
    except Exception as e:
        logger.exception("DSTORE delivery failed: %s", e)
        try:
            await message.reply_text("DSTORE delivery failed. Check FILE_CHANNEL admin permissions.")
        except Exception:
            pass
    raise StopPropagation
