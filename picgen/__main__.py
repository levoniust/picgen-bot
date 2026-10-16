"""Starts PIC_GEN: python -m picgen

Environment:
  PICGEN_DATA        data folder (settings.json, jobs/, pictures/), default ./data
  DISCORD_TOKEN      the bot token, or
  PICGEN_TOKEN_FILE  a file that contains it (default: /run/secrets/discord_token)
"""
import asyncio
import json
import logging
import os
import socket
import sys
import time
from pathlib import Path

from .bot import PicGenBot
from .config import DEFAULTS, Settings
from .engine import Engine


def read_token():
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if token:
        return token
    path = Path(os.environ.get("PICGEN_TOKEN_FILE", "/run/secrets/discord_token"))
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    sys.exit(f"No Discord token: set DISCORD_TOKEN or put it in {path} (PICGEN_TOKEN_FILE).")


def wait_for_network(host="discord.com", seconds=120):
    """A freshly started container can have no DNS for a few seconds; wait instead of crashing the login."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            socket.getaddrinfo(host, 443)
            return
        except OSError as ex:
            if time.monotonic() > deadline:
                sys.exit(f"No network: cannot resolve {host} ({ex}).")
            time.sleep(2)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    data = Path(os.environ.get("PICGEN_DATA", "data"))
    data.mkdir(parents=True, exist_ok=True)
    settings_file = data / "settings.json"
    if not settings_file.exists():
        settings_file.write_text(json.dumps(DEFAULTS, indent=2) + "\n", encoding="utf-8")
        logging.getLogger("picgen").info("Wrote default settings to %s - edit comfy_url (and llm) there.", settings_file)
    settings = Settings(data)
    bot = PicGenBot(Engine(settings, data))
    wait_for_network()
    bot.run(read_token(), log_handler=None)


if __name__ == "__main__":
    main()
