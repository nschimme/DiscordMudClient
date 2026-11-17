import json
import re
import asyncio
import logging
from .editor import display_file_view, display_file_edit_prompt
logger = logging.getLogger(__name__)

ANSI_SGR_RE = re.compile(r'\x1b\[[0-9;:]*m')
EMOJI_RANGES = (
    (0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2300, 0x23FF), (0x2B00, 0x2BFF),
    (0xFE00, 0xFE0F), (0x200D, 0x200D), (0x20E3, 0x20E3),
)


def parse_allowed_ranges(spec):
    """
    Parses MUME's `allowed-ranges` into (numeric_ranges, flags), or None if absent/unparseable
    (in which case the server remains the authority).
    Accepts ints, [lo, hi] pairs and the strings "ansi-sgr" / "emoji".
    """
    if not isinstance(spec, list):
        return None
    ranges, flags = [], set()
    for item in spec:
        if isinstance(item, bool):
            return None
        if isinstance(item, int):
            ranges.append((item, item))
        elif isinstance(item, (list, tuple)) and len(item) == 2 and all(isinstance(x, int) for x in item):
            ranges.append((item[0], item[1]))
        elif isinstance(item, str) and item in ("ansi-sgr", "emoji"):
            flags.add(item)
        else:
            return None
    return ranges, flags


def check_allowed_characters(text, allowed):
    """Raises ValueError naming the first disallowed character (with line:col)."""
    ranges, flags = allowed
    if "ansi-sgr" in flags:
        scan = ANSI_SGR_RE.sub(lambda m: "\0" * len(m.group(0)), text)  # placeholders, same length
    else:
        scan = text
    line, col = 1, 0
    for ch, orig in zip(scan, text):
        col += 1
        if orig == "\n":
            line, col = line + 1, 0
            continue
        cp = ord(orig)
        if ch == "\0" and orig != "\0":
            continue  # part of an allowed ANSI SGR sequence
        if orig == "\t" or any(lo <= cp <= hi for lo, hi in ranges):
            continue
        if "emoji" in flags and any(lo <= cp <= hi for lo, hi in EMOJI_RANGES):
            continue
        raise ValueError(
            f"Character {orig!r} (U+{cp:04X}) at line {line}, column {col} is not allowed in this text by the server."
        )

class MumeClientHandler:
    """
    Handles MUME-specific GMCP client messaging.
    Translates MUME messages to/from the generic EditorManager.
    """
    def __init__(self, gmcp_handler):
        self.gmcp = gmcp_handler
        self.protocol = gmcp_handler.protocol

    def get_editor_manager(self):
        if self.protocol.session:
            return getattr(self.protocol.session, 'editor_manager', None)
        return None

    def handle(self, package_cmd: str, arg: str):
        try:
            data = json.loads(arg) if arg else {}
        except json.JSONDecodeError as e:
            logger.warning("Failed to decode GMCP payload for package %r. Raw arg: %r. Error: %s", package_cmd, arg, e)
            data = {}

        if package_cmd == "mume.client.view":
            self.handle_view(data)
        elif package_cmd == "mume.client.edit":
            self.handle_edit(data)
        elif package_cmd == "mume.client.write":
            self.handle_write_response(data)
        elif package_cmd == "mume.client.canceledit":
            self.handle_cancel_response(data)
        elif package_cmd == "mume.client.error":
            self.handle_error(data)
        elif package_cmd.startswith("mume.client."):
            logger.debug("Unhandled MUME.Client package: %s payload=%r", package_cmd, data)

    def _create_task(self, coro):
        try:
            loop = asyncio.get_running_loop()
            return loop.create_task(coro)
        except RuntimeError:
            # Fallback for synchronous/test environments without a running loop.
            # Log instead of silently dropping the coroutine so misconfigurations are visible.
            logger.warning(
                "No running asyncio event loop; dropping coroutine %r in %s._create_task",
                coro,
                type(self).__name__,
            )
            return None

    def handle_view(self, data):
        title = data.get("title", "Untitled")
        text = data.get("text", "")
        if self.protocol.session:
            self._create_task(display_file_view(self.protocol.session.channel, title, text))

    def handle_edit(self, data):
        session_id = data.get("id")
        if session_id is None:
            return

        title = data.get("title", f"edit_{session_id}")
        text = data.get("text", "")
        max_size = data.get("max-size")
        allowed = parse_allowed_ranges(data.get("allowed-ranges"))

        manager = self.get_editor_manager()
        if not manager:
            return

        # Define callbacks
        async def on_save(updated_text):
            payload = {
                "id": session_id,
                "text": updated_text
            }
            await self.gmcp.send("MUME.Client.Write", payload)

        async def on_cancel():
            payload = {
                "id": session_id
            }
            await self.gmcp.send("MUME.Client.CancelEdit", payload)

        def mume_validator(session, updated_text):
            # Size (UTF-8 bytes) is enforced by EditSession.validate
            if '\x00' in updated_text:
                raise ValueError("Text cannot contain NUL bytes.")
            if allowed is not None:
                check_allowed_characters(updated_text, allowed)

        # Register edit session
        edit_session = manager.register_session(
            session_id=session_id,
            title=title,
            text=text,
            max_size=max_size,
            on_save=on_save,
            on_cancel=on_cancel,
            validator=mume_validator
        )

        self._create_task(display_file_edit_prompt(self.protocol.session.channel, edit_session, manager))

    def handle_write_response(self, data):
        session_id = data.get("id")
        result = data.get("result")

        manager = self.get_editor_manager()
        if not manager:
            return

        if session_id is not None:
            self._create_task(manager.resolve(session_id, result is True, detail=result))

    def handle_cancel_response(self, data):
        session_id = data.get("id")
        result = data.get("result")

        manager = self.get_editor_manager()
        if not manager:
            return

        if session_id is not None:
            if manager.get_session(session_id) and manager.get_session(session_id).pending is None:
                manager.get_session(session_id).pending = "cancel"  # server-initiated cancel
            self._create_task(manager.resolve(session_id, result is True, detail=result))

    def handle_error(self, data):
        message = data.get("message", "Unknown error")
        if self.protocol.session:
            self._create_task(self.protocol.session.channel.send(
                f"⚠️ **GMCP MUME.Client Error:** {message}"
            ))
