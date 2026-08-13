import io
import re
import discord
from discord.ui import View, Button, Modal, TextInput

class EditSession:
    def __init__(self, session_id, title, text, max_size, on_save, on_cancel):
        self.id = session_id
        self.title = title or f"file_{session_id}"
        self.text = text if text is not None else ""
        self.max_size = max_size
        self.on_save = on_save       # async function taking text
        self.on_cancel = on_cancel   # async function

class EditorModal(Modal):
    def __init__(self, edit_session, view_message_to_update=None):
        # Modal title can be at most 45 characters
        title = f"Edit: {edit_session.title}"[:45]
        super().__init__(title=title)
        self.edit_session = edit_session
        self.view_message_to_update = view_message_to_update

        # Limit text input to 4000 characters (Discord limit)
        initial_val = edit_session.text
        if len(initial_val) > 4000:
            initial_val = initial_val[:4000]

        self.text_input = TextInput(
            label="File Content",
            style=discord.TextStyle.paragraph,
            placeholder="Type your content here...",
            default=initial_val,
            max_length=4000,
            required=False
        )
        self.add_item(self.text_input)

    async def on_submit(self, interaction: discord.Interaction):
        text_val = self.text_input.value
        await interaction.response.defer(ephemeral=True)
        try:
            await self.edit_session.on_save(text_val)
            await interaction.followup.send("✅ Content submitted successfully!", ephemeral=True)
            if self.view_message_to_update:
                try:
                    await self.view_message_to_update.edit(content=f"✅ **Edit '{self.edit_session.title}' (ID: {self.edit_session.id})** has been saved/submitted.", view=None)
                except Exception:
                    pass
        except Exception as e:
            await interaction.followup.send(f"❌ Error saving content: {e}", ephemeral=True)

class EditorView(View):
    def __init__(self, edit_session, manager):
        super().__init__(timeout=None) # persistent
        self.edit_session = edit_session
        self.manager = manager

        # Determine if edit button is disabled
        text_len = len(edit_session.text) if edit_session.text else 0
        disable_edit = text_len > 4000

        self.edit_btn = Button(
            label="Edit" if not disable_edit else "Too long for Modal (>4k)",
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

        await interaction.response.defer()
        try:
            await self.edit_session.on_cancel()
            await interaction.edit_original_response(content=f"❌ **Edit '{self.edit_session.title}' (ID: {self.edit_session.id})** was cancelled.", view=None)
        except Exception as e:
            await interaction.followup.send(f"❌ Error cancelling session: {e}", ephemeral=True)

class EditorManager:
    def __init__(self, mud_session):
        self.mud_session = mud_session
        self.active_sessions = {} # {id: EditSession}

    def register_session(self, session_id, title, text, max_size, on_save, on_cancel):
        edit_session = EditSession(session_id, title, text, max_size, on_save, on_cancel)
        self.active_sessions[session_id] = edit_session
        return edit_session

    def get_session(self, session_id):
        return self.active_sessions.get(session_id)

    def unregister_session(self, session_id):
        return self.active_sessions.pop(session_id, None)

    def extract_id_from_filename(self, filename):
        match = re.search(r'edit_(\d+)', filename.lower())
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
                    f"⚠️ **No active edit session found!** This file looks like a MUME edit file, "
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
                await self.mud_session.channel.send(
                    f"⚠️ **Multiple active edit sessions!** Please name your uploaded file starting with "
                    f"`edit_<id>_` (for example, `edit_{list(self.active_sessions.keys())[0]}.txt`) "
                    f"so I know which file you are updating. Active IDs: {active_ids}"
                )
                return True

        # Process the save
        try:
            # Attempt to decode as UTF-8 first, fall back to ISO-8859-1
            try:
                text = content_bytes.decode('utf-8')
            except UnicodeDecodeError:
                text = content_bytes.decode('iso-8859-1', errors='replace')
            await session.on_save(text)
            await self.mud_session.channel.send(f"✅ **Saved edit** for '{session.title}' (ID: {session.id}).")
            return True
        except Exception as e:
            await self.mud_session.channel.send(f"❌ Error processing uploaded file: {e}")
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

    # MUME specifies ISO-8859-1 for texts. We fall back gracefully but attempt
    # to encode appropriately.
    try:
        file_bytes = text.encode('iso-8859-1')
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
        file_bytes = text_content.encode('iso-8859-1')
    except UnicodeEncodeError:
        file_bytes = text_content.encode('utf-8', errors='replace')

    file_fp = io.BytesIO(file_bytes)
    discord_file = discord.File(file_fp, filename=filename)

    view = EditorView(edit_session, manager)
    msg = f"📝 **Edit Request:** '{edit_session.title}' (ID: {edit_session.id})"
    if len(text_content) > 4000:
        msg += "\n⚠️ This file is too large to edit via Discord's inline input. Please download the file, edit it, and upload the updated file back to this channel!"
    else:
        msg += "\n💡 You can edit this file inline using the **Edit** button below, OR download, edit, and upload it back here!"

    await channel.send(content=msg, file=discord_file, view=view)
