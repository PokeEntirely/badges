WEBHOOK_URL = "https://discord.com/api/webhooks/1342702266347683903/Syw7X5ZrJiVhI-Zl2ENg2QsURsdtjpZ5dOa0pRk-QAGRb8sy3x6zwj2i-ZJ2GtiMhozj"
PUSHOVER_API_KEY = "axaaescm93q62gppb117migpf4k8es"
PUSHOVER_USER_KEY = "uae6nsc26pq5d79bnoitj4dimyz7hx"

logging.basicConfig(
    filename="error.log",
    level=logging.ERROR,
    format="%(asctime)s:%(levelname)s:%(message)s"
)

class WebhookSender:
    def __init__(self, webhook_url):
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
        while True:
            async with self.lock:
                if not self.embeds:
                    break
                current_batch = self.embeds[:10]
                self.embeds = self.embeds[10:]
            await self.send_webhook({
                "content": "<@&1342702022771871826>",
                "embeds": current_batch,
                "attachments": []
            })
            if len(current_batch) == 10:
                await asyncio.sleep(10)

    async def send_webhook(self, payload):
        try:
            async with self.session.post(self.webhook_url, json=payload) as response:
                response.raise_for_status()
        except Exception as e:
            logging.error(f"Failed to send webhook: {e}")

    async def close(self):
        await self.send_batch()
        await self.session.close()
        self.executor.shutdown(wait=True)

async def send_pushover_notification(session, badge, thumbnail_url, game_name):
    url = "https://api.pushover.net/1/messages.json"
    data = {
        "token": PUSHOVER_API_KEY,
        "user": PUSHOVER_USER_KEY,
        "title": f"New Badge: {badge['name']}",
        "message": f"Game: {game_name}"
    }
    if thumbnail_url:
        data["url"] = thumbnail_url
        data["url_title"] = "Thumbnail"
    try:
        async with session.post(url, data=data) as response:
            response.raise_for_status()
    except Exception as e:
        logging.error(f"Failed to send pushover notification for badge {badge['id']}: {e}")

async def get_badges(session, universe_id):
    badges = []
    cursor = ""
    retry_count = 0
    max_retries = 5
    while True:
        url = f"https://badges.roblox.com/v1/universes/{universe_id}/badges?limit=100&sortOrder=Asc"
        if cursor:
            url = url + f"&cursor={cursor}"
        try:
            async with session.get(url, timeout=10) as response:
                if response.status == 429:
                    await asyncio.sleep(30)
                    continue
                response.raise_for_status()
                data = await response.json()
                badges_data = data.get("data", [])
                badges.extend(badges_data)
                cursor = data.get("nextPageCursor")
                if not cursor:
                    break
                retry_count = 0
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            retry_count = retry_count + 1
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
                if data["data"]:
                    image_url = data["data"][0].get("imageUrl", "")
                    return image_url
                return ""
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            retry_count = retry_count + 1
            if retry_count >= max_retries:
                return ""
            await asyncio.sleep(5)

async def process_new_badge(session, badge, webhook_sender):
    badge_id = badge["id"]
    description = badge.get("description")
    if not description:
        description = "None."
    created_time = badge["created"]
    try:
        time_string = created_time.replace("Z", "+00:00")
        creation_time_obj = datetime.fromisoformat(time_string)
        creation_timestamp = int(creation_time_obj.timestamp())
        discord_timestamp = f"<t:{creation_timestamp}:f>"
    except Exception as e:
        logging.error(f"Error parsing creation time for badge {badge_id}: {e}")
        discord_timestamp = "Unknown"
    awarding_universe = badge.get("awardingUniverse", {})
    game_name = awarding_universe.get("name", "Unknown Game")
    game_id = awarding_universe.get("rootPlaceId", "0")
    if game_id != "0":
        game_link = f"https://www.roblox.com/games/{game_id}/game"
    else:
        game_link = game_name
    if game_id != "0":
        game_name_hyperlink = f"[{game_name}]({game_link})"
    else:
        game_name_hyperlink = game_name
    thumbnail_url = await get_badge_thumbnail_url(session, badge_id)
    embed = {
        "title": badge["name"],
        "url": f"https://www.roblox.com/badges/{badge_id}/",
        "color": 16762779,
        "fields": [
            {
                "name": "Badge ID",
                "value": str(badge_id),
                "inline": True
            },
            {
                "name": "Game Name",
                "value": game_name_hyperlink,
                "inline": True
            },
            {
                "name": "Creation Time",
                "value": discord_timestamp,
                "inline": False
            },
            {
                "name": "Description",
                "value": description,
                "inline": False
            }
        ],
        "thumbnail": {
            "url": thumbnail_url
        },
        "author": {
            "name": "New Badge Uploaded"
        },
        "footer": {
            "text": "Made by Poke • @PokeTheMagnific on X",
            "icon_url": "https://i.imgur.com/Od3xppJ.png"
        }
    }
    await webhook_sender.add_embed(embed, badge_id)
    await send_pushover_notification(session, badge, thumbnail_url, game_name)

async def process_universe(session, universe_id, badges_data, webhook_sender):
    fetched_badges = await get_badges(session, universe_id)
    universe_id_str = str(universe_id)
    new_badges_count = 0
    if universe_id_str in badges_data:
        existing_badge_ids = set()
        for badge in badges_data[universe_id_str]["data"]:
            badge_id_str = str(badge["id"])
            existing_badge_ids.add(badge_id_str)
        new_badges = []
        for badge in fetched_badges:
            current_badge_id = str(badge["id"])
            if current_badge_id not in existing_badge_ids:
                new_badges.append(badge)
        if new_badges:
            badges_data[universe_id_str]["data"].extend(new_badges)
            tasks = []
            for badge in new_badges:
                tasks.append(process_new_badge(session, badge, webhook_sender))
            await asyncio.gather(*tasks)
            new_badges_count = len(new_badges)
    else:
        badges_data[universe_id_str] = {"data": fetched_badges}
        new_badges_count = len(fetched_badges)
    return new_badges_count

async def load_universe_ids():
    universe_ids = []
    async with aiofiles.open("games.txt", "r", encoding="utf-8") as f:
        lines = await f.readlines()
        for line in lines:
            stripped_line = line.strip()
            universe_ids.append(stripped_line)
    return universe_ids

async def load_badges_data():
    if os.path.exists("badges.json"):
        async with aiofiles.open("badges.json", "r", encoding="utf-8") as f:
            content = await f.read()
            badges = json.loads(content)
        return badges
    return {}

async def save_badges_data(badges_data):
    async with aiofiles.open("badges.json", "w", encoding="utf-8") as f:
        data_string = json.dumps(badges_data, indent=2)
        await f.write(data_string)

async def main():
    subprocess.run(["python", "lines.py"])
    webhook_sender = WebhookSender(WEBHOOK_URL)
    universe_ids = await load_universe_ids()
    badges_data = await load_badges_data()
    check = 0
    while True:
        check = check + 1
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            tasks = []
            for universe_id in universe_ids:
                task = process_universe(session, universe_id, badges_data, webhook_sender)
                tasks.append(task)
            results = await asyncio.gather(*tasks)
            total_new_badges = 0
            for result in results:
                total_new_badges = total_new_badges + result
            await webhook_sender.send_batch()
        await save_badges_data(badges_data)
        print(f"check {check}: {total_new_badges} badges found")
        await asyncio.sleep(60)

asyncio.run(main())
