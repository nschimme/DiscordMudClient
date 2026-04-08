import re
import colorsys

# --- Constants ---

# Discord Foreground Palette (RGB tuples)
DISCORD_FG = {
    30: (0x35, 0x37, 0x3D),
    31: (0xD2, 0x1C, 0x24),
    32: (0x73, 0x8A, 0x05),
    33: (0xA5, 0x77, 0x05),
    34: (0x20, 0x76, 0xC7),
    35: (0xC6, 0x1B, 0x6F),
    36: (0x25, 0x92, 0x86),
    37: (0xFF, 0xFF, 0xFF),
}

# Discord Background Palette (RGB tuples)
DISCORD_BG = {
    40: (0x02, 0x20, 0x29),
    41: (0xBD, 0x36, 0x12),
    42: (0x47, 0x5B, 0x62),
    43: (0x53, 0x68, 0x70),
    44: (0x70, 0x82, 0x85),
    45: (0x59, 0x5A, 0xB7),
    46: (0x81, 0x90, 0x90),
    47: (0xFC, 0xF4, 0xDC),
}

# Standard 4-bit Palette (Input RGB Mapping)
ANSI_4BIT_RGB = [
    (0, 0, 0),       # 0: black
    (184, 48, 25),   # 1: red
    (81, 191, 55),   # 2: green
    (198, 196, 61),  # 3: yellow
    (12, 36, 191),   # 4: blue
    (185, 62, 193),  # 5: magenta
    (83, 194, 197),  # 6: cyan
    (255, 255, 255), # 7: white
]

# SGR Codes
SGR_RESET = 0
SGR_BOLD_ON = 1
SGR_UNDERLINE_ON = 4
SGR_BOLD_OFF_1 = 21
SGR_BOLD_OFF_2 = 22
SGR_UNDERLINE_OFF = 24
SGR_FG_EXTENDED = 38
SGR_BG_EXTENDED = 48

# Extended Color Modes
COLOR_MODE_8BIT = 5
COLOR_MODE_24BIT = 2

# Thresholds and Factors
WHITE_THRESHOLD = 200
SATURATION_FACTOR = 10.0

# --- Helper Functions ---

def get_xterm_256_palette():
    """Generates the standard xterm 256-color palette."""
    palette = [None] * 256
    # 0-7: Use explicit 4-bit Discord RGB palette
    for i in range(8):
        palette[i] = ANSI_4BIT_RGB[i]

    # 8-15: Standard xterm bright values
    brights = [
        (128, 128, 128), (255, 0, 0), (0, 255, 0), (255, 255, 0),
        (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255)
    ]
    for i in range(8):
        palette[8+i] = brights[i]

    # 16-231: 6x6x6 color cube
    levels = [0, 95, 135, 175, 215, 255]
    for r in range(6):
        for g in range(6):
            for b in range(6):
                idx = 16 + r * 36 + g * 6 + b
                palette[idx] = (levels[r], levels[g], levels[b])

    # 232-255: Grayscale ramp
    for i in range(24):
        v = 8 + i * 10
        palette[232+i] = (v, v, v)

    return palette

XTERM_256_PALETTE = get_xterm_256_palette()

def adjust_saturation(rgb, factor=SATURATION_FACTOR):
    """Aggressively boosts the saturation of a color."""
    r_in, g_in, b_in = [x / 255.0 for x in rgb]
    h, l, s = colorsys.rgb_to_hls(r_in, g_in, b_in)
    s = min(1.0, s * factor)
    r_out, g_out, b_out = colorsys.hls_to_rgb(h, l, s)
    return (int(r_out * 255), int(g_out * 255), int(b_out * 255))

def get_closest_ansi(rgb, palette_dict):
    """Finds the closest ANSI code in a palette using squared Euclidean distance."""
    best_code = None
    min_dist = float('inf')
    r1, g1, b1 = rgb
    for code, (r2, g2, b2) in palette_dict.items():
        dr = r1 - r2
        dg = g1 - g2
        db = b1 - b2
        dist_sq = dr * dr + dg * dg + db * db
        if dist_sq < min_dist:
            min_dist = dist_sq
            best_code = code
    return best_code

# --- Core Logic ---

class SGRState:
    """Maintains the current ANSI Select Graphic Rendition state."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.bold = False
        self.underline = False
        self.fg = None
        self.bg = None

    def copy(self):
        new_state = SGRState()
        new_state.bold = self.bold
        new_state.underline = self.underline
        new_state.fg = self.fg
        new_state.bg = self.bg
        return new_state

    def apply_params(self, params):
        """Processes a list of SGR parameters and updates the current state."""
        resetted = False
        i = 0
        while i < len(params):
            p = params[i]
            if p == SGR_RESET:
                self.reset()
                resetted = True
            elif p == SGR_BOLD_ON:
                self.bold = True
            elif p == SGR_UNDERLINE_ON:
                self.underline = True
            elif p == SGR_BOLD_OFF_1 or p == SGR_BOLD_OFF_2:
                self.bold = False
            elif p == SGR_UNDERLINE_OFF:
                self.underline = False
            elif 30 <= p <= 37:
                self.fg = self.normalize_4bit_color(p - 30, is_bg=False)
            elif 90 <= p <= 97:
                self.fg = self.normalize_4bit_color(p - 90, is_bg=False)
            elif 40 <= p <= 47:
                self.bg = self.normalize_4bit_color(p - 40, is_bg=True)
            elif 100 <= p <= 107:
                self.bg = self.normalize_4bit_color(p - 100, is_bg=True)
            elif p == SGR_FG_EXTENDED or p == SGR_BG_EXTENDED:
                is_bg = (p == SGR_BG_EXTENDED)
                # Direct mapping for 8-bit color palette (0-7) to match 4-bit colors
                if i + 2 < len(params) and params[i+1] == COLOR_MODE_8BIT and params[i+2] < 8:
                    color = self.normalize_4bit_color(params[i+2], is_bg)
                    if is_bg: self.bg = color
                    else: self.fg = color
                    i += 2
                else:
                    i, rgb = self._parse_extended_color(params, i)
                    if rgb:
                        color = self.process_rgb(rgb, is_bg)
                        if is_bg: self.bg = color
                        else: self.fg = color
            i += 1
        return resetted

    def _parse_extended_color(self, params, index):
        """Extracts RGB from 8-bit (38;5;N) or 24-bit (38;2;R;G;B) color parameters."""
        if index + 1 >= len(params):
            return index, None

        mode = params[index + 1]

        if mode == COLOR_MODE_8BIT:
            color_idx = params[index + 2] if index + 2 < len(params) else 0
            rgb = XTERM_256_PALETTE[color_idx % 256]
            return index + 2, rgb

        elif mode == COLOR_MODE_24BIT:
            # Handle standard 38;2;R;G;B and variants with optional color space
            if index + 5 < len(params) and params[index + 2] == 0:
                # Format: 38;2;0;R;G;B
                r, g, b = params[index + 3], params[index + 4], params[index + 5]
                consumed = 5
            elif index + 4 < len(params):
                # Standard format: 38;2;R;G;B
                r, g, b = params[index + 2], params[index + 3], params[index + 4]
                consumed = 4
            else:
                # Handle truncated parameter lists
                r = params[index + 2] if index + 2 < len(params) else 0
                g = params[index + 3] if index + 3 < len(params) else 0
                b = params[index + 4] if index + 4 < len(params) else 0
                consumed = 4
            rgb = (r % 256, g % 256, b % 256)
            return index + consumed, rgb

        return index, None

    def normalize_4bit_color(self, idx, is_bg):
        """Maps a standard 4-bit color index to a Discord-compatible ANSI code."""
        # Use direct mapping for 4-bit colors to ensure consistent behavior across MUDs
        return (idx % 8 + 40) if is_bg else (idx % 8 + 30)

    def process_rgb(self, rgb, is_bg):
        """Converts an RGB color to the closest Discord-compatible ANSI code."""
        r, g, b = rgb
        # White detection threshold bypasses saturation adjustment
        if r > WHITE_THRESHOLD and g > WHITE_THRESHOLD and b > WHITE_THRESHOLD:
            return 47 if is_bg else 37

        rgb = adjust_saturation(rgb)
        palette = DISCORD_BG if is_bg else DISCORD_FG
        return get_closest_ansi(rgb, palette)

    def get_sequence(self, prev_state=None, explicit_reset=False):
        """Constructs a Discord-compatible ANSI sequence representing the current state."""
        # Calculate if we need a reset because an attribute was turned OFF.
        # Discord's limited ANSI only supports a full reset (0), no specific OFF codes.
        needs_reset = explicit_reset
        if prev_state and not needs_reset:
            # If bold or underline were on and are now off, we must reset
            if (prev_state.bold and not self.bold) or \
               (prev_state.underline and not self.underline) or \
               (prev_state.fg is not None and self.fg is None) or \
               (prev_state.bg is not None and self.bg is None):
                needs_reset = True

        parts = []
        if needs_reset:
            # Emit reset code only if we were NOT already in a default state
            # or if it was explicitly requested by the input (e.g. \x1b[0m)
            was_already_default = not prev_state or (
                not prev_state.bold and
                not prev_state.underline and
                prev_state.fg is None and
                prev_state.bg is None
            )
            if explicit_reset or not was_already_default:
                parts.append(str(SGR_RESET))

        # Add attributes that are logically ON
        # Optimized to only emit if state changed or if we just reset
        if self.bold:
            if needs_reset or not prev_state or not prev_state.bold:
                parts.append(str(SGR_BOLD_ON))
        if self.underline:
            if needs_reset or not prev_state or not prev_state.underline:
                parts.append(str(SGR_UNDERLINE_ON))
        if self.fg is not None:
            if needs_reset or not prev_state or prev_state.fg != self.fg:
                parts.append(str(self.fg))
        if self.bg is not None:
            if needs_reset or not prev_state or prev_state.bg != self.bg:
                parts.append(str(self.bg))

        if not parts:
            return f"\x1b[{SGR_RESET}m" if explicit_reset else ""

        return f"\x1b[{';'.join(parts)}m"

def parse_sgr_params(param_str):
    """Parses semicolon or colon separated ANSI SGR parameters into a list of integers."""
    if not param_str:
        return [SGR_RESET]
    raw_params = re.split(r'[;:]', param_str)
    params = []
    for p in raw_params:
        if p == '':
            params.append(SGR_RESET)
        else:
            try:
                params.append(int(p))
            except ValueError:
                params.append(SGR_RESET)
    return params

# Regex for ANSI SGR: ESC [ parameters m
ANSI_SGR_RE = re.compile(r'\x1b\[([\d;:]*)m')

def transform_ansi_to_discord(text: str) -> str:
    """Transforms a stream of ANSI-coded text into Discord-compatible formatting."""
    result = []
    last_end = 0
    state = SGRState()
    prev_emitted_state = SGRState()

    for match in ANSI_SGR_RE.finditer(text):
        # Add plain text segment before the escape sequence
        result.append(text[last_end:match.start()])

        param_str = match.group(1)
        params = parse_sgr_params(param_str)

        # Apply the parameters to our internal state
        explicit_reset = state.apply_params(params)

        # Generate the optimized Discord-compatible sequence
        seq = state.get_sequence(prev_state=prev_emitted_state, explicit_reset=explicit_reset)
        if seq:
            # Check if this sequence is effectively a no-op reset
            is_redundant_reset = (
                seq == f"\x1b[{SGR_RESET}m" and
                not prev_emitted_state.bold and
                not prev_emitted_state.underline and
                prev_emitted_state.fg is None and
                prev_emitted_state.bg is None and
                not explicit_reset
            )

            if not is_redundant_reset:
                result.append(seq)
                prev_emitted_state = state.copy()

        last_end = match.end()

    # Add trailing plain text
    result.append(text[last_end:])
    return "".join(result)
