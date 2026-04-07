import sys
import os
import importlib.util
import logging
import io

# Set up logging for DAAD adapter
logger = logging.getLogger(__name__)

# Get the path to the submodule
LIB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "libs", "daad"))
SCRIPT_PATH = os.path.join(LIB_PATH, "discord-ansi-adapter.py")

# Function to dynamically load the module
def _load_daad():
    if not os.path.exists(SCRIPT_PATH):
        logger.warning(f"DAAD adapter script not found at {SCRIPT_PATH}. Color adaptation will be disabled.")
        return None

    try:
        spec = importlib.util.spec_from_file_location("daad", SCRIPT_PATH)
        if spec is None or spec.loader is None:
            logger.error(f"Failed to create spec or find loader for DAAD adapter script at {SCRIPT_PATH}.")
            return None

        module = importlib.util.module_from_spec(spec)
        # We don't set sys.modules until after success to avoid partial registration

        # The script attempts to read from sys.stdin on import, which will block.
        # We need to temporarily redirect stdin to prevent this.
        original_stdin = sys.stdin
        original_stdout = sys.stdout
        sys.stdin = io.StringIO("") # Empty input
        sys.stdout = io.StringIO("") # Capture any print calls

        try:
            spec.loader.exec_module(module)
            sys.modules["daad"] = module
        except Exception as e:
            logger.exception(f"Error executing DAAD adapter script: {e}")
            sys.modules.pop("daad", None)
            return None
        finally:
            sys.stdin = original_stdin
            sys.stdout = original_stdout

        logger.info("Successfully loaded DAAD adapter.")
        return module
    except Exception as e:
        logger.exception(f"Unexpected error while loading DAAD adapter: {e}")
        return None

# Global state for lazy loading
_daad_module = None
_load_attempted = False

def process_ansi(sequence: str) -> str:
    """Processes an ANSI sequence through the DAAD adapter with lazy loading."""
    global _daad_module, _load_attempted

    if not _load_attempted:
        _daad_module = _load_daad()
        _load_attempted = True

    if _daad_module is None:
        return sequence

    try:
        # sequence is something like '\x1b[38;2;255;0;0m'
        return _daad_module.process_sequence(sequence)
    except Exception as e:
        # Fallback to original sequence if anything goes wrong, but log the error
        logger.error(f"DAAD conversion failed for sequence {repr(sequence)}: {e}")
        return sequence
