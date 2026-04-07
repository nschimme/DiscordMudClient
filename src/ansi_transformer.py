import re
import colorsys

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

# 4-bit Palette (Input RGB Mapping)
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

def get_xterm_256_palette():
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

def adjust_saturation(rgb, factor=10.0):
    r_in, g_in, b_in = [x / 255.0 for x in rgb]
    h, l, s = colorsys.rgb_to_hls(r_in, g_in, b_in)
    s = min(1.0, s * factor)
    r_out, g_out, b_out = colorsys.hls_to_rgb(h, l, s)
    return (int(r_out * 255), int(g_out * 255), int(b_out * 255))

def get_closest_ansi(rgb, palette_dict):
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

class SGRState:
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
        resetted = False
        i = 0
        while i < len(params):
            p = params[i]
            if p == 0:
                self.reset()
                resetted = True
            elif p == 1:
                self.bold = True
            elif p == 4:
                self.underline = True
            elif p == 21 or p == 22:
                self.bold = False
            elif p == 24:
                self.underline = False
            elif 30 <= p <= 37:
                self.fg = self.normalize_color(p - 30, is_bg=False)
            elif 90 <= p <= 97:
                self.fg = self.normalize_color(p - 90, is_bg=False)
            elif 40 <= p <= 47:
                self.bg = self.normalize_color(p - 40, is_bg=True)
            elif 100 <= p <= 107:
                self.bg = self.normalize_color(p - 100, is_bg=True)
            elif p == 38 or p == 48:
                is_bg = (p == 48)
                if i + 1 < len(params):
                    mode = params[i+1]
                    if mode == 5: # 8-bit
                        color_idx = params[i+2] if i + 2 < len(params) else 0
                        rgb = XTERM_256_PALETTE[color_idx % 256]
                        if is_bg: self.bg = self.process_rgb(rgb, is_bg=True)
                        else: self.fg = self.process_rgb(rgb, is_bg=False)
                        i += 2
                    elif mode == 2: # 24-bit
                        if i + 5 < len(params):
                            if params[i+2] == 0:
                                r, g, b = params[i+3], params[i+4], params[i+5]
                                i += 5
                            else:
                                r, g, b = params[i+2], params[i+3], params[i+4]
                                i += 4
                        elif i + 4 < len(params):
                            r, g, b = params[i+2], params[i+3], params[i+4]
                            i += 4
                        else:
                            r = params[i+2] if i + 2 < len(params) else 0
                            g = params[i+3] if i + 3 < len(params) else 0
                            b = params[i+4] if i + 4 < len(params) else 0
                            i += 4
                        rgb = (r % 256, g % 256, b % 256)
                        if is_bg: self.bg = self.process_rgb(rgb, is_bg=True)
                        else: self.fg = self.process_rgb(rgb, is_bg=False)
            i += 1
        return resetted

    def normalize_color(self, idx, is_bg):
        rgb = ANSI_4BIT_RGB[idx % 8]
        return self.process_rgb(rgb, is_bg)

    def process_rgb(self, rgb, is_bg):
        r, g, b = rgb
        if r > 200 and g > 200 and b > 200:
            return 47 if is_bg else 37
        rgb = adjust_saturation(rgb)
        palette = DISCORD_BG if is_bg else DISCORD_FG
        return get_closest_ansi(rgb, palette)

    def get_sequence(self, prev_state=None, explicit_reset=False):
        needs_reset = explicit_reset
        if prev_state and not needs_reset:
            if (prev_state.bold and not self.bold) or \
               (prev_state.underline and not self.underline) or \
               (prev_state.fg is not None and self.fg is None) or \
               (prev_state.bg is not None and self.bg is None):
                needs_reset = True

        parts = []
        if needs_reset:
            # Only add 0 if we were NOT already in a reset state
            if prev_state and (prev_state.bold or prev_state.underline or prev_state.fg is not None or prev_state.bg is not None):
                parts.append("0")
            elif explicit_reset:
                # If it was explicit, maybe they want to be sure?
                # But to stay minimal, we skip if already reset.
                # Actually, some terminals use ESC[m to ensure default.
                # Let's include 0 if it was explicit.
                parts.append("0")

        if self.bold:
            if needs_reset or not prev_state or not prev_state.bold:
                parts.append("1")
        if self.underline:
            if needs_reset or not prev_state or not prev_state.underline:
                parts.append("4")
        if self.fg is not None:
            if needs_reset or not prev_state or prev_state.fg != self.fg:
                parts.append(str(self.fg))
        if self.bg is not None:
            if needs_reset or not prev_state or prev_state.bg != self.bg:
                parts.append(str(self.bg))

        if not parts:
            return "\x1b[0m" if explicit_reset else ""

        return f"\x1b[{';'.join(parts)}m"

def parse_sgr_params(param_str):
    if not param_str:
        return [0]
    raw_params = re.split(r'[;:]', param_str)
    params = []
    for p in raw_params:
        if p == '':
            params.append(0)
        else:
            try:
                params.append(int(p))
            except ValueError:
                params.append(0)
    return params

# Regex for ANSI SGR: ESC [ parameters m
ANSI_SGR_RE = re.compile(r'\x1b\[([\d;:]*)m')

def transform_ansi_to_discord(text: str) -> str:
    result = []
    last_end = 0
    state = SGRState()
    prev_emitted_state = SGRState()

    for match in ANSI_SGR_RE.finditer(text):
        result.append(text[last_end:match.start()])
        param_str = match.group(1)
        params = parse_sgr_params(param_str)

        explicit_reset = state.apply_params(params)

        seq = state.get_sequence(prev_state=prev_emitted_state, explicit_reset=explicit_reset)
        if seq:
            if seq == "\x1b[0m" and not prev_emitted_state.bold and not prev_emitted_state.underline and prev_emitted_state.fg is None and prev_emitted_state.bg is None and not explicit_reset:
                pass
            else:
                result.append(seq)
                prev_emitted_state = state.copy()

        last_end = match.end()

    result.append(text[last_end:])
    return "".join(result)
