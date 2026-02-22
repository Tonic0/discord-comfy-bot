import discord
import requests
import uuid
import json
import time
import io
import random
import signal
import sys
from PIL import Image

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

# Stores (prompt, workflow_file)
client.last_request = None

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


def queue_prompt(prompt_text: str, workflow_file: str) -> str:
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

    r = requests.post(f"{COMFYUI_URL}/prompt", json=payload)
    r.raise_for_status()
    return r.json()["prompt_id"]


def get_preview_image(prompt_id: str):
    r = requests.get(f"{COMFYUI_URL}/history")
    r.raise_for_status()
    history = r.json()

    if prompt_id not in history:
        return None

    outputs = history[prompt_id].get("outputs", {})
    for node in outputs.values():
        if "images" in node and node["images"]:
            return node["images"][0]

    return None


def wait_for_image(prompt_id: str):
    waited = 0
    while waited < MAX_WAIT_SECONDS:
        time.sleep(POLL_INTERVAL)
        waited += POLL_INTERVAL

        image_info = get_preview_image(prompt_id)
        if image_info:
            return image_info

    return None

# =========================
# DISCORD EVENTS
# =========================

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")

@client.event
async def on_message(message):
    if message.author.bot:
        return

    prompt = None
    workflow_file = None

    # Reroll
    if message.content.startswith(REROLL_PREFIX):
        if not client.last_request:
            await message.channel.send("No previous prompt to reroll.")
            return
        prompt, workflow_file = client.last_request

    # New generation
    else:
        # Match longest prefixes first (!zl / !zp before !z)
        for prefix in sorted(WORKFLOWS.keys(), key=len, reverse=True):
            if message.content.startswith(prefix + " ") or message.content == prefix:
                prompt = message.content[len(prefix):].strip()
                if not prompt:
                    await message.channel.send("Please provide a prompt.")
                    return
                workflow_file = WORKFLOWS[prefix]
                client.last_request = (prompt, workflow_file)
                break
        else:
            return

    await message.channel.send("Generating image...")

    try:
        prompt_id = queue_prompt(prompt, workflow_file)
        image_info = wait_for_image(prompt_id)

        if not image_info:
            await message.channel.send("Image generation timed out.")
            return

        r = requests.get(
            f"{COMFYUI_URL}/view",
            params={
                "filename": image_info["filename"],
                "subfolder": image_info.get("subfolder", ""),
                "type": image_info.get("type", "preview")
            }
        )
        r.raise_for_status()

        png_bytes = io.BytesIO(r.content)
        img = Image.open(png_bytes)

        jpeg_bytes = io.BytesIO()
        img.convert("RGB").save(jpeg_bytes, format="JPEG", quality=95)
        jpeg_bytes.seek(0)

        await message.channel.send(
            file=discord.File(fp=jpeg_bytes, filename="generated.jpg")
        )

    except Exception as e:
        print("ERROR:", e)
        await message.channel.send("An error occurred during generation.")

# =========================
# RUN
# =========================

client.run(DISCORD_TOKEN)