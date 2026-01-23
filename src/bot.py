import discord
from discord.ext import commands
import asyncio
import socket
import signal
from datetime import datetime
from .config import MUD_HOST, MUD_PORT, MUD_SCHEME, MUD_PATH, MAX_INPUT_LENGTH, MAX_BUFFER_SIZE, ANSI_TIMEOUT
from .protocol import Telnet, DecompressionError
from .session import MudSession, SessionManager
from .commands import MudCommands
from .utils import parse_mud_url
from .connection import connect_mud

class DiscordMudClient(commands.Bot):
    def __init__(self, *args, **kwargs):
        # Using an obscure prefix since we want to only use Slash Commands,
        # but commands.Bot still requires one.
        super().__init__(command_prefix='\x00', help_command=None, *args, **kwargs)
        self.session_manager = SessionManager(self)
        self.is_shutting_down = False

    async def setup_hook(self):
        await self.add_cog(MudCommands(self))
        try:
            # Sync commands globally. Slash commands can take time to propagate in guilds,
            # but usually appear instantly in DMs.
            synced = await self.tree.sync()
            print(f"--- Synced {len(synced)} global slash commands ---")
        except Exception as e:
            print(f"--- Failed to sync slash commands: {e} ---")

    def log_event(self, user_id, username, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] [User: {username} ({user_id})] {message}")

    async def on_ready(self):
        await self.change_presence(activity=discord.Game(name="DM to Play"))
        # Redundant sync to ensure everything is registered
        try:
            await self.tree.sync()
        except: pass
        print(f'--- DiscordMudClient Online as {self.user} ---')

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

    async def shutdown(self):
        if self.is_shutting_down: return
        self.is_shutting_down = True
        self.log_event("SYSTEM", "CORE", "Shutdown signal received. Closing all sessions...")

        uids = list(self.session_manager.sessions.keys())

        async def notify_and_close(uid):
            session = self.session_manager.get(uid)
            if session:
                try:
                    await session.channel.send("🛑 **Bot Shutdown:** Bridge closing. Your session has ended.")
                except: pass
                await self.session_manager.close_session(uid)

        if uids:
            await asyncio.gather(*(notify_and_close(uid) for uid in uids), return_exceptions=True)

        await self.close()

    async def close_session(self, user_id):
        """Helper to close session via manager."""
        await self.session_manager.close_session(user_id)

    async def mud_listener(self, user_id, channel, username):
        session = self.session_manager.get(user_id)
        if not session: return
        try:
            while True:
                data = await session.reader.read(8192)
                if not data:
                    self.log_event(user_id, username, "Connection closed by remote MUD host.")
                    break

                try:
                    raw_text = session.protocol.feed(data)
                    if raw_text:
                        session.buffer = (session.buffer + raw_text)[-MAX_BUFFER_SIZE:]
                        await session.msg_queue.put(True)
                except DecompressionError as e:
                    self.log_event(user_id, username, f"Decompression error: {e}")
                    try:
                        await channel.send("❌ **Compression Error:** The compressed data stream from the MUD is corrupted. Closing session.")
                    except: pass
                    break
        except (ConnectionResetError, BrokenPipeError):
            self.log_event(user_id, username, "Connection reset by peer.")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.log_event(user_id, username, f"Listener Error: {str(e)}")
        finally:
            if not self.is_shutting_down:
                try:
                    if self.session_manager.get(user_id):
                        await channel.send("⚠️ *Connection closed.*")
                except: pass
            await self.session_manager.close_session(user_id)

    async def init_session(self, user, channel, url=None):
        user_id = user.id
        display_name = str(user)

        if self.session_manager.get(user_id) or self.session_manager.is_connecting(user_id):
            return

        # Determine connection parameters
        if url:
            parsed = parse_mud_url(url)
            if not parsed:
                await channel.send("❌ Invalid URL format.")
                return
            protocol, host, port, path = parsed
        else:
            protocol = MUD_SCHEME
            host = MUD_HOST
            port = int(MUD_PORT)
            path = MUD_PATH

        if not host or not port:
            await channel.send("❌ Missing host or port.")
            return

        self.session_manager.start_connecting(user_id)

        # Display connection info
        conn_display = f"{protocol}://{host}:{port}{path if 'ws' in protocol else ''}"
        self.log_event(user_id, display_name, f"Attempting to connect to {conn_display}...")
        await channel.send(f"⏳ *Connecting to {conn_display}...*\n(Tip: Type `/` to see available Slash Commands)")

        async def on_warning(msg):
            self.log_event(user_id, display_name, msg)
            await channel.send(f"⚠️ *SSL verification failed. Continuing with unverified {protocol.upper()}.*")

        try:
            reader, writer = await connect_mud(protocol, host, port, path, on_warning=on_warning)
            session = MudSession(self.session_manager, user_id, reader, writer, channel, display_name)
            self.session_manager.sessions[user_id] = session

            # Check for encryption
            is_encrypted = False
            if protocol in ('telnets', 'wss'):
                is_encrypted = True
            elif hasattr(writer, 'get_extra_info'):
                # Handle both asyncio StreamWriter (returns SSL object or None)
                # and our WebSocket adapter (returns True/False)
                info = writer.get_extra_info('ssl_object')
                is_encrypted = bool(info)

            self.log_event(user_id, display_name, f"Successfully connected to MUD (Encrypted: {is_encrypted}).")

            await session.protocol.send_command(Telnet.WILL, Telnet.TTYPE)
            await session.protocol.send_command(Telnet.WILL, Telnet.NAWS)
            await session.protocol.send_naws()

            session.listener_task = asyncio.create_task(self.mud_listener(user_id, channel, display_name))
            self.session_manager.stop_connecting(user_id)

        except Exception as e:
            self.session_manager.stop_connecting(user_id)
            self.log_event(user_id, display_name, f"Connection failed: {str(e)}")
            if isinstance(e, ConnectionRefusedError):
                await channel.send("❌ Connection refused: The MUD server is likely down.")
            elif isinstance(e, (socket.timeout, asyncio.TimeoutError)):
                await channel.send("❌ Connection timed out.")
            else:
                await channel.send(f"❌ Could not connect: {type(e).__name__}")

    async def on_message(self, message):
        if message.author.bot or self.is_shutting_down: return
        user_id = message.author.id
        display_name = str(message.author)

        # If not in DMs, ignore everything (Slash commands are interactions, not messages)
        if message.guild: return

        if len(message.content) > MAX_INPUT_LENGTH:
            await message.channel.send(f"❌ Input too long (Max {MAX_INPUT_LENGTH} characters).")
            return

        session = self.session_manager.get(user_id)
        if session:
            if session.echo_off:
                # Password mode
                await message.channel.send("⚠️ **Security Warning:** Please use the `/password` command to enter your password instead of typing it directly.")

            try:
                await session.protocol.send_text(message.content + "\n")
            except:
                pass # safe_send handles logging and closing
            return

        # Start a new session if they DM us and don't have one
        if not self.session_manager.is_connecting(user_id):
            await self.init_session(message.author, message.channel)
