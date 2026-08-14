import io
import re
import discord
import logging
from discord.ui import View, Button, Modal, TextInput
logger = logging.getLogger(__name__)

# Local editor constants
DISCORD_MODAL_LIMIT = 4000
DEFAULT_TEXT_ENCODING = "iso-8859-1"

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
        # Save draft immediately to prevent user data loss
        self.text = updated_text

        # If a custom validator was provided (e.g. by MumeClientHandler), run it!
        if self.validator:
            self.validator(self, updated_text)

        # Enforce size limit check on UTF-8 bytes by default
        self.check_size_limit(len(updated_text.encode('utf-8')), is_safeguard=False)

class EditorModal(Modal):
    def __init__(self, edit_session, view_message_to_update=None):
        # Modal title can be at most 45 characters
        title = f"Edit: {edit_session.title}"[:45]
        super().__init__(title=title)
        self.edit_session = edit_session
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
            self.edit_session.validate(text_val)
            await self.edit_session.on_save(text_val)
            await interaction.followup.send("✅ Content submitted successfully!", ephemeral=True)

            # Standardize feedback: edit the prompt message to remove interactive buttons
            msg_to_edit = self.edit_session.prompt_message or self.view_message_to_update
            if msg_to_edit:
                try:
                    await msg_to_edit.edit(content=f"✅ **Saved edit** for '{self.edit_session.title}' (ID: {self.edit_session.id}).", view=None)
                except Exception:
                    pass
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

        modal = EditorModal(self.edit_session, view_message_to_update=interaction.message)
        await interaction.response.send_modal(modal)

    async def on_cancel_click(self, interaction: discord.Interaction):
        current_sess = self.manager.get_session(self.edit_session.id)
        if not current_sess:
            await interaction.response.send_message("❌ This edit session is no longer active.", ephemeral=True)
            return

        try:
            await self.edit_session.on_cancel()
            # Use standard response.edit_message for component interactions instead of edit_original_response
            await interaction.response.edit_message(content=f"❌ **Edit '{self.edit_session.title}' (ID: {self.edit_session.id})** was cancelled.", view=None)
        except Exception as e:
            # Unexpected error: log securely and return generic error message
            logger.exception("Unexpected error during edit cancellation in on_cancel_click:")
            await interaction.followup.send("❌ An unexpected error occurred while cancelling the edit session.", ephemeral=True)

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
            try:
                text = content_bytes.decode('utf-8')
            except UnicodeDecodeError:
                text = content_bytes.decode(DEFAULT_TEXT_ENCODING, errors='replace')

            # Enforce centralized validations and draft saving on file uploads
            session.validate(text)
            await session.on_save(text)

            # Standardize feedback: edit the prompt message to reflect the save and remove interactive buttons
            if session.prompt_message:
                try:
                    await session.prompt_message.edit(content=f"✅ **Saved edit** for '{session.title}' (ID: {session.id}) via file upload.", view=None)
                except Exception:
                    pass
            else:
                await self.mud_session.channel.send(f"✅ **Saved edit** for '{session.title}' (ID: {session.id}).")
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
    try:
        file_bytes = text.encode(DEFAULT_TEXT_ENCODING)
    except UnicodeEncodeError:
        file_bytes = text.encode('utf-8', errors='replace')

    file_fp = io.BytesIO(file_bytes)
    discord_file = discord.File(file_fp, filename=filename)
    await channel.send(content=f"📖 **Viewing: {title}**", file=discord_file)

async def display_file_edit_prompt(channel, edit_session, manager):
    prefix = f"edit_{edit_session.id}"
    filename = sanitize_filename(edit_session.title, prefix=prefix, fallback="file")

    text_content = edit_session.text if edit_session.text is not None else ""
    try:
        file_bytes = text_content.encode(DEFAULT_TEXT_ENCODING)
    except UnicodeEncodeError:
        file_bytes = text_content.encode('utf-8', errors='replace')

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
