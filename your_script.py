# your_script.py

import asyncio
import aiohttp
import json
import os
import logging
from concurrent.futures import ThreadPoolExecutor
import aiofiles
from datetime import datetime

DEBUG = False

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
                if len(self.embeds) >= 10:
                    await self.send_batch()

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
        retry_count = 0
        max_retries = 5
        delay = 1
        while retry_count < max_retries:
            try:
                async with self.session.post(self.webhook_url, json=payload, timeout=120) as response:
                    if response.status == 429:
                        data = await response.json()
                        retry_after = data.get("retry_after", delay)
                        print(f"Rate limited by Discord. Retrying after {retry_after} seconds...")
                        await asyncio.sleep(float(retry_after))
                        delay *= 2
                        retry_count += 1
                        continue
                    elif response.status >= 500:
                        print(f"Server error ({response.status}). Retrying...")
                        await asyncio.sleep(delay)
                        delay *= 2
                        retry_count += 1
                        continue
                    elif response.status != 200:
                        text = await response.text()
                        logging.error(f"Failed to send webhook: {text}")
                        print(f"Failed to send webhook: {text}")
                    else:
                        return
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logging.error(f"Exception while sending webhook: {e}")
                print(f"Exception while sending webhook: {e}")
                await asyncio.sleep(delay)
                delay *= 2
                retry_count += 1
        print("Max retries reached for sending webhook.")

    async def close(self):
        await self.send_batch()
        await self.session.close()
        self.executor.shutdown(wait=True)

async def make_request_with_backoff(session, url, retries=5):
    delay = 1
    for attempt in range(retries):
        try:
            async with session.get(url, timeout=10) as response:
                if response.status == 429:
                    retry_after = response.headers.get("Retry-After", delay)
                    print(f"Rate limit hit. Retrying after {retry_after} seconds...")
                    await asyncio.sleep(float(retry_after))
                    delay *= 2
                    continue
                elif response.status >= 500:
                    print(f"Server error ({response.status}). Retrying...")
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                elif response.status != 200:
                    text = await response.text()
                    logging.error(f"Failed to fetch {url}: {text}")
                    print(f"Failed to fetch {url}: {text}")
                    return None
                data = await response.json()
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            print(f"Exception while fetching {url}: {e}. Retrying...")
            logging.error(f"Exception while fetching {url}: {e}")
            await asyncio.sleep(delay)
            delay *= 2
    print(f"Failed to get a valid response from {url} after {retries} attempts.")
    return None

async def get_badges(session, universe_id):
    badges = []
    cursor = ''
    while True:
        url = f"https://badges.roblox.com/v1/universes/{universe_id}/badges?limit=100&sortOrder=Asc"
        if cursor:
            url += f"&cursor={cursor}"
        data = await make_request_with_backoff(session, url)
        if not data:
            break
        if 'data' in data:
            badges.extend(data.get('data', []))
        else:
            break
        cursor = data.get('nextPageCursor')
        if not cursor:
            break
    return badges

async def get_badge_thumbnail_url(session, badge_id):
    url = f"https://thumbnails.roblox.com/v1/badges/icons?badgeIds={badge_id}&size=150x150&format=Png&isCircular=false"
    data = await make_request_with_backoff(session, url)
    if data and 'data' in data and data['data']:
        return data['data'][0].get('imageUrl', '')
    return ""

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
            "name": "Note: This isn't a confirmed game, just a newly uploaded badge from a top Roblox game.",
        },
        "footer": {
            "text": "Created by Poke",
            "icon_url": "https://cdn.discordapp.com/avatars/381611177664577537/a45fe102ee104ed3357b65e56559f96e.png?size=1024"
        }
    }
    await webhook_sender.add_embed(embed, badge_id)

async def process_universe(session, universe_id, badges_data, webhook_sender):
    fetched_badges = await get_badges(session, universe_id)
    universe_id_str = str(universe_id)
    new_badges_count = 0
    if fetched_badges:
        if universe_id_str in badges_data:
            existing_badge_ids = set(str(badge['id']) for badge in badges_data[universe_id_str]['data'])
            new_badges = [badge for badge in fetched_badges if str(badge['id']) not in existing_badge_ids]
            if new_badges:
                badges_data[universe_id_str]['data'].extend(new_badges)
                await asyncio.gather(*(process_new_badge(session, badge, webhook_sender) for badge in new_badges))
                new_badges_count = len(new_badges)
        else:
            badges_data[universe_id_str] = {"data": fetched_badges}
    return new_badges_count

async def load_universe_ids():
    async with aiofiles.open('hrefs.txt', 'r', encoding='utf-8') as f:
        return [line.strip() for line in await f.readlines()]

async def load_badges_data():
    if os.path.exists('badges.json'):
        async with aiofiles.open('badges.json', 'r', encoding='utf-8') as f:
            content = await f.read()
            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                logging.error(f"Error decoding badges.json: {e}")
                return {}
    return {}

async def save_badges_data(badges_data):
    if os.path.exists('badges.json'):
        async with aiofiles.open('badges.json', 'r', encoding='utf-8') as f:
            existing_data = await f.read()
            try:
                existing_data = json.loads(existing_data)
                for universe_id, new_data in badges_data.items():
                    if universe_id in existing_data:
                        existing_data[universe_id]['data'].extend(new_data['data'])
                    else:
                        existing_data[universe_id] = new_data
            except json.JSONDecodeError as e:
                logging.error(f"Error decoding badges.json: {e}")
                existing_data = badges_data
    else:
        existing_data = badges_data

    async with aiofiles.open('badges.json', 'w', encoding='utf-8') as f:
        await f.write(json.dumps(existing_data, indent=2))

async def main():
    debug_print("Starting main function")
    if not WEBHOOK_URL:
        print("Webhook URL not set. Please set the WEBHOOK_URL environment variable.")
        return

    webhook_sender = WebhookSender(WEBHOOK_URL)

    universe_ids = await load_universe_ids()
    badges_data = await load_badges_data()

    timeout = aiohttp.ClientTimeout(total=15)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        semaphore = asyncio.Semaphore(50)

        async def sem_task(universe_id):
            async with semaphore:
                return await process_universe(session, universe_id, badges_data, webhook_sender)

        tasks = [sem_task(universe_id) for universe_id in universe_ids]
        results = await asyncio.gather(*tasks)
        total_new_badges = sum(results)
        await webhook_sender.send_batch()
    await save_badges_data(badges_data)
    print(f"Processed all universes. New badges: {total_new_badges}")
    await webhook_sender.close()

if __name__ == "__main__":
    print("Running script")
    asyncio.run(main())
