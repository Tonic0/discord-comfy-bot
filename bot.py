import discord
import aiohttp
import asyncio
import uuid
import json
import io
import random
import signal
import sys
from PIL import Image
from collections import deque

# =========================
# CONFIG
# =========================

DISCORD_TOKEN = "TOKEN"
COMFYUI_URL = "http://127.0.0.1:8188"

WORKFLOWS = {
    "!z":  "workflow_square.json",
    "!zl": "workflow_landscape.json",
    "!zp": "workflow_portrait.json",
}

REROLL_PREFIX = "!zr"
POLL_INTERVAL = 2
MAX_WAIT_SECONDS = 300

# =========================
# DISCORD SETUP
# =========================

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# =========================
# STATE
# =========================

job_queue = deque()
queue_lock = asyncio.Lock()
queue_event = asyncio.Event()

# Per-user memory: user_id -> (prompt, workflow_file)
last_request_per_user = {}

# =========================
# SIGNAL HANDLING
# =========================

def shutdown_signal_handler(sig, frame):
    print("Shutting down...")
    sys.exit(0)

signal.signal(signal.SIGTERM, shutdown_signal_handler)
signal.signal(signal.SIGINT, shutdown_signal_handler)

# =========================
# COMFYUI HELPERS
# =========================

def set_positive_prompt_only(workflow: dict, prompt_text: str):
    positive_node_ids = set()

    for node_id, node in workflow.items():
        if node.get("class_type") == "KSampler":
            positive = node["inputs"].get("positive")
            if isinstance(positive, list):
                positive_node_ids.add(str(positive[0]))

    for node_id in positive_node_ids:
        node = workflow.get(node_id)
        if node and node.get("class_type") == "CLIPTextEncode":
            node["inputs"]["text"] = prompt_text


async def queue_prompt(session, prompt_text, workflow_file):
    with open(workflow_file, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    set_positive_prompt_only(workflow, prompt_text)

    for node in workflow.values():
        if node.get("class_type") == "KSampler":
            node["inputs"]["seed"] = random.randint(1, 2**31 - 1)

    payload = {
        "prompt": workflow,
        "client_id": str(uuid.uuid4())
    }

    async with session.post(f"{COMFYUI_URL}/prompt", json=payload) as r:
        r.raise_for_status()
        data = await r.json()
        return data["prompt_id"]


async def get_preview_image(session, prompt_id):
    async with session.get(f"{COMFYUI_URL}/history") as r:
        r.raise_for_status()
        history = await r.json()

    if prompt_id not in history:
        return None

    outputs = history[prompt_id].get("outputs", {})
    for node in outputs.values():
        if "images" in node and node["images"]:
            return node["images"][0]

    return None


async def wait_for_image(session, prompt_id):
    waited = 0
    while waited < MAX_WAIT_SECONDS:
        await asyncio.sleep(POLL_INTERVAL)
        waited += POLL_INTERVAL

        image_info = await get_preview_image(session, prompt_id)
        if image_info:
            return image_info

    return None


async def fetch_and_convert_image(session, image_info):
    async with session.get(
        f"{COMFYUI_URL}/view",
        params={
            "filename": image_info["filename"],
            "subfolder": image_info.get("subfolder", ""),
            "type": image_info.get("type", "preview")
        }
    ) as r:
        r.raise_for_status()
        content = await r.read()

    png_bytes = io.BytesIO(content)
    img = Image.open(png_bytes)

    jpeg_bytes = io.BytesIO()
    img.convert("RGB").save(jpeg_bytes, format="JPEG", quality=95)
    jpeg_bytes.seek(0)

    return jpeg_bytes

# =========================
# QUEUE WORKER
# =========================

async def job_worker():
    async with aiohttp.ClientSession() as session:
        while True:
            await queue_event.wait()

            async with queue_lock:
                if not job_queue:
                    queue_event.clear()
                    continue
                message, user_id, prompt, workflow_file = job_queue.popleft()

            try:
                prompt_id = await queue_prompt(session, prompt, workflow_file)
                image_info = await wait_for_image(session, prompt_id)

                if not image_info:
                    await message.channel.send("Image generation timed out.")
                    continue

                jpeg_bytes = await fetch_and_convert_image(session, image_info)

                await message.channel.send(
                    file=discord.File(fp=jpeg_bytes, filename="generated.jpg")
                )

            except Exception as e:
                print("ERROR:", e)
                await message.channel.send("An error occurred during generation.")

# =========================
# DISCORD EVENTS
# =========================

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    client.loop.create_task(job_worker())


@client.event
async def on_message(message):
    if message.author.bot:
        return

    user_id = message.author.id
    prompt = None
    workflow_file = None

    # Reroll (per-user)
    if message.content.startswith(REROLL_PREFIX):
        if user_id not in last_request_per_user:
            await message.channel.send("You have no previous prompt to reroll.")
            return
        prompt, workflow_file = last_request_per_user[user_id]

    else:
        for prefix in sorted(WORKFLOWS.keys(), key=len, reverse=True):
            if message.content.startswith(prefix + " ") or message.content == prefix:
                prompt = message.content[len(prefix):].strip()
                if not prompt:
                    await message.channel.send("Please provide a prompt.")
                    return
                workflow_file = WORKFLOWS[prefix]
                last_request_per_user[user_id] = (prompt, workflow_file)
                break
        else:
            return

    async with queue_lock:
        job_queue.append((message, user_id, prompt, workflow_file))
        position = len(job_queue)
        queue_event.set()

    await message.channel.send(
        f"Your request has been added to the queue. Position: {position}"
    )

# =========================
# RUN
# =========================

client.run(DISCORD_TOKEN)
