import re
import logging
from pyrogram import Client, filters, enums, StopPropagation
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database.ia_filterdb import get_search_results
from utils import get_size

logger = logging.getLogger(__name__)

# Short-lived in-memory navigation cache. Search results are already in MongoDB;
# this only stores the selected files for the user's current navigation screen.
SERIES_NAV = {}

SEASON_RE = re.compile(r"(?:S(?:EASON)?\s*[-._ ]?)(\d{1,2})", re.I)
EPISODE_RE = re.compile(r"S(\d{1,2})\s*E(\d{1,3})", re.I)


def _season(file_name):
    match = SEASON_RE.search(file_name or "")
    return int(match.group(1)) if match else None


def _episode(file_name):
    match = EPISODE_RE.search(file_name or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _clean_title(name):
    name = re.sub(r"[._-]+", " ", name or "")
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"\bS\d{1,2}(?:E\d{1,3})?\b.*$", "", name, flags=re.I)
    return name.strip(" -–|[]") or "Series"


def _key(user_id, chat_id):
    return f"{user_id}:{chat_id}"


async def _search_all(search):
    """Collect up to 100 matching files, following the normal DB pagination."""
    results = []
    offset = 0
    seen = set()

    for _ in range(10):
        files, next_offset, _total = await get_search_results(search, offset=offset, filter=True)
        if not files:
            break
        for file in files:
            if file.file_id in seen:
                continue
            seen.add(file.file_id)
            results.append(file)
        try:
            next_offset = int(next_offset)
        except Exception:
            break
        if next_offset <= offset:
            break
        offset = next_offset

    return results


def _season_keyboard(seasons, key):
    rows = []
    row = []
    for season in seasons:
        row.append(InlineKeyboardButton(f"Season {season:02d}", callback_data=f"snav:season:{key}:{season}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("✖ Close", callback_data="snav:close")])
    return InlineKeyboardMarkup(rows)


def _episode_keyboard(files, key, season):
    rows = []
    for file in files:
        ep = _episode(file.file_name or "")
        label = f"E{ep[1]:02d}" if ep else "FILE"
        rows.append([
            InlineKeyboardButton(
                f"{label} • {get_size(file.file_size)}",
                callback_data=f"files#{file.file_id}",
            )
        ])
    rows.append([
        InlineKeyboardButton("◀ Seasons", callback_data=f"snav:back:{key}"),
        InlineKeyboardButton("✖ Close", callback_data="snav:close"),
    ])
    return InlineKeyboardMarkup(rows)


@Client.on_message(filters.command("series") & filters.incoming, group=-1)
async def series_command(client, message):
    if len(message.command) < 2:
        await message.reply_text(
            "📺 <b>Series Smart Navigation</b>\n\n"
            "Use: <code>/series Series Name</code>\n\n"
            "Example: <code>/series Breaking Bad</code>",
            parse_mode=enums.ParseMode.HTML,
        )
        raise StopPropagation

    search = " ".join(message.command[1:]).strip()
    status = await message.reply_text("🔎 Finding seasons and episodes…")

    try:
        files = await _search_all(search)
        grouped = {}
        for file in files:
            season = _season(file.file_name or "")
            if season is not None:
                grouped.setdefault(season, []).append(file)

        if not grouped:
            await status.edit_text(
                f"❌ No series episodes found for <b>{search}</b>.",
                parse_mode=enums.ParseMode.HTML,
            )
            raise StopPropagation

        for season in grouped:
            grouped[season].sort(key=lambda f: (_episode(f.file_name or "") or (999, 999))[1])

        key = _key(message.from_user.id if message.from_user else 0, message.chat.id)
        SERIES_NAV[key] = {
            "title": _clean_title(search),
            "search": search,
            "seasons": grouped,
        }

        season_text = "\n".join(
            f"• Season {season:02d} — {len(grouped[season])} episode(s)"
            for season in sorted(grouped)
        )
        await status.edit_text(
            f"📺 <b>{_clean_title(search)}</b>\n\n"
            f"Select a season:\n\n{season_text}",
            reply_markup=_season_keyboard(sorted(grouped), key),
            parse_mode=enums.ParseMode.HTML,
        )
    except StopPropagation:
        raise
    except Exception:
        logger.exception("Series navigation failed")
        await status.edit_text("❌ Series navigation failed. Please try again.")
    raise StopPropagation


@Client.on_callback_query(filters.regex(r"^snav:"), group=-1)
async def series_navigation_callback(client, query):
    data = query.data.split(":")
    action = data[1] if len(data) > 1 else ""

    if action == "close":
        await query.answer()
        try:
            key = _key(query.from_user.id, query.message.chat.id)
            SERIES_NAV.pop(key, None)
        except Exception:
            pass
        await query.message.delete()
        raise StopPropagation

    if len(data) < 3:
        await query.answer("Navigation data expired.", show_alert=True)
        raise StopPropagation

    key = data[2]
    expected = _key(query.from_user.id, query.message.chat.id)
    if key != expected:
        await query.answer("This series menu belongs to another user.", show_alert=True)
        raise StopPropagation

    nav = SERIES_NAV.get(key)
    if not nav:
        await query.answer("Series menu expired. Search again.", show_alert=True)
        raise StopPropagation

    if action == "season":
        if len(data) < 4:
            await query.answer("Invalid season.", show_alert=True)
            raise StopPropagation
        season = int(data[3])
        files = nav["seasons"].get(season, [])
        if not files:
            await query.answer("No episodes found.", show_alert=True)
            raise StopPropagation

        await query.message.edit_text(
            f"📺 <b>{nav['title']}</b> — <b>Season {season:02d}</b>\n\n"
            f"Select an episode:",
            reply_markup=_episode_keyboard(files, key, season),
            parse_mode=enums.ParseMode.HTML,
        )
        await query.answer()
        raise StopPropagation

    if action == "back":
        seasons = sorted(nav["seasons"])
        season_text = "\n".join(
            f"• Season {s:02d} — {len(nav['seasons'][s])} episode(s)" for s in seasons
        )
        await query.message.edit_text(
            f"📺 <b>{nav['title']}</b>\n\nSelect a season:\n\n{season_text}",
            reply_markup=_season_keyboard(seasons, key),
            parse_mode=enums.ParseMode.HTML,
        )
        await query.answer()
        raise StopPropagation

    await query.answer("Unknown navigation action.", show_alert=True)
    raise StopPropagation
