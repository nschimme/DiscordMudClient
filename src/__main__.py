# Discord Mud Client
# Copyright (C) 2026 Nils Schimmelmann
# SPDX-License-Identifier: AGPL-3.0-or-later
# See LICENSE.md for the full license text.
import discord
from .bot import DiscordMudClient
from .config import TOKEN

def main():
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    client = DiscordMudClient(intents=intents)
    client.run(TOKEN)

if __name__ == "__main__":
    main()
