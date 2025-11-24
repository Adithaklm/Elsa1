import logging
from struct import pack
import re
import base64

from pyrogram.file_id import FileId
from pymongo.errors import DuplicateKeyError
from umongo import Instance, Document, fields
from motor.motor_asyncio import AsyncIOMotorClient
from marshmallow.exceptions import ValidationError

from info import DATABASE_URI, DATABASE_NAME, COLLECTION_NAME, USE_CAPTION_FILTER, MAX_BTN

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------
# Mongo / umongo setup
# ---------------------------------------------------------------------

client = AsyncIOMotorClient(DATABASE_URI)
db = client[DATABASE_NAME]
instance = Instance.from_db(db)


@instance.register
class Media(Document):
    # file_id stored as Mongo _id
    file_id = fields.StrField(attribute="_id")
    file_ref = fields.StrField(allow_none=True)
    file_name = fields.StrField(required=True)
    file_size = fields.IntField(required=True)
    file_type = fields.StrField(allow_none=True)
    mime_type = fields.StrField(allow_none=True)
    caption = fields.StrField(allow_none=True)

    class Meta:
        # umongo text index + compound index
        indexes = (
            ("$file_name",),  # text index on file_name (umongo shorthand)
            (("file_name", 1), ("caption", 1)),
        )
        collection_name = COLLECTION_NAME


async def ensure_extra_indexes():
    """
    Optional: call once at startup to add any extra non-text indexes.
    Avoid creating another text index here (MongoDB allows only one).
    """
    coll = Media.collection
    # file_type index helps when you filter by file_type
    await coll.create_index([("file_type", 1)], name="idx_file_type")


# ---------------------------------------------------------------------
# Helper: wrap raw Mongo dicts into attribute-style objects
# ---------------------------------------------------------------------

class MediaResult:
    """
    Lightweight wrapper so caller can use file.file_size, file.file_name, file.file_id
    even though underlying data came as dict from Motor.
    """

    def __init__(self, doc: dict):
        # Mongo stores _id, map to file_id for backward compatibility
        self.file_id = str(doc.get("_id") or doc.get("file_id") or "")
        self.file_ref = doc.get("file_ref")
        self.file_name = doc.get("file_name")
        self.file_size = doc.get("file_size")
        self.file_type = doc.get("file_type")
        self.mime_type = doc.get("mime_type")
        self.caption = doc.get("caption")

        # Keep raw document if you ever need it
        self._raw = doc

    # Optional: allow dict-like access too if needed
    def __getitem__(self, item):
        return self._raw.get(item)


# ---------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------

def normalize_query(q: str) -> str:
    """
    Make query look more like the normalized file_name we store.
    Replaces _, -, ., + with spaces and compresses multiple spaces.
    """
    q = (q or "").strip()
    if not q:
        return ""
    q = re.sub(r"(_|\-|\.|\+)", " ", q)
    q = re.sub(r"\s+", " ", q)
    return q


def encode_file_id(s: bytes) -> str:
    r = b""
    n = 0

    for i in s + bytes([22]) + bytes([4]):
        if i == 0:
            n += 1
        else:
            if n:
                r += b"\x00" + bytes([n])
                n = 0
            r += bytes([i])

    return base64.urlsafe_b64encode(r).decode().rstrip("=")


def encode_file_ref(file_ref: bytes) -> str:
    return base64.urlsafe_b64encode(file_ref).decode().rstrip("=")


def unpack_new_file_id(new_file_id):
    """
    Convert Pyrogram's FileId into (file_id, file_ref) suitable for Mongo.
    """
    decoded = FileId.decode(new_file_id)
    file_id = encode_file_id(
        pack(
            "<iiqq",
            int(decoded.file_type),
            decoded.dc_id,
            decoded.media_id,
            decoded.access_hash,
        )
    )
    file_ref = encode_file_ref(decoded.file_reference)
    return file_id, file_ref


# ---------------------------------------------------------------------
# Save file
# ---------------------------------------------------------------------

async def save_file(media):
    """Save file in database. Returns (success: bool, code: int)."""

    file_id, file_ref = unpack_new_file_id(media.file_id)

    # Normalize file_name: replace special chars with spaces
    file_name = re.sub(r"(_|\-|\.|\+)", " ", str(media.file_name))

    try:
        file = Media(
            file_id=file_id,
            file_ref=file_ref,
            file_name=file_name,
            file_size=media.file_size,
            file_type=media.file_type,
            mime_type=media.mime_type,
            caption=media.caption.html if getattr(media, "caption", None) else None,
        )
    except ValidationError:
        logger.exception("Error occurred while saving file in database")
        return False, 2
    else:
        try:
            await file.commit()
        except DuplicateKeyError:
            logger.warning(
                f'{getattr(media, "file_name", "NO_FILE")} is already saved in database'
            )
            return False, 0
        else:
            logger.info(
                f'{getattr(media, "file_name", "NO_FILE")} is saved to database'
            )
            return True, 1


# ---------------------------------------------------------------------
# Search: primary ($text) with relevance
# ---------------------------------------------------------------------

async def get_search_results(query, file_type=None, max_results=MAX_BTN, offset=0, filter=False):
    """
    Primary search using MongoDB $text index.
    Returns (files, next_offset, total_results).

    - Uses textScore for relevance ranking.
    - Respects file_type filter if provided.
    - Wraps results into MediaResult so existing code using file.file_size works.
    """
    query = normalize_query(query)

    if not query:
        return [], "", 0

    filter_query = {"$text": {"$search": query}}
    if file_type:
        filter_query["file_type"] = file_type

    coll = Media.collection

    # Projection includes textScore
    projection = {
        "file_ref": 1,
        "file_name": 1,
        "file_size": 1,
        "file_type": 1,
        "mime_type": 1,
        "caption": 1,
        "score": {"$meta": "textScore"},
        # _id is included by default unless explicitly excluded
    }

    total_results = await coll.count_documents(filter_query)

    next_offset = offset + max_results
    if next_offset > total_results:
        next_offset = ""

    cursor = coll.find(filter_query, projection)
    # Sort by relevance (textScore)
    cursor.sort([("score", {"$meta": "textScore"})])
    cursor.skip(offset).limit(max_results)

    raw_files = await cursor.to_list(length=max_results)
    files = [MediaResult(doc) for doc in raw_files]

    return files, next_offset, total_results


# ---------------------------------------------------------------------
# Search: regex fallback (for non-text index / weird queries)
# ---------------------------------------------------------------------

async def get_bad_files(query, file_type=None, max_results=100, offset=0, filter=False):
    """
    Fallback search using regex on file_name (and caption if enabled).
    Returns (files, next_offset, total_results).

    This is used when $text search is not good enough or for
    more flexible pattern matching.
    """
    query = normalize_query(query)
    coll = Media.collection

    # Build regex pattern
    if not query:
        raw_pattern = ".*"
    else:
        parts = query.split()
        if len(parts) == 1:
            # Single word -> word boundary-ish match
            word = re.escape(parts[0])
            raw_pattern = rf"(\b|[\.\+\-_]){word}(\b|[\.\+\-_])"
        else:
            # "spider man homecoming" -> spider.*man.*homecoming (in order)
            escaped = [re.escape(p) for p in parts]
            raw_pattern = r".*".join(escaped)

    try:
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except re.error:
        # If regex is invalid, just return nothing (but keep return format)
        return [], "", 0

    if USE_CAPTION_FILTER:
        mongo_filter = {"$or": [{"file_name": regex}, {"caption": regex}]}
    else:
        mongo_filter = {"file_name": regex}

    if file_type:
        mongo_filter["file_type"] = file_type

    total_results = await coll.count_documents(mongo_filter)

    next_offset = offset + max_results
    if next_offset > total_results:
        next_offset = ""

    cursor = coll.find(mongo_filter)
    # Sort by recent (natural order descending)
    cursor.sort([("$natural", -1)])
    cursor.skip(offset).limit(max_results)

    raw_files = await cursor.to_list(length=max_results)
    files = [MediaResult(doc) for doc in raw_files]

    return files, next_offset, total_results


# ---------------------------------------------------------------------
# Get file details
# ---------------------------------------------------------------------

async def get_file_details(query):
    """
    Find a single file by its file_id (our internal _id).
    Returns a list of length 0 or 1 (wrapped MediaResult) for compatibility.
    """
    coll = Media.collection
    # In DB, key is _id, not file_id
    mongo_filter = {"_id": query}
    cursor = coll.find(mongo_filter)
    raw_list = await cursor.to_list(length=1)
    files = [MediaResult(doc) for doc in raw_list]
    return files
