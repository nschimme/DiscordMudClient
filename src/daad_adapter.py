import sys
import os
import importlib.util

# Get the path to the submodule
LIB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "libs", "daad"))
SCRIPT_PATH = os.path.join(LIB_PATH, "discord-ansi-adapter.py")

# Function to dynamically load the module
def _load_daad():
    if not os.path.exists(SCRIPT_PATH):
        return None

    spec = importlib.util.spec_from_file_location("daad", SCRIPT_PATH)
    if spec is None:
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules["daad"] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        return None
    return module

_daad_module = _load_daad()

def process_ansi(sequence: str) -> str:
    """Processes an ANSI sequence through the DAAD adapter."""
    if _daad_module is None:
        return sequence

    try:
        # sequence is something like '\x1b[38;2;255;0;0m'
        return _daad_module.process_sequence(sequence)
    except Exception:
        # Fallback to original sequence if anything goes wrong
        return sequence
