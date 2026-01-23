import asyncio
import socket
import ssl
import websockets

class WebSocketStreamAdapter:
    def __init__(self, websocket):
        self.ws = websocket
        self._read_buffer = bytearray()
        self._write_buffer = bytearray()

    async def read(self, n):
        if not self._read_buffer:
            try:
                data = await self.ws.recv()
                if isinstance(data, str):
                    self._read_buffer.extend(data.encode('utf-8'))
                else:
                    self._read_buffer.extend(data)
            except websockets.exceptions.ConnectionClosed:
                return b''

        chunk = self._read_buffer[:n]
        self._read_buffer = self._read_buffer[n:]
        return bytes(chunk)

    def write(self, data):
        self._write_buffer.extend(data)

    async def drain(self):
        if self._write_buffer:
            await self.ws.send(bytes(self._write_buffer))
            self._write_buffer.clear()

    def close(self):
        asyncio.create_task(self.ws.close())

    async def wait_closed(self):
        try:
            await self.ws.wait_closed()
        except: pass

    def get_extra_info(self, info):
        if info == 'ssl_object':
            return getattr(self.ws, 'secure', False)
        return None

async def connect_mud(protocol, host, port, path='/', on_warning=None):
    """
    Establishes a connection to the MUD based on the protocol.
    Returns (reader, writer).
    on_warning is an optional async callback for TLS fallback warnings.
    """
    reader, writer = None, None

    if protocol in ('telnet', 'telnets'):
        use_ssl = (protocol == 'telnets')
        if use_ssl:
            ssl_context = ssl.create_default_context()
            try:
                reader, writer = await asyncio.open_connection(host, port, ssl=ssl_context)
            except (ssl.SSLError, ConnectionRefusedError) as e:
                if isinstance(e, ssl.SSLError) and on_warning:
                    await on_warning(f"TLS Verification failed: {e}. Retrying leniently...")
                    ssl_context = ssl.create_default_context()
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE
                    reader, writer = await asyncio.open_connection(host, port, ssl=ssl_context)
                else:
                    raise e
        else:
            reader, writer = await asyncio.open_connection(host, port)

        sock = writer.get_extra_info('socket')
        if sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

    elif protocol in ('ws', 'wss'):
        use_ssl = (protocol == 'wss')
        ws_url = f"{protocol}://{host}:{port}{path}"

        ssl_context = None
        if use_ssl:
            ssl_context = ssl.create_default_context()

        try:
            ws = await websockets.connect(ws_url, ssl=ssl_context, ping_timeout=None)
        except (ssl.SSLError, Exception) as e:
            if isinstance(e, ssl.SSLError) and on_warning:
                await on_warning(f"WSS Verification failed: {e}. Retrying leniently...")
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
                ws = await websockets.connect(ws_url, ssl=ssl_context, ping_timeout=None)
            else:
                raise e

        adapter = WebSocketStreamAdapter(ws)
        reader, writer = adapter, adapter

    else:
        raise ValueError(f"Unknown protocol: {protocol}")

    return reader, writer
