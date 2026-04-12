import asyncio
import discord
from .config import MAX_BUFFER_SIZE, SESSION_CLOSE_TIMEOUT
from .protocol import TelnetProtocol
from .utils import extract_urls

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

                    # Reserve space for mention and a few potential links
                    mention = f" <@{self.user_id}> 🔔" if self.bell_pending else ""
                    reserved_for_links = 400 # Reserve some space for links
                    extra_len = len(mention) + reserved_for_links

                    chunk, remainder = self.manager.split_buffer(current_snapshot, extra_len=extra_len)

                    if not chunk.strip() and not self.bell_pending:
                        self.buffer = self.buffer[len(chunk):].lstrip('\n')
                        if not self.buffer: break
                        continue

                    # Extract URLs actually present in this chunk
                    chunk_urls = extract_urls(chunk)
                    final_links_text = ""
                    overflow_links = []
                    if chunk_urls:
                        # Add links one by one as long as we stay under the limit
                        for url in chunk_urls:
                            new_link = f"\n🔗 {url}"
                            if len(f"```ansi\n{chunk}\n```{mention}{final_links_text}{new_link}") < 1995:
                                final_links_text += new_link
                            else:
                                overflow_links.append(url)

                    try:
                        await self.channel.send(f"```ansi\n{chunk}\n```{mention}{final_links_text}")

                        # Handle overflow links in follow-up messages
                        while overflow_links:
                            current_followup = ""
                            while overflow_links:
                                next_url = overflow_links[0]
                                next_line = f"🔗 {next_url}\n"
                                if len(current_followup) + len(next_line) < 1990:
                                    current_followup += next_line
                                    overflow_links.pop(0)
                                else:
                                    # If even a single link is too long, we have to send it anyway
                                    # or it's just one giant link
                                    if not current_followup:
                                        current_followup = next_line[:1990] # Truncate extremely long links
                                        overflow_links.pop(0)
                                    break

                            if current_followup:
                                await self.channel.send(current_followup.strip())
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

    def split_buffer(self, buf, extra_len=0):
        # Ensure limit is at least a reasonable minimum (e.g., 500) to avoid infinite loops
        # and stay within Discord's 2000 character limit.
        limit = max(500, 1900 - extra_len)
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
