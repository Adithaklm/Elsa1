import logging
from struct import pack
import difflib
import re
import base64
from pyrogram.file_id import FileId
from pymongo.errors import DuplicateKeyError
from umongo import Instance, Document, fields
from motor.motor_asyncio import AsyncIOMotorClient
from marshmallow.exceptions import ValidationError
from info import DATABASE_URI, DATABASE_NAME, COLLECTION_NAME, USE_CAPTION_FILTER, MAX_BTN
# ... (other imports)

def fuzzy_filter(query, file_list, n=5, cutoff=0.7):
    names = [f.file_name for f in file_list]
    close = difflib.get_close_matches(query, names, n=n, cutoff=cutoff)
    return [f for f in file_list if f.file_name in close]

def keyword_score(query, file_name):
    words = query.lower().split()
    name = file_name.lower()
    return sum(1 for w in words if w in name)

def normalize(text):
    return re.sub(r'[^a-zA-Z0-9 ]', '', text.lower().strip())

# ... rest of your code ...
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


client = AsyncIOMotorClient(DATABASE_URI)
db = client[DATABASE_NAME]
instance = Instance.from_db(db)

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
        indexes = (
            ('$file_name', ),  # Single-field index (existing)
            (('file_name', 1), ('caption', 1)),  # Compound index (add this)
        )
        collection_name = COLLECTION_NAME


async def save_file(media):
    """Save file in database"""

    file_id, file_ref = unpack_new_file_id(media.file_id)
    # (_, -, +, ., (, ), |) ഇവയെല്ലാം ഒഴിവാക്കുന്നു
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
    except ValidationError:
        logger.exception('Error occurred while saving file in database')
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
            logger.info(f'{getattr(media, "file_name", "NO_FILE")} is saved to database')
            return True, 1


# Replace the existing get_search_results implementation with the code below.
# This version uses MongoDB $text search when appropriate, avoids count_documents
# by fetching max_results+1, adds a projection, and sorts by textScore or _id.
# It keeps the regex fallback for short/single-token queries.

import re
import logging

logger = logging.getLogger(__name__)

# ... (existing code and Media class above) ...

async def get_search_results(query, file_type=None, max_results=6, offset=0, filter=False):
    """
    Faster search:
    - Use $text (text index required) for multi-word or longer queries
    - Fallback to a tighter regex for very short/single-token queries
    - Use projection to only fetch needed fields (no 'score' field to keep umongo happy)
    - Sort by _id desc (recent first)
    - Use limit(max_results + 1) to determine if there is a next page (avoid count_documents)
    Returns: (files, next_offset, None)
    """
    query = (query or "").strip()

    # projection: only the fields we need (reduces payload)
    projection = {
        "file_name": 1,
        "file_id": 1,
        "file_size": 1,
        "file_type": 1,
        "caption": 1,
    }

    use_text_search = False

    # Decide filter
    if not query:
        mongo_filter = {}
    elif " " in query or len(query) > 2:
        # Use text search for multi-word or longer queries (requires a text index)
        use_text_search = True
        mongo_filter = {"$text": {"$search": query}}
    else:
        # Short single token: fallback to a narrower regex to mimic word-boundary matching
        raw_pattern = r'(\b|[\.\+\-_])' + re.escape(query) + r'(\b|[\.\+\-_])'
        try:
            regex = re.compile(raw_pattern, flags=re.IGNORECASE)
        except Exception as e:
            logger.exception("Invalid regex for search query: %s", e)
            return [], '', 0
        mongo_filter = {"file_name": regex}

    if file_type:
        mongo_filter["file_type"] = file_type

    # Build cursor with appropriate sort & projection
    cursor = Media.find(mongo_filter, projection)
    cursor.sort("_id", -1)  # use indexed sort (recent first)

    # Pagination: fetch one extra doc to detect next page (avoid count_documents)
    docs = await cursor.skip(offset).limit(max_results + 1).to_list(length=max_results + 1)

    if len(docs) <= max_results:
        next_offset = ''
        files = docs
    else:
        files = docs[:max_results]
        next_offset = offset + max_results

    # We avoid returning/using total_results (expensive). Return None for that slot.
    return files, next_offset, None
    
async def get_bad_files(query, file_type=None, max_results=100, offset=0, filter=False):
    """For given query return (results, next_offset)"""
    query = query.strip()
    #if filter:
        #better ?
        #query = query.replace(' ', r'(\s|\.|\+|\-|_)')
        #raw_pattern = r'(\s|_|\-|\.|\+)' + query + r'(\s|_|\-|\.|\+)'
    if not query:
        raw_pattern = '.'
    elif ' ' not in query:
        raw_pattern = r'(\b|[\.\+\-_])' + query + r'(\b|[\.\+\-_])'
    else:
        raw_pattern = query.replace(' ', r'.*[\s\.\+\-_]')

    try:
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except:
        return []

    if USE_CAPTION_FILTER:
        filter = {'$or': [{'file_name': regex}, {'caption': regex}]}
    else:
        filter = {'file_name': regex}

    if file_type:
        filter['file_type'] = file_type

    total_results = await Media.count_documents(filter)
    next_offset = offset + max_results

    if next_offset > total_results:
        next_offset = ''

    cursor = Media.find(filter)
    # Sort by recent
    cursor.sort('$natural', -1)
    # Slice files according to offset and max results
    cursor.skip(offset).limit(max_results)
    # Get list of files
    files = await cursor.to_list(length=max_results)

    return files, next_offset, total_results

async def get_file_details(query):
    filter = {'file_id': query}
    cursor = Media.find(filter)
    filedetails = await cursor.to_list(length=1)
    return filedetails


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
    """Return file_id, file_ref"""
    decoded = FileId.decode(new_file_id)
    file_id = encode_file_id(
        pack(
            "<iiqq",
            int(decoded.file_type),
            decoded.dc_id,
            decoded.media_id,
            decoded.access_hash
        )
    )
    file_ref = encode_file_ref(decoded.file_reference)
    return file_id, file_ref
