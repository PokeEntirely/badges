# your_script.py

import asyncio
import aiohttp
import json
import os
import logging
from concurrent.futures import ThreadPoolExecutor
import aiofiles
from datetime import datetime

# Set to True if you want debug messages
DEBUG = True

# Use the webhook URL from environment variable for security
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

logging.basicConfig(
    filename='error.log',
    level=logging.ERROR,
    format='%(asctime)s:%(levelname)s:%(message)s'
)

def debug_print(message):
    if DEBUG:
        print(message)

class WebhookSender:
    def __init__(self, webhook_url):
        debug_print("Initializing WebhookSender")
        self.webhook_url = webhook_url
        self.embeds = []
        self.sent_badges = set()
        self.lock = asyncio.Lock()
        self.executor = ThreadPoolExecutor(max_workers=10)
        self.session = aiohttp.ClientSession()

    async def add_embed(self, embed, badge_id):
        async with self.lock:
            if badge_id not in self.sent_badges:
                self.embeds.append(embed)
                self.sent_badges.add(badge_id)

    async def send_batch(self):
        async with self.lock:
            if not self.embeds:
                return
            batch_size = min(len(self.embeds), 10)
            batch = self.embeds[:batch_size]
            payload = {
                "content": "<@&1293689261773557865>",
                "embeds": batch,
                "attachments": []
            }
            await self.send_webhook(payload)
            self.embeds = self.embeds[batch_size:]

    async def send_webhook(self, payload):
        try:
            async with self.session.post(self.webhook_url, json=payload, timeout=10) as response:
                if response.status == 429:
                    data = await response.json()
                    retry_after = data.get("retry_after", 5)
                    await asyncio.sleep(retry_after)
                    await self.session.post(self.webhook_url, json=payload, timeout=10)
                else:
                    response.raise_for_status()
        except Exception as e:
            logging.error(f"Failed to send webhook: {e}")
            print(f"Failed to send webhook: {e}")

    async def close(self):
        await self.send_batch()  # Ensure all embeds are sent before closing
        await self.session.close()
        self.executor.shutdown(wait=True)

async def get_badges(session, universe_id):
    badges = []
    cursor = ''
    retry_count = 0
    max_retries = 5
    while True:
        url = f"https://badges.roblox.com/v1/universes/{universe_id}/badges?limit=100&sortOrder=Asc"
        if cursor:
            url += f"&cursor={cursor}"
        try:
            async with session.get(url, timeout=10) as response:
                if response.status == 429:
                    await asyncio.sleep(30)
                    continue
                response.raise_for_status()
                data = await response.json()
                badges.extend(data.get('data', []))
                cursor = data.get('nextPageCursor')
                if not cursor:
                    break
                retry_count = 0
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            retry_count += 1
            if retry_count >= max_retries:
                break
            await asyncio.sleep(5)
    return badges

async def get_badge_thumbnail_url(session, badge_id):
    url = f"https://thumbnails.roblox.com/v1/badges/icons?badgeIds={badge_id}&size=150x150&format=Png&isCircular=false"
    retry_count = 0
    max_retries = 5
    while True:
        try:
            async with session.get(url, timeout=10) as response:
                if response.status == 429:
                    await asyncio.sleep(30)
                    continue
                response.raise_for_status()
                data = await response.json()
                if data['data']:
                    return data['data'][0].get('imageUrl', '')
                return ""
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            retry_count += 1
            if retry_count >= max_retries:
                return ""
            await asyncio.sleep(5)

async def process_new_badge(session, badge, webhook_sender):
    badge_id = badge['id']
    description = badge.get('description') or "None."
    created_time = badge['created']

    try:
        creation_time_obj = datetime.fromisoformat(created_time.replace('Z', '+00:00'))
        creation_timestamp = int(creation_time_obj.timestamp())
        discord_timestamp = f"<t:{creation_timestamp}:f>"
    except Exception as e:
        logging.error(f"Error parsing creation time for badge {badge_id}: {e}")
        discord_timestamp = "Unknown"

    awarding_universe = badge.get('awardingUniverse', {})
    game_name = awarding_universe.get('name', 'Unknown Game')
    game_id = awarding_universe.get('rootPlaceId', '0')
    game_link = f"https://www.roblox.com/games/{game_id}/game" if game_id != '0' else game_name
    game_name_hyperlink = f"[{game_name}]({game_link})" if game_id != '0' else game_name

    thumbnail_url = await get_badge_thumbnail_url(session, badge_id)

    embed = {
        "title": badge['name'],
        "url": f"https://www.roblox.com/badges/{badge_id}/",
        "color": 16762779,
        "fields": [
            {"name": "Badge ID", "value": str(badge_id), "inline": True},
            {"name": "Game Name", "value": game_name_hyperlink, "inline": True},
            {"name": "Creation Time", "value": discord_timestamp, "inline": False},
            {"name": "Description", "value": description, "inline": False}
        ],
        "thumbnail": {"url": thumbnail_url},
        "author": {
            "name": "New Badge Uploaded",
        },
        "footer": {
            "text": "Created by Poke",
            "icon_url": "https://i.imgur.com/Od3xppJ.png"
        }
    }
    await webhook_sender.add_embed(embed, badge_id)

async def process_universe(session, universe_id, badges_data, webhook_sender):
    fetched_badges = await get_badges(session, universe_id)
    universe_id_str = str(universe_id)
    if universe_id_str in badges_data:
        existing_badge_ids = set(str(badge['id']) for badge in badges_data[universe_id_str]['data'])
        new_badges = [badge for badge in fetched_badges if str(badge['id']) not in existing_badge_ids]
        if new_badges:
            badges_data[universe_id_str]['data'].extend(new_badges)
            await asyncio.gather(*(process_new_badge(session, badge, webhook_sender) for badge in new_badges))
            return len(new_badges)
    else:
        badges_data[universe_id_str] = {"data": fetched_badges}
    return 0

async def load_universe_ids():
    async with aiofiles.open('games.txt', 'r', encoding='utf-8') as f:
        return [line.strip() for line in await f.readlines()]

async def load_badges_data():
    if os.path.exists('badges.json'):
        async with aiofiles.open('badges.json', 'r', encoding='utf-8') as f:
            return json.loads(await f.read())
    return {}

async def save_badges_data(badges_data):
    async with aiofiles.open('badges.json', 'w', encoding='utf-8') as f:
        await f.write(json.dumps(badges_data, indent=2))

async def main():
    debug_print("Starting main function")
    webhook_sender = WebhookSender(WEBHOOK_URL)

    universal_ids = await load_universe_ids()
    badges_data = await load_badges_data()

    timeout = aiohttp.ClientTimeout(total=15)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [process_universe(session, universe_id, badges_data, webhook_sender) for universe_id in universal_ids]
        total_new_badges = sum(await asyncio.gather(*tasks))
        await webhook_sender.send_batch()
    await save_badges_data(badges_data)
    print(f"Processed all universes. New badges: {total_new_badges}")
    await webhook_sender.close()

if __name__ == "__main__":
    print("Running script")
    asyncio.run(main())
