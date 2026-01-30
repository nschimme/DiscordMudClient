import urllib.parse
import os
import subprocess

def get_version():
    """
    Retrieves the application version.
    1. Checks for src/VERSION file (created during build).
    2. Tries to get the short git SHA.
    3. Falls back to 'dev'.
    """
    # 1. Check for VERSION file
    version_file = os.path.join(os.path.dirname(__file__), 'VERSION')
    if os.path.exists(version_file):
        try:
            with open(version_file, 'r') as f:
                return f.read().strip()
        except:
            pass

    # 2. Try git
    try:
        sha = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'],
                                    stderr=subprocess.DEVNULL).decode('ascii').strip()
        return f"sha-{sha}"
    except:
        pass

    # 3. Fallback
    return "dev"

def parse_mud_url(url_str):
    """
    Parses a MUD URL and returns (protocol, host, port, path).
    Defaults to 'telnets://' if no protocol is specified.
    """
    if not url_str:
        return None

    if '://' not in url_str:
        url_str = 'telnets://' + url_str

    parsed = urllib.parse.urlparse(url_str)
    protocol = parsed.scheme.lower()
    host = parsed.hostname
    port = parsed.port
    path = parsed.path or '/'

    if not port:
        if protocol == 'telnet': port = 23
        elif protocol == 'telnets': port = 992
        elif protocol == 'ws': port = 80
        elif protocol == 'wss': port = 443

    return protocol, host, port, path

EMOJI_MAP = {
    # Unicode Emojis
    "🙂": ":)",
    "😊": ":)",
    "☺️": ":)",
    "😉": ";)",
    "😄": ":D",
    "😀": ":D",
    "😃": ":D",
    "😆": "XD",
    "😅": ":D",
    "😮": ":O",
    "😲": ":O",
    "😱": ":O",
    "🙁": ":(",
    "☹️": ":(",
    "😞": ":(",
    "😟": ":(",
    "😢": ":'(",
    "😭": ":'(",
    "😛": ":P",
    "😜": ";P",
    "😋": ":P",
    "😝": "XP",
    "😡": ":@",
    "😠": ":@",
    "😎": "8)",
    "🙄": ":/",
    "😕": ":/",
    "🫤": ":/",
    "🤔": ":?",
    "😐": ":|",
    "😑": ":|",
    "😶": ":|",
    "😍": "<3",
    "❤️": "<3",
    "💕": "<3",
    "💖": "<3",
    "💗": "<3",
    "👍": "+1",
    "👎": "-1"
}

def transliterate_emojis(text):
    """
    Transliterates common emojis and shortcodes into equivalent ASCII smileys.
    Handles variation selectors and common Discord auto-conversions.
    """
    if not text:
        return text

    # Strip Variation Selector-16 (U+FE0F) which Discord often appends
    text = text.replace("\ufe0f", "")

    # Strip skin tone modifiers (U+1F3FB to U+1F3FF) to prevent dangling bytes
    for i in range(0x1f3fb, 0x1f400):
        text = text.replace(chr(i), "")

    for target, replacement in EMOJI_MAP.items():
        text = text.replace(target, replacement)

    return text
