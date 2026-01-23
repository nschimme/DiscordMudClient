import asyncio
import discord
from .config import MAX_BUFFER_SIZE, SESSION_CLOSE_TIMEOUT
from .protocol import TelnetProtocol

class MudSession:
    def __init__(self, manager, user_id, reader, writer, channel, username):
        self.manager = manager
        self.client = manager.client
        self.user_id = user_id
        self.reader = reader
        self.writer = writer
        self.channel = channel
        self.username = username
        self.protocol = TelnetProtocol(self.client, writer, user_id, username, session=self)
        self.echo_off = False
        self.bell_pending = False
        self.buffer = ""
        self.msg_queue = asyncio.Queue()
        self.activity_event = asyncio.Event()
        self.worker_task = asyncio.create_task(self.worker())
        self.heartbeat_task = asyncio.create_task(self.gmcp_heartbeat())
        self.listener_task = None

    def notify_activity(self):
        self.activity_event.set()
        self.activity_event.clear()

    async def gmcp_heartbeat(self):
        try:
            while True:
                try:
                    await asyncio.wait_for(self.activity_event.wait(), timeout=45.0)
                except asyncio.TimeoutError:
                    if self.protocol and self.protocol.gmcp.enabled:
                        await self.protocol.gmcp.send("Core.Ping", self.protocol.gmcp.last_rtt)
                except Exception as e:
                    self.client.log_event(self.user_id, self.username, f"Heartbeat error: {e}")
                    await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass

    async def worker(self):
        try:
            while True:
                await self.msg_queue.get()
                await asyncio.sleep(0.03)
                while not self.msg_queue.empty():
                    self.msg_queue.get_nowait()

                while (self.buffer or self.bell_pending) and not self.client.is_shutting_down:
                    current_snapshot = self.buffer
                    chunk, remainder = self.manager.split_buffer(current_snapshot)

                    if not chunk.strip() and not self.bell_pending:
                        self.buffer = self.buffer[len(chunk):].lstrip('\n')
                        if not self.buffer: break
                        continue

                    try:
                        mention = f" <@{self.user_id}> 🔔" if self.bell_pending else ""
                        await self.channel.send(f"```ansi\n{chunk}\n```{mention}")
                        self.bell_pending = False
                        self.buffer = self.buffer[len(chunk):].lstrip('\n')
                        await asyncio.sleep(0.6)
                    except discord.HTTPException as e:
                        if e.status == 429:
                            await asyncio.sleep(5)
                            continue
                        else: break
                    except Exception: break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.client.log_event(self.user_id, self.username, f"Worker error: {e}")

    def stop(self):
        if self.worker_task:
            self.worker_task.cancel()
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
        if self.listener_task and self.listener_task != asyncio.current_task():
            self.listener_task.cancel()

class SessionManager:
    def __init__(self, client):
        self.client = client
        self.sessions = {}  # {user_id: MudSession}
        self.connecting = set()

    def get(self, user_id):
        return self.sessions.get(user_id)

    def is_connecting(self, user_id):
        return user_id in self.connecting

    def start_connecting(self, user_id):
        self.connecting.add(user_id)

    def stop_connecting(self, user_id):
        self.connecting.discard(user_id)

    async def close_session(self, user_id):
        """Centralized session cleanup logic."""
        session = self.sessions.pop(user_id, None)
        if not session:
            return

        self.client.log_event(user_id, session.username, "Initiating session cleanup.")

        try:
            session.stop()
        except Exception as e:
            self.client.log_event(user_id, session.username, f"Error stopping worker: {e}")

        try:
            if session.writer:
                session.writer.close()
                try:
                    await asyncio.wait_for(session.writer.wait_closed(), timeout=SESSION_CLOSE_TIMEOUT)
                except asyncio.TimeoutError:
                    self.client.log_event(user_id, session.username, "SSL/Socket shutdown timed out. Force closing.")
                except Exception as e:
                    self.client.log_event(user_id, session.username, f"Error during wait_closed: {e}")
        except Exception as e:
            self.client.log_event(user_id, session.username, f"Error during writer.close: {e}")

        self.client.log_event(user_id, session.username, "Session cleanup complete.")

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
