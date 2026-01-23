import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import os
import socket
import signal
import ssl
import codecs
from datetime import datetime

# --- CONFIGURATION ---
TOKEN = os.getenv('DISCORD_TOKEN')
MUD_HOST = os.getenv('MUD_HOST')
MUD_PORT = os.getenv('MUD_PORT', '4242')
MUD_TLS = os.getenv('MUD_TLS', 'false').lower() == 'true'
MAX_BUFFER_SIZE = 50000  # Prevent memory exhaustion
MAX_INPUT_LENGTH = 500   # Prevent MUD buffer flooding

class Telnet:
    IAC, DONT, DO, WONT, WILL = 255, 254, 253, 252, 251
    SB, SE = 250, 240
    ECHO, TTYPE, NAWS, CHARSET = 1, 24, 31, 42
    IS, SEND, REQUEST, ACCEPTED, REJECTED = 0, 1, 1, 2, 3

class TelnetParser:
    def __init__(self, client, writer, user_id, username):
        self.client = client
        self.writer = writer
        self.user_id = user_id
        self.username = username
        self.state = "TEXT"
        self.sb_option = None
        self.sb_data = bytearray()
        self.iac_cmd = None
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='ignore')
        self.current_ansi = bytearray()

    def feed(self, data):
        output_text = ""
        for byte in data:
            if self.state == "TEXT":
                if byte == Telnet.IAC:
                    self.state = "IAC"
                elif byte == 27: # ESC
                    self.state = "ESC"
                    self.current_ansi = bytearray([27])
                else:
                    output_text += self.decoder.decode(bytes([byte]))
            elif self.state == "ESC":
                self.current_ansi.append(byte)
                if byte == ord('['):
                    self.state = "CSI"
                else:
                    output_text += self.decoder.decode(self.current_ansi)
                    self.state = "TEXT"
            elif self.state == "CSI":
                self.current_ansi.append(byte)
                if 0x40 <= byte <= 0x7E:
                    output_text += self.decoder.decode(self.current_ansi)
                    self.state = "TEXT"
                elif len(self.current_ansi) > 32: # Safety limit
                    output_text += self.decoder.decode(self.current_ansi)
                    self.state = "TEXT"
            elif self.state == "IAC":
                if byte == Telnet.IAC:
                    output_text += self.decoder.decode(bytes([255]))
                    self.state = "TEXT"
                elif byte in (Telnet.WILL, Telnet.WONT, Telnet.DO, Telnet.DONT):
                    self.iac_cmd = byte
                    self.state = "IAC_COMMAND"
                elif byte == Telnet.SB:
                    self.state = "SB"
                    self.sb_data = bytearray()
                else:
                    self.state = "TEXT"
            elif self.state == "IAC_COMMAND":
                self.handle_command(self.iac_cmd, byte)
                self.state = "TEXT"
            elif self.state == "SB":
                self.sb_option = byte
                self.state = "SB_DATA"
            elif self.state == "SB_DATA":
                if byte == Telnet.IAC:
                    self.state = "SB_IAC"
                else:
                    self.sb_data.append(byte)
            elif self.state == "SB_IAC":
                if byte == Telnet.SE:
                    self.handle_subnegotiation(self.sb_option, self.sb_data)
                    self.state = "TEXT"
                elif byte == Telnet.IAC:
                    self.sb_data.append(Telnet.IAC)
                    self.state = "SB_DATA"
                else:
                    self.state = "TEXT"
        return output_text

    def handle_command(self, cmd, opt):
        if opt == Telnet.ECHO:
            asyncio.create_task(self.client.send_telnet_cmd(self.writer, Telnet.IAC, (Telnet.DO if cmd == Telnet.WILL else Telnet.DONT), Telnet.ECHO))
            session = self.client.sessions.get(self.user_id)
            if session:
                if cmd == Telnet.WILL:
                    if not session.echo_off:
                        session.echo_off = True
                        asyncio.create_task(session.channel.send("🔑 **Password Mode:** The MUD has disabled echo. Please use `/password <your_password>` (Slash Command) for security, as bots cannot delete your messages in DMs."))
                else:
                    session.echo_off = False
        elif opt == Telnet.TTYPE and cmd == Telnet.DO:
            asyncio.create_task(self.client.send_telnet_cmd(self.writer, Telnet.IAC, Telnet.WILL, Telnet.TTYPE))
        elif opt == Telnet.NAWS and cmd == Telnet.DO:
            asyncio.create_task(self.client.send_naws(self.writer))
        elif opt == Telnet.CHARSET and cmd == Telnet.DO:
            asyncio.create_task(self.client.send_telnet_cmd(self.writer, Telnet.IAC, Telnet.WILL, Telnet.CHARSET))
        elif opt == Telnet.CHARSET and cmd == Telnet.WILL:
            asyncio.create_task(self.client.send_telnet_cmd(self.writer, Telnet.IAC, Telnet.DO, Telnet.CHARSET))

    def handle_subnegotiation(self, opt, data):
        if opt == Telnet.TTYPE and len(data) > 0 and data[0] == Telnet.SEND:
            identity = f"DiscordMudClient (UID:{self.user_id})"
            packet = bytes([Telnet.IAC, Telnet.SB, Telnet.TTYPE, Telnet.IS]) + \
                     identity.encode('ascii', errors='ignore') + \
                     bytes([Telnet.IAC, Telnet.SE])
            self.writer.write(packet)
            asyncio.create_task(self.writer.drain())
        elif opt == Telnet.CHARSET and len(data) > 0 and data[0] == Telnet.REQUEST:
            try:
                charsets = data[1:].decode('ascii', errors='ignore')
                if 'UTF-8' in charsets.upper():
                    packet = bytes([Telnet.IAC, Telnet.SB, Telnet.CHARSET, Telnet.ACCEPTED]) + \
                             "UTF-8".encode('ascii') + \
                             bytes([Telnet.IAC, Telnet.SE])
                    self.writer.write(packet)
                    asyncio.create_task(self.writer.drain())
                else:
                    packet = bytes([Telnet.IAC, Telnet.SB, Telnet.CHARSET, Telnet.REJECTED]) + \
                             bytes([Telnet.IAC, Telnet.SE])
                    self.writer.write(packet)
                    asyncio.create_task(self.writer.drain())
            except: pass

class MudSession:
    def __init__(self, client, user_id, reader, writer, channel, username):
        self.client = client
        self.user_id = user_id
        self.reader = reader
        self.writer = writer
        self.channel = channel
        self.username = username
        self.parser = TelnetParser(client, writer, user_id, username)
        self.echo_off = False
        self.buffer = ""
        self.msg_queue = asyncio.Queue()
        self.worker_task = asyncio.create_task(self.worker())

    async def worker(self):
        try:
            while True:
                await self.msg_queue.get()
                await asyncio.sleep(0.15)
                while not self.msg_queue.empty():
                    self.msg_queue.get_nowait()

                while self.buffer and not self.client.is_shutting_down:
                    # Capture current buffer to work with a stable snapshot
                    current_snapshot = self.buffer
                    chunk, remainder = self.client.split_buffer(current_snapshot)

                    if not chunk.strip():
                        # If the chunk is empty or just whitespace, discard it from the main buffer
                        self.buffer = self.buffer[len(chunk):].lstrip('\n')
                        if not self.buffer: break
                        continue

                    try:
                        await self.channel.send(f"```ansi\n{chunk}\n```")
                        # Success! Remove only the processed chunk from the main buffer
                        self.buffer = self.buffer[len(chunk):].lstrip('\n')
                        await asyncio.sleep(0.6)
                    except discord.HTTPException as e:
                        if e.status == 429:
                            await asyncio.sleep(5)
                            # Do not discard chunk on rate limit, try again
                            continue
                        else: break
                    except Exception: break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.client.log_event(self.user_id, self.username, f"Worker error: {e}")

    def stop(self):
        self.worker_task.cancel()

class MudCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="play", description="Start playing the MUD in DMs")
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.allowed_installs(guilds=True, users=False)
    async def play_slash(self, interaction: discord.Interaction):
        user = interaction.user
        try:
            dm_channel = user.dm_channel or await user.create_dm()
            await dm_channel.send("🎮 **Starting MUD Connection...**")
            await interaction.response.send_message("✅ Check your DMs! I've started the connection process.", ephemeral=True)
            await self.bot.init_session(user, dm_channel)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I couldn't send you a DM! Please enable 'Allow direct messages from server members' in your privacy settings.", ephemeral=True)

    @commands.hybrid_command(name="disconnect", description="Disconnect from the MUD")
    @commands.dm_only()
    @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def disconnect_cmd(self, ctx: commands.Context):
        user_id = ctx.author.id
        if user_id in self.bot.sessions:
            session = self.bot.sessions.pop(user_id, None)
            session.stop()
            session.writer.close()
            try: await session.writer.wait_closed()
            except: pass
            await ctx.send("🔌 *Disconnected.*")
        else:
            await ctx.send("❌ You are not currently connected.")

    @commands.hybrid_command(name="return", description="Send a newline character to the MUD")
    @commands.dm_only()
    @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def return_cmd(self, ctx: commands.Context):
        user_id = ctx.author.id
        if user_id in self.bot.sessions:
            session = self.bot.sessions[user_id]
            session.writer.write(b"\n")
            await session.writer.drain()
            await ctx.send("✅ *Newline sent.*", ephemeral=True)
        else:
            await ctx.send("❌ You are not currently connected.")

    @commands.hybrid_command(name="password", description="Enter your password securely")
    @commands.dm_only()
    @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.describe(password="The password to send")
    async def password_cmd(self, ctx: commands.Context, *, password: str):
        user_id = ctx.author.id
        if user_id in self.bot.sessions:
            session = self.bot.sessions[user_id]
            session.writer.write((password + "\n").encode('utf-8'))
            await session.writer.drain()
            if ctx.interaction:
                await ctx.send("🔑 *Password sent securely.*", ephemeral=True)
            else:
                await ctx.send("⚠️ *Password sent, but prefix commands in DMs are visible in your history. Please use the Slash Command version or delete your message.*")
        else:
            await ctx.send("❌ You are not currently connected.")

    @commands.command(name="help")
    async def help_prefix(self, ctx):
        help_text = (
            "**Mud Client Commands:**\n"
            "`_help`: Show this help message.\n"
            "`_disconnect`: End your current MUD session.\n"
            "`_return`: Send a newline character to the MUD.\n"
            "`_password <pass>`: Enter your password (Warning: visible in history! Use Slash Command `/password` instead)."
        )
        await ctx.send(help_text)

class DiscordMudClient(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(command_prefix='_', help_command=None, *args, **kwargs)
        self.sessions = {}  # {user_id: MudSession}
        self.connecting = set()
        self.is_shutting_down = False

    async def setup_hook(self):
        await self.add_cog(MudCommands(self))
        try:
            synced = await self.tree.sync()
            print(f"--- Synced {len(synced)} global slash commands ---")
        except Exception as e:
            print(f"--- Failed to sync slash commands: {e} ---")

    def log_event(self, user_id, username, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] [User: {username} ({user_id})] {message}")

    async def on_ready(self):
        await self.change_presence(activity=discord.Game(name="DM to Play"))
        # Final sync attempt in on_ready to ensure all commands are registered
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

        tasks = [self.notify_and_close(uid, s.writer, s.channel, s.username) for uid, s in self.sessions.items()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        await self.close()

    async def notify_and_close(self, user_id, writer, channel, username):
        try:
            self.log_event(user_id, username, "Closing session due to bot shutdown.")
            await channel.send("🛑 **Bot Shutdown:** Bridge closing. Your session has ended.")
            if user_id in self.sessions:
                self.sessions[user_id].stop()
            writer.close()
            await writer.wait_closed()
        except: pass

    def split_buffer(self, buf):
        limit = 1900
        if len(buf) <= limit:
            return buf, ""

        split_at = buf.rfind('\n', 0, limit)
        if split_at == -1 or split_at < 500:
            split_at = limit

        last_esc = buf.rfind('\x1b', 0, split_at)
        if last_esc != -1:
            terminated = False
            for j in range(last_esc + 1, min(split_at + 10, len(buf))):
                if 0x40 <= ord(buf[j]) <= 0x7E:
                    if j < split_at:
                        terminated = True
                    break
            if not terminated:
                split_at = last_esc

        return buf[:split_at], buf[split_at:].lstrip('\n')

    async def mud_listener(self, user_id, channel, username):
        if user_id not in self.sessions: return
        session = self.sessions[user_id]
        try:
            while True:
                data = await session.reader.read(8192)
                if not data:
                    self.log_event(user_id, username, "Connection closed by remote MUD host.")
                    break

                raw_text = session.parser.feed(data)
                if raw_text:
                    session.buffer = (session.buffer + raw_text)[-MAX_BUFFER_SIZE:]
                    await session.msg_queue.put(True)
        except (ConnectionResetError, BrokenPipeError):
            self.log_event(user_id, username, "Connection reset by peer.")
        except Exception as e:
            self.log_event(user_id, username, f"Listener Error: {str(e)}")
        finally:
            if not self.is_shutting_down:
                try:
                    if user_id in self.sessions:
                        await channel.send("⚠️ *Connection closed.*")
                except: pass
            session = self.sessions.pop(user_id, None)
            if session:
                session.stop()
            self.log_event(user_id, username, "Session cleaned up and removed.")

    async def send_telnet_cmd(self, writer, *args):
        try:
            writer.write(bytes(args))
            await writer.drain()
        except: pass

    async def send_naws(self, writer, width=80, height=1000):
        w_hi, w_lo = divmod(width, 256)
        h_hi, h_lo = divmod(height, 256)
        packet = bytes([Telnet.IAC, Telnet.SB, Telnet.NAWS, w_hi, w_lo, h_hi, h_lo, Telnet.IAC, Telnet.SE])
        try:
            writer.write(packet)
            await writer.drain()
        except: pass

    async def init_session(self, user, channel):
        user_id = user.id
        display_name = str(user)

        if user_id in self.sessions or user_id in self.connecting:
            return

        self.connecting.add(user_id)
        self.log_event(user_id, display_name, f"Attempting to connect to {MUD_HOST}:{MUD_PORT} (TLS: {MUD_TLS})...")
        await channel.send(f"⏳ *Connecting to {MUD_HOST}:{MUD_PORT}...*\n(Tip: Type `_help` for available commands)")

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

            session = MudSession(self, user_id, reader, writer, channel, display_name)
            self.sessions[user_id] = session
            is_encrypted = writer.get_extra_info('ssl_object') is not None
            self.log_event(user_id, display_name, f"Successfully connected to MUD (Encrypted: {is_encrypted}).")

            await self.send_telnet_cmd(session.writer, Telnet.IAC, Telnet.WILL, Telnet.TTYPE)
            await self.send_telnet_cmd(session.writer, Telnet.IAC, Telnet.WILL, Telnet.NAWS)
            await self.send_naws(session.writer)

            asyncio.create_task(self.mud_listener(user_id, channel, display_name))
            self.connecting.discard(user_id)
        except ConnectionRefusedError:
            self.connecting.discard(user_id)
            await channel.send("❌ Connection refused: The MUD server is likely down.")
        except (socket.timeout, asyncio.TimeoutError):
            self.connecting.discard(user_id)
            await channel.send("❌ Connection timed out.")
        except Exception as e:
            self.connecting.discard(user_id)
            self.log_event(user_id, display_name, f"Connection failed: {str(e)}")
            await channel.send(f"❌ Could not connect: {type(e).__name__}")

    async def on_message(self, message):
        if message.author.bot or self.is_shutting_down: return
        user_id = message.author.id
        display_name = str(message.author)

        ctx = await self.get_context(message)
        if ctx.valid:
            await self.invoke(ctx)
            return

        # If not a command, it must be in DMs to be MUD input
        if message.guild: return

        if len(message.content) > MAX_INPUT_LENGTH:
            await message.channel.send(f"❌ Input too long (Max {MAX_INPUT_LENGTH} characters).")
            return

        if user_id in self.sessions:
            session = self.sessions[user_id]
            if session.echo_off:
                # User is typing a password normally. Warn them.
                await message.channel.send("⚠️ **SECURITY WARNING:** You are typing a password while Echo is OFF. **Bots cannot delete user messages in DMs.** Please delete your previous message immediately and use `/password <your_password>` (Slash Command) instead.")
                # We still send it to the MUD though, as they probably want to log in.
                sanitized_input = message.content.replace('\xff', '')
                try:
                    session.writer.write((sanitized_input + "\n").encode('utf-8'))
                    await session.writer.drain()
                except Exception as e:
                    self.log_event(user_id, display_name, f"Write error (disconnecting): {str(e)}")
                    self.sessions.pop(user_id, None)
                return

            # Normal input
            sanitized_input = message.content.replace('\xff', '')
            try:
                session.writer.write((sanitized_input + "\n").encode('utf-8'))
                await session.writer.drain()
            except Exception as e:
                self.log_event(user_id, display_name, f"Write error (disconnecting): {str(e)}")
                self.sessions.pop(user_id, None)
            return

        # Start a new session if they DM us and don't have one
        await self.init_session(message.author, message.channel)

if __name__ == "__main__":
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    client = DiscordMudClient(intents=intents)
    client.run(TOKEN)
