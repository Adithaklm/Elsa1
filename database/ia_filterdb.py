# ia_filterdb.py
"""
Optimized DB search layer for Telegram media bot.

Features:
- Creates/ensures text & file_name indexes
- Tiered search: exact phrase -> word-boundary -> partial -> Mongo text -> fuzzy trigram fallback
- Lightweight in-memory TTL cache
- Uses motor (async MongoDB driver)
- Returns minimal projection for speed
"""

import re
import time
import asyncio
from typing import List, Dict, Any, Optional, Tuple
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import TEXT, ASCENDING
from pymongo.errors import DuplicateKeyError
import difflib
import math
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# -------------------------
# Configuration (override as needed)
# -------------------------
DATABASE_URI = "mongodb://localhost:27017"
DATABASE_NAME = "botdb"
COLLECTION_NAME = "media_files"
CACHE_TTL_SECONDS = 60 * 2  # cache queries for 2 minutes
FUZZY_CANDIDATE_LIMIT = 2000  # number of DB rows to consider for fuzzy fallback
DEFAULT_LIMIT = 15

# -------------------------
# Utilities
# -------------------------
def normalize_text(s: str) -> str:
    """Lowercase + strip whitespace and reduce multiple spaces."""
    return re.sub(r"\s+", " ", s.strip().lower())

def split_trigrams(s: str) -> List[str]:
    """Return list of character trigrams for string s (after padding)."""
    s = f"  {s}  "
    return [s[i:i+3] for i in range(len(s)-2)]

def trigram_similarity(a: str, b: str) -> float:
    """Jaccard-like trigram similarity between two strings."""
    ta = set(split_trigrams(a))
    tb = set(split_trigrams(b))
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union

# -------------------------
# Simple TTL cache
# -------------------------
class SimpleTTLCache:
    def __init__(self, ttl_seconds: int = 120):
        self.ttl = ttl_seconds
        self.store: Dict[str, Tuple[float, Any]] = {}

    def get(self, key: str):
        entry = self.store.get(key)
        if not entry:
            return None
        ts, val = entry
        if time.time() - ts > self.ttl:
            del self.store[key]
            return None
        return val

    def set(self, key: str, value: Any):
        self.store[key] = (time.time(), value)

    def clear(self):
        self.store.clear()

query_cache = SimpleTTLCache(ttl_seconds=CACHE_TTL_SECONDS)

# -------------------------
# Database layer
# -------------------------
class MediaDB:
    def __init__(self,
                 uri: str = DATABASE_URI,
                 db_name: str = DATABASE_NAME,
                 collection_name: str = COLLECTION_NAME):
        self.client = AsyncIOMotorClient(uri)
        self.db = self.client[db_name]
        self.collection = self.db[collection_name]

    async def ensure_indexes(self):
        """
        Ensure indexes exist:
         - text index on file_name and caption for $text search
         - case-insensitive index on file_name (not strictly possible; but a normal index helps regex)
        """
        # Text index (weights can be tuned)
        try:
            await self.collection.create_index(
                [("file_name", TEXT), ("caption", TEXT)],
                name="text_idx",
                default_language="english",
                background=True
            )
            # Helpful index for prefix/regex searches
            await self.collection.create_index([("file_name", ASCENDING)], name="file_name_idx", background=True)
            logger.info("Indexes ensured")
        except Exception as e:
            logger.exception("Index creation failed: %s", e)

    async def save_file(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Insert or update minimal media metadata.
        Required fields: file_id (unique), file_name
        """
        assert "file_id" in data and "file_name" in data
        data.setdefault("created_at", time.time())
        data["file_name_norm"] = normalize_text(data["file_name"])
        try:
            res = await self.collection.insert_one(data)
            return {"ok": True, "id": str(res.inserted_id)}
        except DuplicateKeyError:
            # fallback: update existing
            await self.collection.update_one({"file_id": data["file_id"]}, {"$set": data})
            return {"ok": True, "updated": True}
        except Exception as e:
            logger.exception("save_file error")
            return {"ok": False, "error": str(e)}

    # -------------------------
    # Search API
    # -------------------------
    async def get_search_results(self, query: str, limit: int = DEFAULT_LIMIT) -> List[Dict[str, Any]]:
        """
        Tiered search:
         1) exact phrase (whole string)
         2) word-boundary match
         3) partial contains
         4) MongoDB $text (if available) with sort by score
         5) fuzzy trigram fallback (cheap approximate)
        """
        if not query or not query.strip():
            return []

        query_norm = normalize_text(query)
        cache_key = f"search::{query_norm}::{limit}"
        cached = query_cache.get(cache_key)
        if cached is not None:
            return cached

        projection = {"file_id": 1, "file_name": 1, "caption": 1, "mime_type": 1, "_id": 0}
        results = []

        # 1) exact phrase (strict)
        regex_exact = re.compile(rf"^{re.escape(query_norm)}$", re.IGNORECASE | re.UNICODE)
        r = await self.collection.find({"file_name_norm": {"$regex": regex_exact}}, projection).to_list(length=limit)
        if r:
            query_cache.set(cache_key, r[:limit])
            return r[:limit]

        # 2) word-boundary match
        # protect special regex characters in query
        regex_word = re.compile(rf"\b{re.escape(query_norm)}\b", re.IGNORECASE | re.UNICODE)
        r = await self.collection.find({"file_name_norm": {"$regex": regex_word}}, projection).to_list(length=limit)
        if r:
            query_cache.set(cache_key, r[:limit])
            return r[:limit]

        # 3) partial contains (substring)
        regex_partial = re.compile(re.escape(query_norm), re.IGNORECASE | re.UNICODE)
        r = await self.collection.find({"file_name_norm": {"$regex": regex_partial}}, projection).to_list(length=limit)
        if r:
            query_cache.set(cache_key, r[:limit])
            return r[:limit]

        # 4) $text search (if text index exists); give it a shot
        try:
            # Use $text with score if possible
            cursor = self.collection.find(
                {"$text": {"$search": query}},
                {"score": {"$meta": "textScore"}, **projection}
            ).sort([("score", {"$meta": "textScore"})]).limit(limit)
            r = await cursor.to_list(length=limit)
            if r:
                query_cache.set(cache_key, r[:limit])
                return r[:limit]
        except Exception:
            # either no text index or other issue — ignore and move to fuzzy fallback
            logger.debug("Text search failed or not available for query=%s", query)

        # 5) Fuzzy fallback (trigram similarity)
        # Pull a bounded candidate set from DB to avoid scanning whole collection.
        # Here we fetch filenames that contain at least one word from query (cheap prune).
        words = [w for w in re.split(r"\s+", query_norm) if w]
        if words:
            # Build regex OR of words (escaped)
            or_pattern = "|".join(re.escape(w) for w in words[:5])  # limit to first 5 words to avoid huge regex
            prune_regex = re.compile(or_pattern, re.IGNORECASE | re.UNICODE)
            try:
                candidates = await self.collection.find(
                    {"file_name_norm": {"$regex": prune_regex}},
                    projection
                ).to_list(length=FUZZY_CANDIDATE_LIMIT)
            except Exception:
                candidates = await self.collection.find({}, projection).to_list(length=FUZZY_CANDIDATE_LIMIT)
        else:
            candidates = await self.collection.find({}, projection).to_list(length=FUZZY_CANDIDATE_LIMIT)

        # compute trigram similarity and also use difflib ratio as tie-breaker
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for doc in candidates:
            name = normalize_text(doc.get("file_name", ""))
            score_tri = trigram_similarity(query_norm, name)
            # difflib's ratio can help when trigrams are sparse (short strings)
            d_ratio = difflib.SequenceMatcher(None, query_norm, name).ratio()
            # combine: give trigram more weight but include difflib
            combined = 0.8 * score_tri + 0.2 * d_ratio
            if combined > 0.12:  # threshold to drop hopeless matches
                scored.append((combined, doc))

        scored.sort(key=lambda x: x[0], reverse=True)
        final = [doc for score, doc in scored[:limit]]
        query_cache.set(cache_key, final)
        return final

    # Optional helper: force-clear cache (useful during updates)
    def clear_cache(self):
        query_cache.clear()

# -------------------------
# Example usage (for dev)
# -------------------------
if __name__ == "__main__":
    async def _demo():
        m = MediaDB()
        await m.ensure_indexes()
        print("Indexes ensured. Try searching...")
        res = await m.get_search_results("thor", limit=10)
        print("Results:", res)

    asyncio.run(_demo())

# --- Backwards compatibility alias (place this AFTER MediaDB class definition) ---
# Old code expected `from database.ia_filterdb import Media`
# Map it to the new MediaDB class so old imports continue to work.
Media = MediaDB
