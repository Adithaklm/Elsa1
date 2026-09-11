import logging
from struct import pack
import re
import base64
from difflib import SequenceMatcher

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
    file_id = fields.StrField(attribute="_id")
    file_ref = fields.StrField(allow_none=True)
    file_name = fields.StrField(required=True)
    file_size = fields.IntField(required=True)
    file_type = fields.StrField(allow_none=True)
    mime_type = fields.StrField(allow_none=True)
    caption = fields.StrField(allow_none=True)

    class Meta:
        indexes = (
            ("$file_name",),
            (("file_name", 1), ("caption", 1)),
        )
        collection_name = COLLECTION_NAME


async def ensure_extra_indexes():
    """
    Optional: call once at startup to add extra indexes.
    """
    coll = Media.collection

    await coll.create_index(
        [("file_type", 1)],
        name="idx_file_type"
    )


# ---------------------------------------------------------------------
# Helper: wrap raw Mongo dicts
# ---------------------------------------------------------------------

class MediaResult:
    """
    Lightweight wrapper so existing code can use:

        file.file_size
        file.file_name
        file.file_id
    """

    def __init__(self, doc: dict):
        self.file_id = str(
            doc.get("_id") or doc.get("file_id") or ""
        )
        self.file_ref = doc.get("file_ref")
        self.file_name = doc.get("file_name")
        self.file_size = doc.get("file_size")
        self.file_type = doc.get("file_type")
        self.mime_type = doc.get("mime_type")
        self.caption = doc.get("caption")

        self._raw = doc

    def __getitem__(self, item):
        return self._raw.get(item)


# ---------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------

def normalize_query(q: str) -> str:
    """
    Normalize search text.

    Example:

        Spider-Man.No Way Home
        Spider_Man No Way Home

    becomes:

        Spider Man No Way Home
    """

    q = (q or "").strip()

    if not q:
        return ""

    q = re.sub(r"(_|\-|\.+|\+)", " ", q)
    q = re.sub(r"\s+", " ", q)

    return q


def normalize_for_fuzzy(q: str) -> str:
    """
    Strong normalization used by fuzzy matching.

    Removes punctuation and spaces so:

        Spider-Man
        Spider Man
        spider_man

    are treated similarly.
    """

    q = (q or "").lower()

    q = re.sub(r"[^a-z0-9]+", "", q)

    return q


def similarity(a: str, b: str) -> float:
    """
    Return similarity between two strings from 0.0 to 1.0.
    """

    return SequenceMatcher(
        None,
        normalize_for_fuzzy(a),
        normalize_for_fuzzy(b)
    ).ratio()


def token_similarity(query: str, filename: str) -> float:
    """
    Compare individual words as well as the complete filename.

    This helps with searches such as:

        avengrs endgme

    against:

        Avengers Endgame 2019
    """

    query_tokens = normalize_query(query).lower().split()
    file_tokens = normalize_query(filename).lower().split()

    if not query_tokens or not file_tokens:
        return 0.0

    total = 0.0

    for q_token in query_tokens:
        best = 0.0

        for f_token in file_tokens:
            score = SequenceMatcher(
                None,
                q_token,
                f_token
            ).ratio()

            if score > best:
                best = score

        total += best

    token_score = total / len(query_tokens)

    full_score = similarity(query, filename)

    # Give word matching slightly more importance.
    return (token_score * 0.70) + (full_score * 0.30)


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
    Convert Pyrogram FileId into:

        (file_id, file_ref)
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
    """
    Save file in database.

    Returns:

        (True, 1)  -> saved
        (False, 0) -> duplicate
        (False, 2) -> error
    """

    file_id, file_ref = unpack_new_file_id(media.file_id)

    # Normalize filename before storing.
    file_name = re.sub(
        r"(_|\-|\.+|\+)",
        " ",
        str(media.file_name)
    )

    try:
        file = Media(
            file_id=file_id,
            file_ref=file_ref,
            file_name=file_name,
            file_size=media.file_size,
            file_type=media.file_type,
            mime_type=media.mime_type,
            caption=(
                media.caption.html
                if getattr(media, "caption", None)
                else None
            ),
        )

    except ValidationError:
        logger.exception(
            "Error occurred while saving file in database"
        )

        return False, 2

    else:
        try:
            await file.commit()

        except DuplicateKeyError:
            logger.warning(
                f'{getattr(media, "file_name", "NO_FILE")} '
                f'is already saved in database'
            )

            return False, 0

        else:
            logger.info(
                f'{getattr(media, "file_name", "NO_FILE")} '
                f'is saved to database'
            )

            return True, 1


# ---------------------------------------------------------------------
# Primary MongoDB text search
# ---------------------------------------------------------------------

async def get_search_results(
    query,
    file_type=None,
    max_results=MAX_BTN,
    offset=0,
    filter=False
):
    """
    Smart movie search.

    Supports:
    - punctuation differences
    - different word order
    - year searches
    - partial movie names
    - small spelling mistakes
    """

    query = (query or "").strip().lower()

    if not query:
        return [], "", 0

    coll = Media.collection

    # ---------------------------------------------------------
    # Normalize query
    # ---------------------------------------------------------

    query = re.sub(r"[_\-.+,()\[\]{}]", " ", query)
    query = re.sub(r"\s+", " ", query).strip()

    query_words = query.split()

    if not query_words:
        return [], "", 0

    # ---------------------------------------------------------
    # 1. Normal MongoDB search
    # ---------------------------------------------------------

    mongo_query = {
        "$text": {
            "$search": query
        }
    }

    if file_type:
        mongo_query["file_type"] = file_type

    projection = {
        "_id": 1,
        "file_ref": 1,
        "file_name": 1,
        "file_size": 1,
        "file_type": 1,
        "mime_type": 1,
        "caption": 1,
        "score": {
            "$meta": "textScore"
        }
    }

    cursor = coll.find(
        mongo_query,
        projection
    )

    cursor.sort(
        [
            (
                "score",
                {
                    "$meta": "textScore"
                }
            )
        ]
    )

    cursor.skip(offset).limit(max_results)

    normal_results = await cursor.to_list(
        length=max_results
    )

    # If normal search works, use it.
    if normal_results:
        files = [
            MediaResult(doc)
            for doc in normal_results
        ]

        total = await coll.count_documents(
            mongo_query
        )

        next_offset = (
            offset + max_results
            if offset + max_results < total
            else ""
        )

        return files, next_offset, total

    # ---------------------------------------------------------
    # 2. Smart token search
    #
    # Example:
    #
    # "I Nobody 2026"
    #
    # matches:
    #
    # "I, Nobody (2026) Malayalam..."
    # ---------------------------------------------------------

    # Don't fuzzy-search extremely short queries.
    if len(query) < 3:
        return [], "", 0

    # Build regex for important words.
    #
    # MongoDB searches each word independently.
    # This avoids the punctuation problem with:
    #
    # I, Nobody (2026)
    #

    word_conditions = []

    for word in query_words:

        # Ignore one-character words such as "I"
        # during the database filtering stage.
        #
        # They will NOT prevent the movie from matching.
        if len(word) <= 1:
            continue

        escaped = re.escape(word)

        word_conditions.append({
            "file_name": {
                "$regex": escaped,
                "$options": "i"
            }
        })

    if not word_conditions:
        return [], "", 0

    token_query = {
        "$and": word_conditions
    }

    if file_type:
        token_query["file_type"] = file_type

    token_cursor = coll.find(
        token_query,
        {
            "_id": 1,
            "file_ref": 1,
            "file_name": 1,
            "file_size": 1,
            "file_type": 1,
            "mime_type": 1,
            "caption": 1
        }
    )

    token_docs = await token_cursor.to_list(
        length=MAX_BTN * 5
    )

    if token_docs:
        files = [
            MediaResult(doc)
            for doc in token_docs[offset:offset + max_results]
        ]

        total = len(token_docs)

        next_offset = (
            offset + max_results
            if offset + max_results < total
            else ""
        )

        return files, next_offset, total

    # ---------------------------------------------------------
    # 3. Fuzzy spelling search
    #
    # Only used when normal + token search fail.
    # ---------------------------------------------------------

    logger.info(
        f"No exact/token results for '{query}'. "
        f"Trying fuzzy search."
    )

    # Extract useful words.
    fuzzy_words = [
        word
        for word in query_words
        if len(word) >= 3
    ]

    if not fuzzy_words:
        return [], "", 0

    # Search using the first useful word first.
    #
    # This prevents scanning the entire database for queries
    # such as "no".
    first_word = fuzzy_words[0]

    fuzzy_candidates = await coll.find(
        {
            "file_name": {
                "$regex": re.escape(first_word[:3]),
                "$options": "i"
            }
        },
        {
            "_id": 1,
            "file_ref": 1,
            "file_name": 1,
            "file_size": 1,
            "file_type": 1,
            "mime_type": 1,
            "caption": 1
        }
    ).to_list(length=5000)

    if not fuzzy_candidates:
        return [], "", 0

    scored = []

    for doc in fuzzy_candidates:

        filename = str(
            doc.get("file_name") or ""
        ).lower()

        clean_filename = re.sub(
            r"[^a-z0-9]+",
            " ",
            filename
        )

        filename_words = clean_filename.split()

        if not filename_words:
            continue

        total_score = 0
        matched_words = 0

        for query_word in fuzzy_words:

            best_score = 0

            for filename_word in filename_words:

                score = SequenceMatcher(
                    None,
                    query_word,
                    filename_word
                ).ratio()

                if score > best_score:
                    best_score = score

            # Require reasonably close spelling.
            if best_score >= 0.70:
                matched_words += 1
                total_score += best_score

        if not fuzzy_words:
            continue

        match_ratio = (
            matched_words / len(fuzzy_words)
        )

        if matched_words == 0:
            continue

        average_score = (
            total_score / matched_words
        )

        final_score = (
            average_score * 0.70
            + match_ratio * 0.30
        )

        # Require most search words to match.
        if (
            final_score >= 0.72
            and match_ratio >= 0.50
        ):
            scored.append(
                (
                    final_score,
                    doc
                )
            )

    # Highest-quality matches first.
    scored.sort(
        key=lambda x: x[0],
        reverse=True
    )

    total = len(scored)

    selected = scored[
        offset:offset + max_results
    ]

    files = [
        MediaResult(doc)
        for score, doc in selected
    ]

    next_offset = (
        offset + max_results
        if offset + max_results < total
        else ""
    )

    logger.info(
        f"Fuzzy search '{query}' -> {total} results"
    )

    return files, next_offset, total

# ---------------------------------------------------------------------
# Regex fallback
# ---------------------------------------------------------------------

async def get_bad_files(
    query,
    file_type=None,
    max_results=100,
    offset=0,
    filter=False
):
    """
    Regex-based fallback search.
    """

    query = normalize_query(query)

    coll = Media.collection

    if not query:
        raw_pattern = ".*"

    else:
        parts = query.split()

        if len(parts) == 1:
            word = re.escape(parts[0])

            raw_pattern = (
                rf"(\b|[\.\\+\-_])"
                rf"{word}"
                rf"(\b|[\.\\+\-_])"
            )

        else:
            escaped = [
                re.escape(p)
                for p in parts
            ]

            raw_pattern = (
                r".*".join(escaped)
            )

    try:
        regex = re.compile(
            raw_pattern,
            flags=re.IGNORECASE
        )

    except re.error:
        return [], "", 0

    if USE_CAPTION_FILTER:
        mongo_filter = {
            "$or": [
                {
                    "file_name": regex
                },
                {
                    "caption": regex
                }
            ]
        }

    else:
        mongo_filter = {
            "file_name": regex
        }

    if file_type:
        mongo_filter["file_type"] = file_type

    total_results = await coll.count_documents(
        mongo_filter
    )

    next_offset = offset + max_results

    if next_offset >= total_results:
        next_offset = ""

    cursor = coll.find(
        mongo_filter
    )

    cursor.sort(
        [
            (
                "$natural",
                -1
            )
        ]
    )

    cursor.skip(offset).limit(max_results)

    raw_files = await cursor.to_list(
        length=max_results
    )

    files = [
        MediaResult(doc)
        for doc in raw_files
    ]

    return (
        files,
        next_offset,
        total_results
    )


# ---------------------------------------------------------------------
# FUZZY / SPELLING-TOLERANT SEARCH
# ---------------------------------------------------------------------

async def get_fuzzy_search_results(
    query,
    file_type=None,
    max_results=MAX_BTN,
    offset=0,
    threshold=0.55
):
    """
    Spelling-tolerant search.

    Example:

        User:
            Avengrs Endgme

        Database:
            Avengers Endgame 2019

    The database filename receives a similarity score.

    IMPORTANT:
    This is a fallback search, not the primary search.
    """

    query = normalize_query(query)

    if not query:
        return [], "", 0

    coll = Media.collection

    mongo_filter = {}

    if file_type:
        mongo_filter["file_type"] = file_type

    # -------------------------------------------------------------
    # Fetch only fields required for fuzzy matching.
    # -------------------------------------------------------------

    projection = {
        "_id": 1,
        "file_ref": 1,
        "file_name": 1,
        "file_size": 1,
        "file_type": 1,
        "mime_type": 1,
        "caption": 1,
    }

    # -------------------------------------------------------------
    # Prevent an accidental unlimited database operation.
    #
    # The fuzzy fallback is only used when normal search fails.
    # -------------------------------------------------------------

    cursor = coll.find(
        mongo_filter,
        projection
    )

    raw_files = await cursor.to_list(
        length=10000
    )

    if not raw_files:
        return [], "", 0

    scored_results = []

    query_normalized = normalize_for_fuzzy(
        query
    )

    for doc in raw_files:

        file_name = str(
            doc.get("file_name") or ""
        )

        if not file_name:
            continue

        score = token_similarity(
            query,
            file_name
        )

        # ---------------------------------------------------------
        # Additional substring bonus.
        # ---------------------------------------------------------

        normalized_filename = normalize_for_fuzzy(
            file_name
        )

        if query_normalized in normalized_filename:
            score += 0.20

        # ---------------------------------------------------------
        # Exact token bonus.
        # ---------------------------------------------------------

        query_tokens = (
            normalize_query(query)
            .lower()
            .split()
        )

        filename_tokens = (
            normalize_query(file_name)
            .lower()
            .split()
        )

        exact_tokens = 0

        for token in query_tokens:
            if token in filename_tokens:
                exact_tokens += 1

        if query_tokens:
            score += (
                exact_tokens /
                len(query_tokens)
            ) * 0.15

        # Maximum score = 1.0
        score = min(score, 1.0)

        if score >= threshold:
            scored_results.append(
                (
                    score,
                    doc
                )
            )

    # -------------------------------------------------------------
    # Highest similarity first.
    # -------------------------------------------------------------

    scored_results.sort(
        key=lambda item: item[0],
        reverse=True
    )

    total_results = len(
        scored_results
    )

    # -------------------------------------------------------------
    # Pagination
    # -------------------------------------------------------------

    start = offset
    end = offset + max_results

    selected = scored_results[
        start:end
    ]

    files = []

    for score, doc in selected:

        result = MediaResult(doc)

        # Keep score available if needed later.
        result.search_score = score

        files.append(result)

    if end >= total_results:
        next_offset = ""

    else:
        next_offset = end

    logger.info(
        f"Fuzzy search '{query}' -> "
        f"{total_results} results"
    )

    return (
        files,
        next_offset,
        total_results
    )


# ---------------------------------------------------------------------
# Optional direct fuzzy search
# ---------------------------------------------------------------------

async def get_fuzzy_files(
    query,
    file_type=None,
    max_results=MAX_BTN,
    offset=0
):
    """
    Public helper for fuzzy search.

    Can be used directly by other plugins if needed.
    """

    return await get_fuzzy_search_results(
        query=query,
        file_type=file_type,
        max_results=max_results,
        offset=offset
    )


# ---------------------------------------------------------------------
# Get file details
# ---------------------------------------------------------------------

async def get_file_details(query):
    """
    Find a single file by its internal file_id.
    """

    coll = Media.collection

    mongo_filter = {
        "_id": query
    }

    cursor = coll.find(
        mongo_filter
    )

    raw_list = await cursor.to_list(
        length=1
    )

    files = [
        MediaResult(doc)
        for doc in raw_list
    ]

    return files
