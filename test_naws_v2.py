
import asyncio
from unittest.mock import MagicMock, AsyncMock
from src.protocol import TelnetProtocol, Telnet, NAWS_MIN, NAWS_MAX

async def test_send_naws():
    writer = MagicMock()
    writer.drain = AsyncMock()

    protocol = TelnetProtocol(None, writer, 123, "testuser")

    # Test default
    await protocol.send_naws()
    # Default width 80, height 24
    expected_data = bytes([Telnet.IAC, Telnet.SB, Telnet.NAWS, 0, 80, 0, 24, Telnet.IAC, Telnet.SE])
    writer.write.assert_called_with(expected_data)
    print("Default NAWS test passed!")

    writer.write.reset_mock()

    # Test custom
    await protocol.send_naws(1000, 500)
    expected_data_custom = bytes([Telnet.IAC, Telnet.SB, Telnet.NAWS, 3, 232, 1, 244, Telnet.IAC, Telnet.SE])
    writer.write.assert_called_with(expected_data_custom)
    print("Custom NAWS test passed!")

    # Test validation
    try:
        await protocol.send_naws(0, 10)
        print("FAIL: Should have raised ValueError for width=0")
    except ValueError as e:
        print(f"Validation test passed: {e}")

    try:
        await protocol.send_naws(10, 65536)
        print("FAIL: Should have raised ValueError for height=65536")
    except ValueError as e:
        print(f"Validation test passed: {e}")

if __name__ == "__main__":
    asyncio.run(test_send_naws())
