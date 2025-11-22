"""
Database layer for file indexing/search.

This file defines:
- Mongo/Motor client and umongo Instance
- Media Document model
- save_file(media) to save a file document
- get_search_results(...) used by the bot (returns files, next_offset, total_results)
- get_bad_files(...) similar helper
- get_file_details(...) helper used by plugins/commands.py
- unpack_new_file_id(...) helper used across the project

Notes:
- Requires DATABASE_URI, DATABASE_NAME, COLLECTION_NAME in info.py
- get_search_results uses $text when appropriate and falls back to regex
- Counting documents is still used because other parts of the bot expect total_results
"""
import re
import logging
import difflib
from typing import Tuple, Optional, Dict, Any

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError
from umongo import Instance, Document, fields

from info import DATABASE_URI, DATABASE_NAME, COLLECTION_NAME, USE_CAPTION_FILTER

# Import helper if present in project, otherwise fallback to local stub
try:
    from utils import unpack_new_file_id as project_unpack_new_file_id
except Exception:
    project_unpack_new_file_id = None

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Utility helpers

def fuzzy_filter(query, file_list, n=5, cutoff=0.7):
    names = [f.file_name for f in file_list]
    close = difflib.get_close_matches(query, names, n=n, cutoff=cutoff)
    return [f for f in file_list if f.file_name in close]

def keyword_score(query, file_name):
    words = query.lower().split()
    name = file_name.lower()
    return sum(1 for w in words if w in name)

def normalize(text: Optional[str]) -> str:
    return re.sub(r'[^a-zA-Z0-9 ]', '', (text or "").lower().strip())

# Mongo client + umongo instance
if not DATABASE_URI:
    raise RuntimeError("DATABASE_URI is not set in info.py")

client = AsyncIOMotorClient(DATABASE_URI)
db = client[DATABASE_NAME]
instance = Instance.from_db(db)

@instance.register
class Media(Document):
    # Map file_id to Mongo _id so umongo saves/retrieves as _id in collection
    file_id = fields.StrField(attribute='_id')
    file_ref = fields.StrField(allow_none=True)
    file_name = fields.StrField(required=True)
    file_size = fields.IntField(required=True)
    file_type = fields.StrField(allow_none=True)
    mime_type = fields.StrField(allow_none=True)
    caption = fields.StrField(allow_none=True)

    class Meta:
        collection_name = COLLECTION_NAME


async def save_file(media) -> Tuple[bool, int]:
    """Save a media object into DB. Returns (success_bool, status_code)"""
    file_id, file_ref = unpack_new_file_id(media.file_id)
    file_name = re.sub(r"[_\-\+\.\(\)\|]", " ", str(media.file_name))
    try:
        file = Media(
            file_id=file_id,
            file_ref=file_ref,
            file_name=file_name,
            file_size=media.file_size,
            file_type=media.file_type,
            mime_type=media.mime_type,
            caption=media.caption.html if media.caption else None,
        )
    except Exception:
        logger.exception('Error occurred while creating Media object (validation?), check fields')
        return False, 2
    else:
        try:
            await file.commit()
        except DuplicateKeyError:
            logger.warning(f'{getattr(media, "file_name", "NO_FILE")} is already saved in database')
            return False, 0
        else:
            logger.info(f'{getattr(media, "file_name", "NO_FILE")} is saved to database')
            return True, 1


# Helper used across project to normalize / split compound ids.
def unpack_new_file_id(file_id: Any) -> Tuple[Optional[str], Optional[str]]:
    """
    Normalize an input file identifier into (file_id, file_ref).
    If your project already provides unpack_new_file_id in utils, that function will be used instead.
    This fallback attempts to be permissive:
      - If file_id is a dict-like object, tries to extract keys
      - Otherwise returns the string form and None as file_ref
    """
    if project_unpack_new_file_id:
        # use project-provided implementation if available
        try:
            return project_unpack_new_file_id(file_id)
        except Exception:
            # fall through to our fallback if the project's function errors
            pass

    if not file_id:
        return None, None

    # If it's already a tuple-like return first two items
    if isinstance(file_id, (list, tuple)) and len(file_id) >= 1:
        fid = str(file_id[0]) if file_id[0] is not None else None
        ref = str(file_id[1]) if len(file_id) > 1 and file_id[1] is not None else None
        return fid, ref

    # If a dict-like object
    try:
        if hasattr(file_id, "get"):
            fid = file_id.get("file_id") or file_id.get("_id") or file_id.get("id")
            if fid:
                return str(fid), None
    except Exception:
        pass

    # Otherwise, coerce to string
    return str(file_id), None


async def get_file_details(file_identifier: Any) -> Optional[Dict[str, Any]]:
    """
    Return a plain dict with file details for a given file identifier.
    Accepts whatever unpack_new_file_id accepts (media.file_id, stored id, etc).
    Returns None if not found.
    """
    fid, _ = unpack_new_file_id(file_identifier)
    if not fid:
        return None

    # Try to find by _id (stored as _id), then by file_id field if needed.
    doc = await db[COLLECTION_NAME].find_one({"_id": fid})
    if not doc:
        doc = await db[COLLECTION_NAME].find_one({"file_id": fid})
    if not doc:
        return None

    # Return a simple dict (plugins expect attributes like file_name, file_id, file_size, file_type, caption)
    return {
        "file_id": doc.get("_id") or doc.get("file_id"),
        "file_ref": doc.get("file_ref"),
        "file_name": doc.get("file_name"),
        "file_size": doc.get("file_size"),
        "file_type": doc.get("file_type"),
        "mime_type": doc.get("mime_type"),
        "caption": doc.get("caption"),
    }


async def get_search_results(query, file_type=None, max_results=6, offset=0, filter=False):
    """
    Search the Media collection.

    Returns:
      files: list of Media documents (umongo objects)
      next_offset: '' or next offset int
      total_results: total matching documents (int) or None if counting failed

    Behavior:
    - If query empty => match all
    - If multi-word or len(query) > 2 => use $text (requires a text index on file_name/caption)
    - Else fallback to a narrow regex that acts like word-boundary matching
    - Uses projection to only fetch necessary fields
    - Sorts by _id descending (recent first)
    - Uses limit(max_results + 1) to detect next page
    """
    query = (query or "").strip()

    projection = {
        "file_name": 1,
        "file_id": 1,
        "file_size": 1,
        "file_type": 1,
        "caption": 1,
    }

    # Build mongo filter
    if not query:
        mongo_filter = {}
    elif " " in query or len(query) > 2:
        mongo_filter = {"$text": {"$search": query}}
    else:
        raw_pattern = r'(\b|[\.\+\-_])' + re.escape(query) + r'(\b|[\.\+\-_])'
        try:
            regex = re.compile(raw_pattern, flags=re.IGNORECASE)
        except Exception as e:
            logger.exception("Invalid regex for search query: %s", e)
            return [], '', 0
        mongo_filter = {"file_name": regex}

    if file_type:
        mongo_filter["file_type"] = file_type

    # Count total results (some parts of the bot expect it). This may be slow for regex on big collections.
    total_results = None
    try:
        total_results = await db[COLLECTION_NAME].count_documents(mongo_filter)
    except Exception as e:
        logger.exception("Failed to count documents for search: %s", e)
        total_results = None

    # Use umongo find to get Document objects
    cursor = Media.find(mongo_filter, projection)
    cursor.sort("_id", -1)

    docs = await cursor.skip(offset).limit(max_results + 1).to_list(length=max_results + 1)

    if len(docs) <= max_results:
        next_offset = ''
        files = docs
    else:
        files = docs[:max_results]
        next_offset = offset + max_results

    return files, next_offset, total_results


async def get_bad_files(query, file_type=None, max_results=100, offset=0, filter=False):
    """
    Return "bad files" results with larger max_results (used by admin tooling).
    Uses the same logic for query->regex/text as get_search_results, but returns raw list.
    """
    query = (query or "").strip()

    if not query:
        raw_pattern = '.'
    elif ' ' not in query:
        raw_pattern = r'(\b|[\.\+\-_])' + re.escape(query) + r'(\b|[\.\+\-_])'
    else:
        raw_pattern = query.replace(' ', r'.*[\s\.\+\-_]')

    try:
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except Exception:
        return []

    if USE_CAPTION_FILTER:
        mongo_filter = {'$or': [{'file_name': regex}, {'caption': regex}]}
    else:
        mongo_filter = {'file_name': regex}

    if file_type:
        mongo_filter['file_type'] = file_type

    cursor = Media.find(mongo_filter, {"file_name": 1, "file_id": 1, "file_size": 1, "file_type": 1, "caption": 1})
    cursor.sort("_id", -1)
    files = await cursor.skip(offset).limit(max_results).to_list(length=max_results)
    return files
