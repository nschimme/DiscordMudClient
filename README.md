# Discord Mud Client

A simple Discord bot that acts as a MUD client over DMs.

---

## 🛠 Setup Instructions

### 1. Discord Bot Configuration
1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create a **New Application**.
3. Under the **Bot** tab:
   - Reset/Copy your **Token**.
   - Enable **Privileged Gateway Intents**: 
     - `Presence Intent`
     - `Server Members Intent`
     - `Message Content Intent` (Required to read MUD commands)
4. Save Changes.

### 2. Install
Choose one of the following:

**A. Use the pre-built image (Recommended)**
Create a `docker-compose.yml` file:
```yaml
version: '3.8'
services:
  mume-bot:
    image: ghcr.io/nschimme/discordmudclient:latest
    restart: unless-stopped
    env_file: .env
    tty: true
    stdin_open: true
```

**B. Clone the source**
```bash
git clone https://github.com/nschimme/DiscordMudClient.git
cd DiscordMudClient
```

### 3. Configure & Run
1. Create a `.env` file in the same folder:
   ```text
   DISCORD_TOKEN=YourBotTokenHere
   MUD_HOST=mume.org
   MUD_PORT=4242
   MUD_SCHEME=telnets   # 'telnet', 'telnets', 'ws', 'wss'
   MUD_PATH=/           # Path for connections (mostly for websockets)
   ```
2. Start the bot: `docker-compose up -d`
3. Install your App into your Discord Channel
4. DM the Discord Bot to play.

---

## 🎮 Commands
Available Slash Commands:

- `/play`: Start playing the default MUD in DMs.
- `/connect <url>`: (DM Only) Connect to a specific MUD.
    - Example: `/connect telnet://mume.org:4242`
    - Supported protocols: `telnet://`, `telnets://`, `ws://`, `wss://`.
    - Defaults to `telnets://` if no protocol is provided.
- `/disconnect`: (DM Only) End your current MUD session.
- `/return`: (DM Only) Send a newline character to the MUD.
- `/password <pass>`: (DM Only) Enter your password securely.
- `/send <command>`: (DM Only) Send a command starting with / to the MUD.
