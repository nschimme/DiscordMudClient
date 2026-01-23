import asyncio
import codecs
from .config import MAX_BUFFER_SIZE, ANSI_TIMEOUT

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
            asyncio.create_task(self.client.send_telnet_cmd(self.user_id, self.username, self.writer, Telnet.IAC, (Telnet.DO if cmd == Telnet.WILL else Telnet.DONT), Telnet.ECHO))
            session = self.client.session_manager.get(self.user_id)
            if session:
                if cmd == Telnet.WILL:
                    if not session.echo_off:
                        session.echo_off = True
                        asyncio.create_task(session.channel.send("🔑 **Password Mode:** Please use `/password <your_password>` to enter your password securely."))
                else:
                    session.echo_off = False
        elif opt == Telnet.TTYPE and cmd == Telnet.DO:
            asyncio.create_task(self.client.send_telnet_cmd(self.user_id, self.username, self.writer, Telnet.IAC, Telnet.WILL, Telnet.TTYPE))
        elif opt == Telnet.NAWS and cmd == Telnet.DO:
            asyncio.create_task(self.client.send_naws(self.user_id, self.username, self.writer))
        elif opt == Telnet.CHARSET and cmd == Telnet.DO:
            asyncio.create_task(self.client.send_telnet_cmd(self.user_id, self.username, self.writer, Telnet.IAC, Telnet.WILL, Telnet.CHARSET))
        elif opt == Telnet.CHARSET and cmd == Telnet.WILL:
            asyncio.create_task(self.client.send_telnet_cmd(self.user_id, self.username, self.writer, Telnet.IAC, Telnet.DO, Telnet.CHARSET))

    def handle_subnegotiation(self, opt, data):
        if opt == Telnet.TTYPE and len(data) > 0 and data[0] == Telnet.SEND:
            identity = f"DiscordMudClient (UID:{self.user_id})"
            packet = bytes([Telnet.IAC, Telnet.SB, Telnet.TTYPE, Telnet.IS]) + \
                     identity.encode('ascii', errors='ignore') + \
                     bytes([Telnet.IAC, Telnet.SE])
            asyncio.create_task(self.safe_send(packet))
        elif opt == Telnet.CHARSET and len(data) > 0 and data[0] == Telnet.REQUEST:
            try:
                charsets = data[1:].decode('ascii', errors='ignore')
                if 'UTF-8' in charsets.upper():
                    packet = bytes([Telnet.IAC, Telnet.SB, Telnet.CHARSET, Telnet.ACCEPTED]) + \
                             "UTF-8".encode('ascii') + \
                             bytes([Telnet.IAC, Telnet.SE])
                    asyncio.create_task(self.safe_send(packet))
                else:
                    packet = bytes([Telnet.IAC, Telnet.SB, Telnet.CHARSET, Telnet.REJECTED]) + \
                             bytes([Telnet.IAC, Telnet.SE])
                    asyncio.create_task(self.safe_send(packet))
            except: pass

    async def safe_send(self, data):
        try:
            self.writer.write(data)
            await asyncio.wait_for(self.writer.drain(), timeout=ANSI_TIMEOUT)
        except Exception as e:
            self.client.log_event(self.user_id, self.username, f"Subnegotiation drain failed: {e}")
            await self.client.close_session(self.user_id)
