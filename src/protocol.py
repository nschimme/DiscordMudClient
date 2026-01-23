import asyncio
import codecs
import zlib
from .config import MAX_BUFFER_SIZE, ANSI_TIMEOUT
from .gmcp import GmcpHandler

class DecompressionError(Exception):
    """Raised when MCCP decompression fails."""
    pass

class Telnet:
    IAC, DONT, DO, WONT, WILL = 255, 254, 253, 252, 251
    SB, SE = 250, 240
    ECHO, TTYPE, NAWS, CHARSET, COMPRESS2 = 1, 24, 31, 42, 86
    GMCP = 201
    IS, SEND, REQUEST, ACCEPTED, REJECTED = 0, 1, 1, 2, 3
    NOP = 241

class AnsiLayer:
    """Handles ANSI escape sequences and UTF-8 decoding."""
    def __init__(self):
        self.state = "TEXT"
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='ignore')
        self.current_ansi = bytearray()
        self.output = ""

    def feed_byte(self, byte):
        if self.state == "TEXT":
            if byte == 27: # ESC
                self.state = "ESC"
                self.current_ansi = bytearray([27])
            else:
                self.output += self.decoder.decode(bytes([byte]))
        elif self.state == "ESC":
            self.current_ansi.append(byte)
            if byte == ord('['):
                self.state = "CSI"
            else:
                # Not a CSI, just output what we have and reset
                self.output += self.decoder.decode(self.current_ansi)
                self.state = "TEXT"
        elif self.state == "CSI":
            self.current_ansi.append(byte)
            # Standard ANSI CSI sequence ends with a byte in 0x40-0x7E range
            if 0x40 <= byte <= 0x7E:
                self.output += self.decoder.decode(self.current_ansi)
                self.state = "TEXT"
            elif len(self.current_ansi) > 32: # Safety limit
                self.output += self.decoder.decode(self.current_ansi)
                self.state = "TEXT"

    def get_output(self):
        out = self.output
        self.output = ""
        return out

class TelnetProtocol:
    """Handles Telnet protocol state machine and outgoing packet construction."""
    def __init__(self, client, writer, user_id, username, session=None):
        self.client = client
        self.writer = writer
        self.user_id = user_id
        self.username = username
        self.session = session
        self.state = "DATA"
        self.sb_option = None
        self.sb_data = bytearray()
        self.iac_cmd = None
        self.ansi = AnsiLayer()
        self.compressing = False
        self.decompressor = None
        self.gmcp = GmcpHandler(self)

    def feed(self, data: bytes):
        if data and self.session:
            self.session.notify_activity()
        self._feed_internal(data)
        return self.ansi.get_output()

    def _feed_internal(self, data: bytes):
        if self.compressing:
            try:
                decompressed = self.decompressor.decompress(data)
                for byte in decompressed:
                    self._feed_byte(byte)
                if self.decompressor.eof:
                    remainder = self.decompressor.unused_data
                    self.compressing = False
                    self.decompressor = None
                    if remainder:
                        self._feed_internal(remainder)
            except zlib.error as e:
                raise DecompressionError(str(e))
        else:
            for i, byte in enumerate(data):
                self._feed_byte(byte)
                if self.compressing:
                    # Compression started during processing of this buffer
                    remaining = data[i+1:]
                    if remaining:
                        self._feed_internal(remaining)
                    break

    def _feed_byte(self, byte):
        if self.state == "DATA":
            if byte == Telnet.IAC:
                self.state = "IAC"
            else:
                self.ansi.feed_byte(byte)
        elif self.state == "IAC":
            if byte == Telnet.IAC:
                self.ansi.feed_byte(Telnet.IAC)
                self.state = "DATA"
            elif byte == Telnet.SB:
                self.state = "SB"
                self.sb_data = bytearray()
            elif byte in (Telnet.WILL, Telnet.WONT, Telnet.DO, Telnet.DONT):
                self.iac_cmd = byte
                self.state = "IAC_COMMAND"
            else:
                self.handle_single_byte_command(byte)
                self.state = "DATA"
        elif self.state == "IAC_COMMAND":
            self.handle_command(self.iac_cmd, byte)
            self.state = "DATA"
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
                self.state = "DATA"
            elif byte == Telnet.IAC:
                self.sb_data.append(Telnet.IAC)
                self.state = "SB_DATA"
            else:
                # Invalid sequence, ignore and return to DATA
                self.state = "DATA"

    def handle_single_byte_command(self, cmd):
        # We don't currently have special logic for NOP, etc.
        pass

    def handle_command(self, cmd, opt):
        if opt == Telnet.GMCP and cmd == Telnet.WILL:
            asyncio.create_task(self.gmcp.enable())
        elif opt == Telnet.ECHO:
            asyncio.create_task(self.send_command((Telnet.DO if cmd == Telnet.WILL else Telnet.DONT), Telnet.ECHO))
            session = self.client.session_manager.get(self.user_id)
            if session:
                if cmd == Telnet.WILL:
                    if not session.echo_off:
                        session.echo_off = True
                        asyncio.create_task(session.channel.send("🔑 **Password Mode:** Please use `/password <your_password>` to enter your password securely."))
                else:
                    session.echo_off = False
        elif opt == Telnet.TTYPE and cmd == Telnet.DO:
            asyncio.create_task(self.send_command(Telnet.WILL, Telnet.TTYPE))
        elif opt == Telnet.NAWS and cmd == Telnet.DO:
            asyncio.create_task(self.send_naws())
        elif opt == Telnet.CHARSET and cmd == Telnet.DO:
            asyncio.create_task(self.send_command(Telnet.WILL, Telnet.CHARSET))
        elif opt == Telnet.CHARSET and cmd == Telnet.WILL:
            asyncio.create_task(self.send_command(Telnet.DO, Telnet.CHARSET))
        elif opt == Telnet.COMPRESS2 and cmd == Telnet.WILL:
            asyncio.create_task(self.send_command(Telnet.DO, Telnet.COMPRESS2))

    def handle_subnegotiation(self, opt, data):
        if opt == Telnet.GMCP:
            self.gmcp.handle(data)
        elif opt == Telnet.COMPRESS2:
            self.compressing = True
            self.decompressor = zlib.decompressobj()
        elif opt == Telnet.TTYPE and len(data) > 0 and data[0] == Telnet.SEND:
            identity = f"DiscordMudClient (UID:{self.user_id})"
            packet = bytes([Telnet.IS]) + identity.encode('ascii', errors='ignore')
            asyncio.create_task(self.send_subnegotiation(Telnet.TTYPE, packet))
        elif opt == Telnet.CHARSET and len(data) > 0 and data[0] == Telnet.REQUEST:
            try:
                charsets = data[1:].decode('ascii', errors='ignore')
                if 'UTF-8' in charsets.upper():
                    packet = bytes([Telnet.ACCEPTED]) + "UTF-8".encode('ascii')
                    asyncio.create_task(self.send_subnegotiation(Telnet.CHARSET, packet))
                else:
                    packet = bytes([Telnet.REJECTED])
                    asyncio.create_task(self.send_subnegotiation(Telnet.CHARSET, packet))
            except: pass

    def escape_iac(self, data: bytes) -> bytes:
        return data.replace(b'\xff', b'\xff\xff')

    async def safe_send(self, data):
        if data and self.session:
            self.session.notify_activity()
        try:
            self.writer.write(data)
            await asyncio.wait_for(self.writer.drain(), timeout=ANSI_TIMEOUT)
        except Exception as e:
            self.client.log_event(self.user_id, self.username, f"Telnet write failed: {e}")
            await self.client.close_session(self.user_id)

    async def send_command(self, cmd, opt=None):
        if opt is not None:
            packet = bytes([Telnet.IAC, cmd, opt])
        else:
            packet = bytes([Telnet.IAC, cmd])
        await self.safe_send(packet)

    async def send_subnegotiation(self, opt, data: bytes):
        packet = bytes([Telnet.IAC, Telnet.SB, opt]) + \
                 self.escape_iac(data) + \
                 bytes([Telnet.IAC, Telnet.SE])
        await self.safe_send(packet)

    async def send_text(self, text: str):
        data = text.encode('utf-8')
        packet = self.escape_iac(data)
        await self.safe_send(packet)

    async def send_naws(self, width=80, height=1000):
        w_hi, w_lo = divmod(width, 256)
        h_hi, h_lo = divmod(height, 256)
        data = bytes([w_hi, w_lo, h_hi, h_lo])
        await self.send_subnegotiation(Telnet.NAWS, data)
