import motor.motor_asyncio
from datetime import datetime

from info import DATABASE_URI, DATABASE_NAME


class ChannelSyncDatabase:
    def __init__(self, uri, database_name):
        self._client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        self.db = self._client[database_name]
        self.col = self.db.channel_file_sync

    async def add_synced_file(self, source_chat_id, source_message_id, destination_chat_id, file_name, language, file_size, file_type):
        payload = {
            "source_chat_id": source_chat_id,
            "source_message_id": source_message_id,
            "destination_chat_id": destination_chat_id,
            "file_name": file_name,
            "language": language,
            "file_size": file_size,
            "file_type": file_type,
            "created_at": datetime.utcnow(),
        }
        await self.col.insert_one(payload)


channel_sync_db = ChannelSyncDatabase(DATABASE_URI, DATABASE_NAME)
