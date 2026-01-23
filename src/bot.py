import discord
from discord.ext import commands
import asyncio
import socket
import signal
import ssl
from datetime import datetime
from .config import MUD_HOST, MUD_PORT, MUD_TLS, MAX_INPUT_LENGTH, MAX_BUFFER_SIZE, ANSI_TIMEOUT
from .protocol import Telnet
from .session import MudSession, SessionManager
from .commands import MudCommands

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

                raw_text = session.protocol.feed(data)
                if raw_text:
                    session.buffer = (session.buffer + raw_text)[-MAX_BUFFER_SIZE:]
                    await session.msg_queue.put(True)
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

    async def init_session(self, user, channel):
        user_id = user.id
        display_name = str(user)

        if self.session_manager.get(user_id) or self.session_manager.is_connecting(user_id):
            return

        self.session_manager.start_connecting(user_id)
        self.log_event(user_id, display_name, f"Attempting to connect to {MUD_HOST}:{MUD_PORT} (TLS: {MUD_TLS})...")
        await channel.send(f"⏳ *Connecting to {MUD_HOST}:{MUD_PORT}...*\n(Tip: Type `/` to see available Slash Commands)")

        try:
            reader, writer = None, None
            if MUD_TLS:
                ssl_context = ssl.create_default_context()
                try:
                    reader, writer = await asyncio.open_connection(MUD_HOST, MUD_PORT, ssl=ssl_context)
                except (ssl.SSLError, ConnectionRefusedError) as e:
                    if isinstance(e, ssl.SSLError):
                        self.log_event(user_id, display_name, f"TLS Verification failed: {str(e)}. Retrying leniently...")
                        await channel.send("⚠️ *SSL verification failed. Continuing with unverified TLS.*")
                        ssl_context = ssl.create_default_context()
                        ssl_context.check_hostname = False
                        ssl_context.verify_mode = ssl.CERT_NONE
                        reader, writer = await asyncio.open_connection(MUD_HOST, MUD_PORT, ssl=ssl_context)
                    else:
                        raise e
            else:
                reader, writer = await asyncio.open_connection(MUD_HOST, MUD_PORT)

            sock = writer.get_extra_info('socket')
            if sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

            session = MudSession(self.session_manager, user_id, reader, writer, channel, display_name)
            self.session_manager.sessions[user_id] = session
            is_encrypted = writer.get_extra_info('ssl_object') is not None
            self.log_event(user_id, display_name, f"Successfully connected to MUD (Encrypted: {is_encrypted}).")

            await session.protocol.send_command(Telnet.WILL, Telnet.TTYPE)
            await session.protocol.send_command(Telnet.WILL, Telnet.NAWS)
            await session.protocol.send_naws()

            session.listener_task = asyncio.create_task(self.mud_listener(user_id, channel, display_name))
            self.session_manager.stop_connecting(user_id)
        except ConnectionRefusedError:
            self.session_manager.stop_connecting(user_id)
            await channel.send("❌ Connection refused: The MUD server is likely down.")
        except (socket.timeout, asyncio.TimeoutError):
            self.session_manager.stop_connecting(user_id)
            await channel.send("❌ Connection timed out.")
        except Exception as e:
            self.session_manager.stop_connecting(user_id)
            self.log_event(user_id, display_name, f"Connection failed: {str(e)}")
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
        await self.init_session(message.author, message.channel)
