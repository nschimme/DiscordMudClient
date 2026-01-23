# Discord Mud Client

A simple Discord bot that acts as a MUD client to play a configured MUD over DMs.

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

### 2. Environment Variables
Create a .env file in the root directory and paste your token:

```text
DISCORD_TOKEN=YourBotTokenHere
MUD_HOST=mume.org
MUD_PORT=4242
MUD_TLS=false
```

### 3. Install
1. Run: `docker-compose up --build -d`
2. Install your App into your Discord Channel
3. DM the Discord Bot to play.

---

## 🎮 Commands
Available Slash Commands:

- `/play`: (Guild Only) Start playing the MUD in DMs.
- `/disconnect`: (DM Only) End your current MUD session.
- `/return`: (DM Only) Send a newline character to the MUD.
- `/password <pass>`: (DM Only) Enter your password securely.
- `/send <command>`: (DM Only) Send a command starting with / to the MUD.
