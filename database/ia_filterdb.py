"""
database/ia_filterdb.py — v7

Database layer used by the bot. Responsibilities:
- motor client + umongo Instance
- Media umongo Document
- save_file(media)
- unpack_new_file_id(...)
- get_file_details(...)
- get_search_results(...) with $text primary and per-token regex fallback
- get_bad_files(...)

Notes:
- Requires DATABASE_URI, DATABASE_NAME, COLLECTION_NAME, USE_CAPTION_FILTER in info.py
- Designed to be tolerant across different umongo versions.
"""
from typing import Tuple, Optional, Dict, Any, List
import re
import logging
import difflib

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError
from umongo import Instance, Document, fields

from info import DATABASE_URI, DATABASE_NAME, COLLECTION_NAME, USE_CAPTION_FILTER

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------
def fuzzy_filter(query: str, file_list: List[Any], n: int = 5, cutoff: float = 0.7) -> List[Any]:
    names = [f.file_name for f in file_list]
    close = difflib.get_close_matches(query, names, n=n, cutoff=cutoff)
    return [f for f in file_list if f.file_name in close]

def keyword_score(query: str, file_name: str) -> int:
    words = query.lower().split()
    name = file_name.lower()
    return sum(1 for w in words if w in name)

def normalize(text: Optional[str]) -> str:
    return re.sub(r'[^a-zA-Z0-9 ]', '', (text or "").lower().strip())

# ---------------------------------------------------------------------
# Mongo client + umongo instance
# ---------------------------------------------------------------------
if not DATABASE_URI:
    raise RuntimeError("DATABASE_URI not set in info.py")

client = AsyncIOMotorClient(DATABASE_URI)
db = client[DATABASE_NAME]
instance = Instance.from_db(db)

# ---------------------------------------------------------------------
# Media Document
# ---------------------------------------------------------------------
@instance.register
class Media(Document):
    file_id = fields.StrField(attribute='_id')
    file_ref = fields.StrField(allow_none=True)
    file_name = fields.StrField(required=True)
    file_size = fields.IntField(required=True)
    file_type = fields.StrField(allow_none=True)
    mime_type = fields.StrField(allow_none=True)
    caption = fields.StrField(allow_none=True)

    class Meta:
        collection_name = COLLECTION_NAME

# ---------------------------------------------------------------------
# Save file
# ---------------------------------------------------------------------
async def save_file(media) -> Tuple[bool, int]:
    """
    Save a media object into DB.
    Returns (success_bool, status_code):
      1 => inserted, 0 => duplicate, 2 => validation/other error
    """
    fid, fref = unpack_new_file_id(media.file_id)
    file_name = re.sub(r"[_\-\+\.\(\)\|]", " ", str(media.file_name))
    try:
        doc = Media(
            file_id=fid,
            file_ref=fref,
            file_name=file_name,
            file_size=media.file_size,
            file_type=media.file_type,
            mime_type=media.mime_type,
            caption=media.caption.html if getattr(media, "caption", None) else None,
        )
    except Exception:
        logger.exception("Error while constructing Media document")
        return False, 2

    try:
        await doc.commit()
    except DuplicateKeyError:
        logger.warning("%s is already saved in database", getattr(media, "file_name", "NO_FILE"))
        return False, 0
    except Exception:
        logger.exception("Error while committing Media to DB")
        return False, 2

    logger.info("%s saved to database", getattr(media, "file_name", "NO_FILE"))
    return True, 1

# ---------------------------------------------------------------------
# Helpers used by other modules
# ---------------------------------------------------------------------
# Project may provide its own unpack_new_file_id; if so try to use it.
try:
    from utils import unpack_new_file_id as project_unpack_new_file_id
except Exception:
    project_unpack_new_file_id = None

def unpack_new_file_id(file_id: Any) -> Tuple[Optional[str], Optional[str]]:
    """
    Normalize file_id input into (file_id_str, file_ref_str).
    Uses project-provided implementation if available, otherwise a permissive fallback.
    """
    if project_unpack_new_file_id:
        try:
            return project_unpack_new_file_id(file_id)
        except Exception:
            # fall back to local behavior if project's function fails
            logger.exception("project unpack_new_file_id raised; falling back to builtin")

    if not file_id:
        return None, None

    if isinstance(file_id, (list, tuple)) and len(file_id) >= 1:
        fid = str(file_id[0]) if file_id[0] is not None else None
        ref = str(file_id[1]) if len(file_id) > 1 and file_id[1] is not None else None
        return fid, ref

    try:
        if hasattr(file_id, "get"):
            fid = file_id.get("file_id") or file_id.get("_id") or file_id.get("id")
            if fid:
                return str(fid), None
    except Exception:
        pass

    return str(file_id), None

async def get_file_details(file_identifier: Any) -> Optional[Dict[str, Any]]:
    """
    Return plain dict with stored file details for given identifier.
    Plugins may expect a dict with keys: file_id, file_ref, file_name, file_size, file_type, mime_type, caption
    """
    fid, _ = unpack_new_file_id(file_identifier)
    if not fid:
        return None

    # prefer lookup by _id (stored as _id)
    doc = await db[COLLECTION_NAME].find_one({"_id": fid})
    if not doc:
        doc = await db[COLLECTION_NAME].find_one({"file_id": fid})
    if not doc:
        return None

    return {
        "file_id": doc.get("_id") or doc.get("file_id"),
        "file_ref": doc.get("file_ref"),
        "file_name": doc.get("file_name"),
        "file_size": doc.get("file_size"),
        "file_type": doc.get("file_type"),
        "mime_type": doc.get("mime_type"),
        "caption": doc.get("caption"),
    }

# ---------------------------------------------------------------------
# Search functions
# ---------------------------------------------------------------------
async def get_search_results(query: str, file_type: Optional[str] = None,
                             max_results: int = 6, offset: int = 0, filter: bool = False):
    """
    Hybrid search:
    - Use $text (text index) for multi-word or longer queries (fast)
    - If $text yields no hits, fallback to per-token regex ($and) to improve accuracy
    - For short single-token queries use a word-boundary regex on file_name
    Returns: (files, next_offset, total_results)
      files: list of umongo Media documents
      next_offset: '' or int
      total_results: int or None (if counting failed)
    """
    query = (query or "").strip()
    projection = {"file_name": 1, "file_id": 1, "file_size": 1, "file_type": 1, "caption": 1}

    tokens = [t for t in re.split(r'\s+', query) if t]
    mongo_filter = {}

    # decide primary strategy
    use_text_search = False
    if not query:
        mongo_filter = {}
    elif " " in query or len(query) > 2:
        use_text_search = True
        mongo_filter = {"$text": {"$search": query}}
    else:
        # single short token -> word-boundary regex
        raw_pattern = r'(\b|[\.\+\-_])' + re.escape(query) + r'(\b|[\.\+\-_])'
        try:
            regex = re.compile(raw_pattern, flags=re.IGNORECASE)
        except Exception:
            logger.exception("Invalid regex for single-token search")
            return [], '', 0
        mongo_filter = {"file_name": regex}

    if file_type:
        if mongo_filter:
            mongo_filter = {"$and": [mongo_filter, {"file_type": file_type}]}
        else:
            mongo_filter = {"file_type": file_type}

    async def run_and_paginate(filter_q):
        total = None
        try:
            total = await db[COLLECTION_NAME].count_documents(filter_q)
        except Exception:
            logger.exception("count_documents failed for filter: %s", filter_q)
            total = None

        cursor = Media.find(filter_q, projection)
        cursor.sort("_id", -1)
        docs = await cursor.skip(offset).limit(max_results + 1).to_list(length=max_results + 1)

        if len(docs) <= max_results:
            return docs, '', total
        return docs[:max_results], offset + max_results, total

    # Try text search first when applicable
    if use_text_search:
        try:
            files, next_offset, total = await run_and_paginate(mongo_filter)
        except Exception as e:
            logger.exception("Text search error: %s", e)
            files, next_offset, total = [], '', None

        if files:
            return files, next_offset, total

        # Fallback: build per-token AND of regex conditions (match all tokens anywhere)
        and_conditions = []
        for tok in tokens:
            tok_regex = re.compile(r'\b' + re.escape(tok) + r'\b', flags=re.IGNORECASE)
            if USE_CAPTION_FILTER:
                and_conditions.append({"$or": [{"file_name": tok_regex}, {"caption": tok_regex}]})
            else:
                and_conditions.append({"file_name": tok_regex})

        if file_type:
            and_conditions.append({"file_type": file_type})

        fallback_filter = {"$and": and_conditions} if and_conditions else {}
        return await run_and_paginate(fallback_filter)

    # Not using text search (single token case)
    return await run_and_paginate(mongo_filter)

async def get_bad_files(query: str, file_type: Optional[str] = None,
                        max_results: int = 100, offset: int = 0, filter: bool = False):
    """
    Used by admin tools: broader search.
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
