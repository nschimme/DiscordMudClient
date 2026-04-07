import discord
from discord import app_commands
from discord.ext import commands
import asyncio
from .config import MAX_INPUT_LENGTH, ANSI_TIMEOUT

class MudCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="play", description="Start playing the MUD in DMs")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def play_slash(self, interaction: discord.Interaction):
        user = interaction.user
        user_id = user.id

        session = self.bot.session_manager.get(user_id)
        if session:
            try:
                await session.channel.send("👋 **You're already connected!** Here is your active session.")
                await interaction.response.send_message("✅ You're already playing! I've bumped your DMs.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ I couldn't bump your DMs! Please check your privacy settings.", ephemeral=True)
            return

        if self.bot.session_manager.is_connecting(user_id):
            try:
                dm_channel = user.dm_channel or await user.create_dm()
                await dm_channel.send("⏳ **Still connecting...** please wait a moment!")
                await interaction.response.send_message("✅ You're already connecting! I've bumped your DMs.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ I couldn't bump your DMs! Please check your privacy settings.", ephemeral=True)
            return

        try:
            dm_channel = user.dm_channel or await user.create_dm()
            await interaction.response.send_message("✅ Check your DMs! I've started the connection process.", ephemeral=True)
            # Perform connection in background to avoid blocking and ensure quick response
            asyncio.create_task(self.bot.init_session(user, dm_channel))
        except discord.Forbidden:
            await interaction.response.send_message("❌ I couldn't send you a DM! Please enable 'Allow direct messages from server members' in your privacy settings.", ephemeral=True)

    @app_commands.command(name="connect", description="Connect to a MUD in DMs")
    @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.describe(url="The MUD URL (e.g. telnet://host:port, wss://host:port/path)")
    async def connect_slash(self, interaction: discord.Interaction, url: str = None):
        user = interaction.user
        user_id = user.id

        if self.bot.session_manager.get(user_id):
            await interaction.response.send_message("❌ You are already connected! Disconnect first to change servers.", ephemeral=True)
            return

        if self.bot.session_manager.is_connecting(user_id):
            await interaction.response.send_message("⏳ You are already connecting! Please wait a moment.", ephemeral=True)
            return

        try:
            dm_channel = interaction.channel
            await interaction.response.send_message("✅ Starting connection process...", ephemeral=True)
            asyncio.create_task(self.bot.init_session(user, dm_channel, url=url))
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)

    @app_commands.command(name="disconnect", description="Disconnect from the MUD")
    @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def disconnect_slash(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        if self.bot.session_manager.get(user_id):
            await interaction.response.send_message("🔌 *Disconnected.*")
            # Close session in background to prevent interaction timeout
            asyncio.create_task(self.bot.close_session(user_id))
        else:
            await interaction.response.send_message("❌ You are not currently connected.", ephemeral=True)

    @app_commands.command(name="return", description="Send a newline character to the MUD")
    @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def return_slash(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        session = self.bot.session_manager.get(user_id)
        if session:
            try:
                await session.protocol.send_text("\n")
                await interaction.response.send_message("✅ *Newline sent.*", ephemeral=True)
            except:
                await interaction.response.send_message("❌ Connection error while sending data.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ You are not currently connected.", ephemeral=True)

    @app_commands.command(name="terminal", description="Set terminal dimensions (NAWS)")
    @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.describe(width="The terminal width (1-65535)", height="The terminal height (1-65535)")
    async def terminal_slash(self, interaction: discord.Interaction, width: int, height: int):
        user_id = interaction.user.id
        session = self.bot.session_manager.get(user_id)
        if session:
            if not (1 <= width <= 65535) or not (1 <= height <= 65535):
                await interaction.response.send_message("❌ Invalid dimensions. Use values between 1 and 65535.", ephemeral=True)
                return
            try:
                await session.protocol.send_naws(width, height)
                await interaction.response.send_message(f"🖥️ *Terminal size set to {width}x{height}.*", ephemeral=True)
            except:
                await interaction.response.send_message("❌ Connection error while sending data.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ You are not currently connected.", ephemeral=True)

    @app_commands.command(name="password", description="Enter your password securely")
    @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.describe(password="The password to send")
    async def password_slash(self, interaction: discord.Interaction, password: str):
        user_id = interaction.user.id
        session = self.bot.session_manager.get(user_id)
        if session:
            try:
                await session.protocol.send_text(password + "\n", transliterate=False)
                await interaction.response.send_message("🔑 *Password sent securely.*", ephemeral=True)
            except:
                await interaction.response.send_message("❌ Connection error while sending data.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ You are not currently connected.", ephemeral=True)

    @app_commands.command(name="send", description="Send a command starting with / to the MUD")
    @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.describe(command="The command to send")
    async def send_slash(self, interaction: discord.Interaction, command: str):
        user_id = interaction.user.id
        session = self.bot.session_manager.get(user_id)
        if session:
            try:
                await session.protocol.send_text(command + "\n")
                # Respond with the command itself to provide a clean display
                await interaction.response.send_message(f"`{command}`")
            except:
                await interaction.response.send_message("❌ Connection error while sending data.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ You are not currently connected.", ephemeral=True)
