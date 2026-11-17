# Discord Mud Client
# Copyright (C) 2026 Nils Schimmelmann
# SPDX-License-Identifier: AGPL-3.0-or-later
# See LICENSE.md for the full license text.
import os
from .utils import get_version

# --- CONFIGURATION ---
APP_VERSION = get_version()
TOKEN = os.getenv('DISCORD_TOKEN')
MUD_HOST = os.getenv('MUD_HOST', 'mume.org')
MUD_PORT = os.getenv('MUD_PORT', '4242')
MUD_SCHEME = os.getenv('MUD_SCHEME', 'telnets').lower() # 'telnet', 'telnets', 'ws', 'wss'
MUD_PATH = os.getenv('MUD_PATH', '/')
TRANSLITERATE = os.getenv('TRANSLITERATE', 'True').lower() == 'true'

DEFAULT_SOURCE_URL = 'https://github.com/nschimme/DiscordMudClient'
SOURCE_URL = os.getenv('SOURCE_URL', DEFAULT_SOURCE_URL)  # AGPL §13: offer source to users

# Constants
MAX_BUFFER_SIZE = 50000  # Prevent memory exhaustion
MAX_INPUT_LENGTH = 500   # Prevent MUD buffer flooding
ANSI_TIMEOUT = 2.0       # Timeout for network write/drain operations
SESSION_CLOSE_TIMEOUT = 2.0
