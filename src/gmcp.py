import asyncio
import json
from .config import APP_VERSION

class GmcpHandler:
    """
    Handles GMCP protocol logic, including handshake and core modules.
    Extensible for future module additions.
    """
    def __init__(self, protocol):
        self.protocol = protocol
        self.enabled = False
        self.last_ping_sent_time = None
        self.last_rtt = None

        # Dispatch table for GMCP packages
        self.handlers = {
            "core.ping": self._handle_core_ping
        }

    async def enable(self):
        self.enabled = True
        from .protocol import Telnet
        await self.protocol.send_command(Telnet.DO, Telnet.GMCP)

        # Core.Hello must be the first message
        await self.send("Core.Hello", {
            "client": "DiscordMudClient",
            "version": APP_VERSION
        })
        # Advertise supported modules
        await self.send("Core.Supports.Set", ["Core 1"])

    async def send(self, package, data=None):
        if not self.enabled:
            return

        if package.lower() == "core.ping":
            self.last_ping_sent_time = asyncio.get_event_loop().time()

        payload = package
        if data is not None:
            payload += " " + json.dumps(data)

        from .protocol import Telnet
        await self.protocol.send_subnegotiation(Telnet.GMCP, payload.encode('utf-8'))

    def handle(self, data: bytes):
        """Parses and dispatches incoming GMCP messages."""
        try:
            msg = data.decode('utf-8', errors='ignore').strip()
            if not msg:
                return

            parts = msg.split(' ', 1)
            package_cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else None

            handler = self.handlers.get(package_cmd)
            if handler:
                handler(arg)
        except Exception as e:
            # Silently ignore malformed GMCP
            pass

    def _handle_core_ping(self, arg):
        if self.last_ping_sent_time is not None:
            now = asyncio.get_event_loop().time()
            self.last_rtt = int((now - self.last_ping_sent_time) * 1000)
            self.last_ping_sent_time = None
