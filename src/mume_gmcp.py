import json
import asyncio
import logging
from .editor import display_file_view, display_file_edit_prompt
from .config import MUME_CHARACTER_ENCODING

logger = logging.getLogger(__name__)

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
        except json.JSONDecodeError:
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

        manager = self.get_editor_manager()
        if not manager:
            return

        # Define callbacks
        async def on_save(updated_text):
            # First, preserve the draft on the edit session so the user doesn't lose data
            sess = manager.get_session(session_id)
            if sess:
                sess.text = updated_text

            # MUME expects MUME_CHARACTER_ENCODING for text (except NUL) and fits in max-size.
            try:
                encoded = updated_text.encode(MUME_CHARACTER_ENCODING)
            except UnicodeEncodeError:
                raise ValueError(f"Text contains characters that cannot be represented in {MUME_CHARACTER_ENCODING.upper()} (Western European) encoding required by MUME.")

            if b'\x00' in encoded:
                raise ValueError("Text cannot contain NUL bytes.")

            if max_size is not None and isinstance(max_size, int) and max_size >= 0:
                if len(encoded) > max_size:
                    raise ValueError(f"Text size ({len(encoded)} bytes) exceeds the maximum allowed size of {max_size} bytes.")

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

        # Register edit session
        edit_session = manager.register_session(
            session_id=session_id,
            title=title,
            text=text,
            max_size=max_size,
            on_save=on_save,
            on_cancel=on_cancel
        )

        self._create_task(display_file_edit_prompt(self.protocol.session.channel, edit_session, manager))

    def handle_write_response(self, data):
        session_id = data.get("id")
        result = data.get("result")

        manager = self.get_editor_manager()
        if not manager:
            return

        if session_id is not None:
            if result is True:
                # Successfully saved
                manager.unregister_session(session_id)
            else:
                # Error saving
                if self.protocol.session:
                    self._create_task(self.protocol.session.channel.send(
                        f"❌ **Server write failed for edit session {session_id}:** {result}"
                    ))

    def handle_cancel_response(self, data):
        session_id = data.get("id")
        result = data.get("result")

        manager = self.get_editor_manager()
        if not manager:
            return

        if session_id is not None:
            if result is True:
                manager.unregister_session(session_id)
            else:
                if self.protocol.session:
                    self._create_task(self.protocol.session.channel.send(
                        f"❌ **Server cancel failed for edit session {session_id}:** {result}"
                    ))

    def handle_error(self, data):
        message = data.get("message", "Unknown error")
        if self.protocol.session:
            self._create_task(self.protocol.session.channel.send(
                f"⚠️ **GMCP MUME.Client Error:** {message}"
            ))
