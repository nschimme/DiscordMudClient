# Discord Mud Client
# Copyright (C) 2026 Nils Schimmelmann
# SPDX-License-Identifier: AGPL-3.0-or-later
# See LICENSE.md for the full license text.
import io
import re
import discord
import logging
from discord.ui import View, Button, Modal, TextInput
logger = logging.getLogger(__name__)

# Local editor constants
DISCORD_MODAL_LIMIT = 4000
DEFAULT_TEXT_ENCODING = "utf-8"

class EditSession:
    def __init__(self, session_id, title, text, max_size, on_save, on_cancel, validator=None):
        self.id = session_id
        self.title = title or f"file_{session_id}"
        self.text = text if text is not None else ""
        self.max_size = max_size
        self.on_save = on_save       # async function taking text
        self.on_cancel = on_cancel   # async function
        self.validator = validator   # optional callable taking (session, updated_text)
        self.prompt_message = None   # Tracks the Discord prompt message with the view
        self.pending = None          # "save" / "cancel" while awaiting the server's answer

    def check_size_limit(self, size_in_bytes, is_safeguard=False):
        """
        Enforces a consistent size limit check.
        If is_safeguard is True, applies a generous margin to prevent false-positives
        on raw undecoded bytes, but reports the configured maximum limit consistently.
        """
        if self.max_size is None or not isinstance(self.max_size, int) or self.max_size < 0:
            return

        if is_safeguard:
            # Under UTF-8 encoding, characters occupy 1 to 4 bytes. We allow a safe margin of
            # 4x the maximum size plus a small buffer to prevent false-positives before decoding.
            early_limit = self.max_size * 4 + 100
            if size_in_bytes > early_limit:
                raise ValueError(
                    f"Uploaded file size ({size_in_bytes} bytes) exceeds the "
                    f"early protective safeguard limit ({early_limit} bytes) for maximum allowed size of {self.max_size} bytes."
                )
        else:
            if size_in_bytes > self.max_size:
                raise ValueError(
                    f"Text size ({size_in_bytes} bytes) exceeds the maximum allowed size of {self.max_size} bytes."
                )

    def validate(self, updated_text):
        """
        Validates the text length and runs any registered custom validator.
        Saves the text as a draft immediately to prevent data loss.
        """
        # Normalize line endings (Windows editors / Discord uploads) so modal and upload paths match
        updated_text = updated_text.replace('\r\n', '\n').replace('\r', '\n')

        # Save draft immediately to prevent user data loss
        self.text = updated_text

        # If a custom validator was provided (e.g. by MumeClientHandler), run it!
        if self.validator:
            self.validator(self, updated_text)

        # Enforce size limit check on UTF-8 bytes by default
        self.check_size_limit(len(updated_text.encode('utf-8')), is_safeguard=False)
        return updated_text

class EditorModal(Modal):
    def __init__(self, edit_session, manager, view_message_to_update=None):
        # Modal title can be at most 45 characters
        title = f"Edit: {edit_session.title}"[:45]
        super().__init__(title=title)
        self.edit_session = edit_session
        self.manager_ref = manager
        self.view_message_to_update = view_message_to_update

        # Limit text input to DISCORD_MODAL_LIMIT characters
        initial_val = edit_session.text
        if len(initial_val) > DISCORD_MODAL_LIMIT:
            initial_val = initial_val[:DISCORD_MODAL_LIMIT]

        self.text_input = TextInput(
            label="File Content",
            style=discord.TextStyle.paragraph,
            placeholder="Type your content here...",
            default=initial_val,
            max_length=DISCORD_MODAL_LIMIT,
            required=False
        )
        self.add_item(self.text_input)

    async def on_submit(self, interaction: discord.Interaction):
        text_val = self.text_input.value
        await interaction.response.defer(ephemeral=True)
        try:
            # Enforce centralized validations and draft saving
            text_val = self.edit_session.validate(text_val)
            await self.edit_session.on_save(text_val)
            await interaction.followup.send("⏳ Sent to the server, waiting for confirmation...", ephemeral=True)

            # The final ✅/❌ is shown once the server answers (see EditorManager.resolve)
            if not self.edit_session.prompt_message:
                self.edit_session.prompt_message = self.view_message_to_update
            await self.manager_ref.set_pending(self.edit_session, "save")
        except ValueError as e:
            # Safe validation error
            await interaction.followup.send(f"❌ Validation Error: {e}", ephemeral=True)
        except Exception as e:
            # Unexpected error: log securely with traceback and return generic error message
            logger.exception("Unexpected error in EditorModal.on_submit:")
            await interaction.followup.send("❌ An unexpected error occurred while saving the content. Please try again.", ephemeral=True)

class EditorView(View):
    def __init__(self, edit_session, manager):
        super().__init__(timeout=None) # persistent
        self.edit_session = edit_session
        self.manager = manager

        # Determine if edit button is disabled
        text_len = len(edit_session.text) if edit_session.text else 0
        disable_edit = text_len > DISCORD_MODAL_LIMIT

        self.edit_btn = Button(
            label="Edit" if not disable_edit else f"Too long for Modal (>{DISCORD_MODAL_LIMIT // 1000}k)",
            style=discord.ButtonStyle.green,
            disabled=disable_edit,
            custom_id=f"editor_edit_{edit_session.id}"
        )
        self.edit_btn.callback = self.on_edit_click
        self.add_item(self.edit_btn)

        self.cancel_btn = Button(
            label="Cancel",
            style=discord.ButtonStyle.red,
            custom_id=f"editor_cancel_{edit_session.id}"
        )
        self.cancel_btn.callback = self.on_cancel_click
        self.add_item(self.cancel_btn)

    async def on_edit_click(self, interaction: discord.Interaction):
        current_sess = self.manager.get_session(self.edit_session.id)
        if not current_sess:
            await interaction.response.send_message("❌ This edit session is no longer active.", ephemeral=True)
            return

        modal = EditorModal(self.edit_session, self.manager, view_message_to_update=interaction.message)
        await interaction.response.send_modal(modal)

    async def on_cancel_click(self, interaction: discord.Interaction):
        current_sess = self.manager.get_session(self.edit_session.id)
        if not current_sess:
            await interaction.response.send_message("❌ This edit session is no longer active.", ephemeral=True)
            return

        try:
            await self.edit_session.on_cancel()
            await interaction.response.edit_message(content=f"⏳ Cancelling '{self.edit_session.title}' (ID: {self.edit_session.id}), waiting for confirmation...", view=None)
            self.edit_session.pending = "cancel"
        except Exception as e:
            # Unexpected error: log securely and return generic error message
            logger.exception("Unexpected error during edit cancellation in on_cancel_click:")
            msg = "❌ An unexpected error occurred while cancelling the edit session."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)

class EditorManager:
    def __init__(self, mud_session):
        self.mud_session = mud_session
        self.active_sessions = {} # {id: EditSession}

    def register_session(self, session_id, title, text, max_size, on_save, on_cancel, validator=None):
        edit_session = EditSession(session_id, title, text, max_size, on_save, on_cancel, validator=validator)
        self.active_sessions[session_id] = edit_session
        return edit_session

    def get_session(self, session_id):
        return self.active_sessions.get(session_id)

    def unregister_session(self, session_id):
        return self.active_sessions.pop(session_id, None)

    async def _edit_prompt(self, session, **kwargs):
        if session.prompt_message:
            try:
                await session.prompt_message.edit(**kwargs)
            except Exception:
                logger.debug("Failed to edit edit-prompt message", exc_info=True)

    async def set_pending(self, session, action):
        session.pending = action
        verb = "Sent to the server" if action == "save" else "Cancelling"
        await self._edit_prompt(session, content=f"⏳ **{verb}** for '{session.title}' (ID: {session.id}), waiting for confirmation...", view=None)

    async def resolve(self, session_id, ok, detail=None):
        """
        Called when the server answers a Write/CancelEdit (or cancels on its own).
        On success the session ends; on failure the prompt is restored so the user can retry.
        """
        session = self.get_session(session_id)
        if not session:
            return
        action = session.pending or "save"
        session.pending = None
        if ok:
            self.unregister_session(session_id)
            word = "Saved edit" if action == "save" else "Edit cancelled"
            icon = "✅" if action == "save" else "❌"
            await self._edit_prompt(session, content=f"{icon} **{word}** for '{session.title}' (ID: {session.id}).", view=None)
        else:
            await self._edit_prompt(
                session,
                content=f"❌ **Server rejected the {action}** for '{session.title}' (ID: {session.id}): {detail}\n"
                        f"Your draft is kept, so you can edit and submit again.",
                view=EditorView(session, self),
            )

    async def end_all(self, reason):
        """End every active edit session (e.g. on disconnect), re-attaching unsaved drafts."""
        sessions = list(self.active_sessions.values())
        self.active_sessions.clear()
        for session in sessions:
            await self._edit_prompt(session, content=f"⚠️ **Edit ended** for '{session.title}' (ID: {session.id}): {reason}", view=None)
            if session.text:
                try:
                    await display_file_view(self.mud_session.channel, f"unsaved_{session.title}", session.text)
                except Exception:
                    logger.exception("Failed to re-attach unsaved draft")

    def extract_id_from_filename(self, filename):
        # Match IDs from filenames starting with "edit_<id>_" or "edit_<id>.txt"
        match = re.search(r'^edit_(\d+)(?:[_\.]|$)', filename.lower())
        if match:
            return int(match.group(1))
        return None

    async def handle_file_upload(self, filename, content_bytes):
        """
        Processes an uploaded file. If it matches an active edit session,
        submits it. Returns True if a session was handled, False otherwise.
        """
        # Try to extract the ID from the filename
        target_id = self.extract_id_from_filename(filename)

        if target_id is not None:
            session = self.get_session(target_id)
            if not session:
                # User uploaded a file with a matching pattern but no active session
                await self.mud_session.channel.send(
                    f"⚠️ **No active edit session found!** This file looks like an edit file, "
                    f"but you do not have an active edit session for ID `{target_id}`. "
                    f"The file contents were not sent."
                )
                return True
        else:
            # Fallback matching
            if not self.active_sessions:
                return False

            if len(self.active_sessions) == 1:
                # Exactly one active session, apply it
                target_id = list(self.active_sessions.keys())[0]
                session = self.active_sessions[target_id]
            else:
                # Multiple active sessions, we cannot disambiguate without a file name match
                active_ids = ", ".join(str(k) for k in self.active_sessions.keys())
                example_id = list(self.active_sessions.keys())[0]
                await self.mud_session.channel.send(
                    f"⚠️ **Multiple active edit sessions!** Please name your uploaded file starting with "
                    f"`edit_<id>_` or `edit_<id>.txt` (for example, `edit_{example_id}_file.txt` or `edit_{example_id}.txt`) "
                    f"so I know which file you are updating. Active IDs: {active_ids}"
                )
                return True

        # Enforce a generic max_size constraint on raw content_bytes first to short-circuit early.
        # This protects the bot from decoding/processing excessively large files (e.g., multi-MB uploads).
        try:
            session.check_size_limit(len(content_bytes), is_safeguard=True)
        except ValueError as e:
            await self.mud_session.channel.send(f"❌ **File too large:** {e}")
            return True

        # Process the save
        try:
            # Attempt to decode as UTF-8 first, fall back to DEFAULT_TEXT_ENCODING
            text = content_bytes.decode(DEFAULT_TEXT_ENCODING, errors='replace')
            if text.startswith('\ufeff'):
                text = text[1:]  # strip BOM

            # Enforce centralized validations and draft saving on file uploads
            text = session.validate(text)
            await session.on_save(text)

            # Final ✅/❌ is shown once the server answers (see resolve)
            if session.prompt_message:
                await self.set_pending(session, "save")
            else:
                session.pending = "save"
                await self.mud_session.channel.send(f"⏳ Sent '{session.title}' (ID: {session.id}), waiting for confirmation...")
            return True
        except ValueError as e:
            # Safe validation error
            await self.mud_session.channel.send(f"❌ Validation Error: {e}")
            return True
        except Exception as e:
            # Unexpected error: log securely and return generic error message
            logger.exception("Unexpected error in EditorManager.handle_file_upload:")
            await self.mud_session.channel.send("❌ An unexpected error occurred while processing the uploaded file. Please try again.")
            return True

def sanitize_filename(title, prefix=None, fallback="file"):
    """
    Sanitizes a title into a safe, consistent filename.
    """
    sanitized = "".join(c for c in title if c.isalnum() or c in (' ', '_', '-', '.')).strip()
    sanitized = sanitized.replace(' ', '_')
    if not sanitized:
        sanitized = fallback
    if prefix:
        filename = f"{prefix}_{sanitized}"
    else:
        filename = sanitized
    if not filename.endswith(".txt"):
        filename += ".txt"
    return filename

async def display_file_view(channel, title, text):
    filename = sanitize_filename(title, fallback="view_file")

    # Fall back gracefully but attempt to encode appropriately using DEFAULT_TEXT_ENCODING.
    file_bytes = text.encode(DEFAULT_TEXT_ENCODING, errors='replace')

    file_fp = io.BytesIO(file_bytes)
    discord_file = discord.File(file_fp, filename=filename)
    await channel.send(content=f"📖 **Viewing: {title}**", file=discord_file)

async def display_file_edit_prompt(channel, edit_session, manager):
    prefix = f"edit_{edit_session.id}"
    filename = sanitize_filename(edit_session.title, prefix=prefix, fallback="file")

    text_content = edit_session.text if edit_session.text is not None else ""
    file_bytes = text_content.encode(DEFAULT_TEXT_ENCODING, errors='replace')

    file_fp = io.BytesIO(file_bytes)
    discord_file = discord.File(file_fp, filename=filename)

    view = EditorView(edit_session, manager)
    msg = f"📝 **Edit Request:** '{edit_session.title}' (ID: {edit_session.id})"
    if len(text_content) > DISCORD_MODAL_LIMIT:
        msg += f"\n⚠️ This file is too large to edit via Discord's inline input (>{DISCORD_MODAL_LIMIT // 1000}k characters). Please download the file, edit it, and upload the updated file back to this channel!"
    else:
        msg += "\n💡 You can edit this file inline using the **Edit** button below, OR download, edit, and upload it back here!"

    prompt_msg = await channel.send(content=msg, file=discord_file, view=view)
    edit_session.prompt_message = prompt_msg
