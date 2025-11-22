from pyrogram import Client, filters
import asyncio
import time

app = Client("search_bot", api_id=12345, api_hash="your_hash", bot_token="your_bot_token")


async def fake_search(query):
    # Replace this with your database search
    # Using delays to simulate long processing
    await asyncio.sleep(1)
    return [f"Result {i+1} for {query}" for i in range(6)]


@app.on_message(filters.group & filters.text & ~filters.command("start"))
async def search_handler(client, message):

    query = message.text.strip()
    if len(query) < 2:
        return

    # Step 1: Immediately send fast feedback
    progress_msg = await message.reply_text("Searching...")

    # Step 2: Progress animation while searching
    stages = [
        "Searching.",
        "Searching..",
        "Searching...",
        "Still working...",
        "Almost done..."
    ]

    search_task = asyncio.create_task(fake_search(query))

    i = 0
    while not search_task.done():
        await asyncio.sleep(0.7)
        try:
            await progress_msg.edit_text(stages[i % len(stages)])
        except:
            pass
        i += 1

    # Step 3: Final results
    results = search_task.result()

    formatted = "\n".join(results)
    await progress_msg.edit_text(
        f"Results for **{query}:**\n\n{formatted}"
    )


app.run()
