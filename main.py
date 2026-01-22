import discord
import asyncio
import os
import socket
import signal
from datetime import datetime

# --- CONFIGURATION ---
TOKEN = os.getenv('DISCORD_TOKEN')
MUD_HOST = os.getenv('MUD_HOST')
MUD_PORT = os.getenv('MUD_PORT')
MAX_BUFFER_SIZE = 50000  # Prevent memory exhaustion
MAX_INPUT_LENGTH = 500   # Prevent MUD buffer flooding

class Telnet:
    IAC, DONT, DO, WONT, WILL = 255, 254, 253, 252, 251
    SB, SE = 250, 240
    ECHO, TTYPE, NAWS = 1, 24, 31
    IS, SEND = 0, 1

class DiscordMudClient(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sessions = {}  # {user_id: (reader, writer, channel, username)}
        self.buffers = {}
        self.connecting = set()
        self.echo_off = set()
        self.is_shutting_down = False

    def log_event(self, user_id, username, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] [User: {username} ({user_id})] {message}")

    async def on_ready(self):
        await self.change_presence(activity=discord.Game(name="DM to Play"))
        print(f'--- DiscordMudClient Online as {self.user} ---')

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

    async def shutdown(self):
        if self.is_shutting_down: return
        self.is_shutting_down = True
        self.log_event("SYSTEM", "CORE", "Shutdown signal received. Closing all sessions...")

        tasks = [self.notify_and_close(uid, s[1], s[2], s[3]) for uid, s in self.sessions.items()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        await self.close()

    async def notify_and_close(self, user_id, writer, channel, username):
        try:
            self.log_event(user_id, username, "Closing session due to bot shutdown.")
            await channel.send("🛑 **Bot Shutdown:** Bridge closing. Your session has ended.")
            writer.close()
            await writer.wait_closed()
        except: pass

    async def flush_buffer(self, user_id, username, channel):
        buf = self.buffers.get(user_id, "")
        if not buf.strip(): return

        while len(buf) > 0:
            if self.is_shutting_down: break

            limit = 1900
            split_at = buf.rfind('\n', 0, limit)
            if split_at == -1 or split_at < 500:
                split_at = limit if len(buf) > limit else len(buf)

            chunk = buf[:split_at]
            if chunk.strip():
                try:
                    await channel.send(f"```ansi\n{chunk}\n```")
                    await asyncio.sleep(0.6)
                except discord.HTTPException:
                    await asyncio.sleep(2) 
                    break

            buf = buf[split_at:].lstrip('\n')

        self.buffers[user_id] = ""

    async def mud_listener(self, user_id, channel, username):
        if user_id not in self.sessions: return
        reader, writer, _, _ = self.sessions[user_id]
        try:
            while True:
                data = await reader.read(8192)
                if not data:
                    self.log_event(user_id, username, "Connection closed by remote MUD host.")
                    break

                raw_text = self.parse_stream(user_id, data, writer, username)
                current_buf = self.buffers.get(user_id, "")
                self.buffers[user_id] = (current_buf + raw_text)[-MAX_BUFFER_SIZE:]

                await asyncio.sleep(0.05)
                await self.flush_buffer(user_id, username, channel)
        except Exception as e:
            self.log_event(user_id, username, f"Listener Error: {str(e)}")
        finally:
            if not self.is_shutting_down:
                try: await channel.send("⚠️ *Connection closed.*")
                except: pass
            self.sessions.pop(user_id, None)
            self.log_event(user_id, username, "Session cleaned up and removed.")

    def parse_stream(self, user_id, data, writer, username):
        output = bytearray()
        i = 0
        while i < len(data):
            byte = data[i]
            if byte == Telnet.IAC:
                try:
                    cmd = data[i+1]
                    if cmd in (Telnet.DO, Telnet.DONT, Telnet.WILL, Telnet.WONT):
                        opt = data[i+2]
                        if opt == Telnet.ECHO:
                            asyncio.create_task(self.send_telnet_cmd(writer, Telnet.IAC, (Telnet.DO if cmd == Telnet.WILL else Telnet.DONT), Telnet.ECHO))
                            if cmd == Telnet.WILL: self.echo_off.add(user_id)
                            else: self.echo_off.discard(user_id)
                        elif opt == Telnet.TTYPE and cmd == Telnet.DO:
                            asyncio.create_task(self.send_telnet_cmd(writer, Telnet.IAC, Telnet.WILL, Telnet.TTYPE))
                        elif opt == Telnet.NAWS and cmd == Telnet.DO:
                            asyncio.create_task(self.send_naws(writer))
                        i += 3
                    elif cmd == Telnet.SB:
                        end = data.find(bytes([Telnet.IAC, Telnet.SE]), i)
                        if end != -1:
                            sb_data = data[i+2:end]
                            if sb_data[0] == Telnet.TTYPE and sb_data[1] == Telnet.SEND:
                                identity = f"DiscordMudClient (UID:{user_id})"
                                packet = bytes([Telnet.IAC, Telnet.SB, Telnet.TTYPE, Telnet.IS]) + \
                                         identity.encode('ascii', errors='ignore') + \
                                         bytes([Telnet.IAC, Telnet.SE])
                                writer.write(packet)
                                asyncio.create_task(writer.drain())
                            i = end + 2
                        else: i = len(data)
                    else: i += 2
                except: i += 1
            else:
                output.append(byte)
                i += 1
        return output.decode('utf-8', errors='ignore')

    async def on_message(self, message):
        if message.author.bot or message.guild or self.is_shutting_down: return
        user_id = message.author.id
        display_name = str(message.author)

        if len(message.content) > MAX_INPUT_LENGTH:
            await message.channel.send(f"❌ Input too long (Max {MAX_INPUT_LENGTH} characters).")
            return

        if user_id not in self.sessions:
            if user_id in self.connecting: return
            self.connecting.add(user_id)
            self.log_event(user_id, display_name, f"Attempting to connect to {MUD_HOST}:{MUD_PORT}...")

            try:
                reader, writer = await asyncio.open_connection(MUD_HOST, MUD_PORT)
                sock = writer.get_extra_info('socket')
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                
                self.sessions[user_id] = (reader, writer, message.channel, display_name)
                self.log_event(user_id, display_name, "Successfully connected to MUD.")

                await self.send_telnet_cmd(writer, Telnet.IAC, Telnet.WILL, Telnet.TTYPE)
                await self.send_telnet_cmd(writer, Telnet.IAC, Telnet.WILL, Telnet.NAWS)
                await self.send_naws(writer)

                asyncio.create_task(self.mud_listener(user_id, message.channel, display_name))
                self.connecting.discard(user_id)
            except Exception as e:
                self.connecting.discard(user_id)
                self.log_event(user_id, display_name, f"Connection failed: {str(e)}")
                await message.channel.send("❌ Could not connect to MUD.")
            return

        sanitized_input = message.content.replace('\xff', '')
        _, writer, _, _ = self.sessions[user_id]
        try:
            writer.write((sanitized_input + "\n").encode('utf-8'))
            await writer.drain()
            if user_id in self.echo_off:
                try: await message.delete()
                except: pass
        except Exception as e:
            self.log_event(user_id, display_name, f"Write error (disconnecting): {str(e)}")
            self.sessions.pop(user_id, None)

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

if __name__ == "__main__":
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    client = DiscordMudClient(intents=intents)
    client.run(TOKEN)

