import time
import random
from pyrogram import Client, filters
import re, asyncio, time, shutil, psutil, os, sys
from pyrogram import Client, filters, enums
from pyrogram.types import *
from info import BOT_START_TIME, ADMINS
from utils import humanbytes  

CMD = ["/", "."]
STICKER_ID = os.environ.get("STICKER_ID", "CAACAgUAAxkBAAIC_mMfiLMHcujIwep30yDH0YEjhf1uAALUBgACFzr4VE9CLiQ3A-zIHgQ CAACAgUAAxkBAAIDBGMfiXHV4IPsvx7TKsiVpIggaOwdAAL5BgAC5Fn4VG7lfz8eWypXHgQ CAACAgUAAxkBAAIDImMgnVFgBKwXkm4xGObhCycAAeNc7QACywUAAnl3CVUOO6Wry-yNFx4E CAACAgUAAxkBAAIDIWMgnVcprgEdxWWr-V9tkcFUc0B_AAKkBwAC9wsIVc5wbdEElriYHgQ CAACAgUAAxkBAAIDIGMgnVsrATRlJ8DTJJAwwR-pYnewAAIGBwACjVEIVQZoTRFaacGbHgQ CAACAgUAAxkBAAIDH2MgnWEd2u3vGw7p9yvwG2bLbY0fAAJWCAACITQJVUb9hJI1XOVnHgQ CAACAgUAAxkBAAIDHmMgnWZt0rTrSalQmFScRMOW9inmAAIiCAACLewAAVWxzIx3gQ8u0B4E").split()


@Client.on_message(filters.private & filters.command("adith") & filters.user(ADMINS))          
async def stats(bot, update):
    currentTime = time.strftime("%Dd%Hh%Mm%Ss", time.gmtime(time.time() - BOT_START_TIME))
    total, used, free = shutil.disk_usage(".")
    total = humanbytes(total)
    used = humanbytes(used)
    free = humanbytes(free)
    cpu_usage = psutil.cpu_percent()
    ram_usage = psutil.virtual_memory().percent
    disk_usage = psutil.disk_usage('/').percent

    ms_g = f"""<b><u>Bot Status</b></u>
Uptime: <code>{currentTime}</code>
CPU Usage: <code>{cpu_usage}%</code>
RAM Usage: <code>{ram_usage}%</code>
Total Disk Space: <code>{total}</code>
Used Space: <code>{used} ({disk_usage}%)</code>
Free Space: <code>{free}</code> """

@Client.on_message(filters.command("alive", CMD))
async def check_alive(_, message):
    await message.reply_text("ചത്തിട്ടില്ല മുത്തേ ഇവിടെ തന്നെ ഉണ്ട്.. നിനക്ക് ഇപ്പൊ എന്നോട് ഒരു സ്നേഹവും ഇല്ല. കൊള്ളാം.. നീ പാഴെ പോലെയേ അല്ല മാറിപോയി..😔 ഇടക്ക് എങ്കിലും ചുമ്മാ ഒന്ന് /start ചെയ്തു നോക്ക്..🙂🥰")


@Client.on_message(filters.command("ping", CMD))
async def ping(_, message):
    start_t = time.time()
    h=await message.reply_sticker(sticker=random.choice(STICKER_ID))
    rm = await message.reply_text("..")
    end_t = time.time()
    time_taken_s = (end_t - start_t) * 1000
    currentTime = time.strftime("%d Day | %H Hour | %M Min | %S Sec", time.gmtime(time.time() - BOT_START_TIME))
    m=await rm.edit(f"🏓 <b>ᴘɪɴɢ</b> : <code>{time_taken_s:.3f} ms</code>\n\n⏰<b> ᴜᴘᴛɪᴍᴇ :  </b><code>{currentTime}</code>")
    await asyncio.sleep(3)
    await message.delete()
    await h.delete()
    await m.delete()
