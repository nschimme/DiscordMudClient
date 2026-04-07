import asyncio
from src.protocol import TelnetProtocol
from src.ansi_transformer import DISCORD_FG

class MockWriter:
    def write(self, data): pass
    async def drain(self): pass

class MockClient:
    def log_event(self, *args): pass
    async def close_session(self, *args): pass

async def test_integration():
    protocol = TelnetProtocol(MockClient(), MockWriter(), 123, "testuser")

    # Test text with 24-bit ANSI
    # Red is 31 in Discord palette
    data = b"Hello \x1b[38;2;255;0;0mRed\x1b[0m World"
    result = protocol.feed(data)

    print(f"Result: {repr(result)}")
    if "\x1b[31mRed" in result:
        print("Integration Test: PASS")
    else:
        print("Integration Test: FAIL")

if __name__ == "__main__":
    asyncio.run(test_integration())
