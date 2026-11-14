"""Keep the computer awake while a long import runs.

Sleep suspends the process and kills its open connections to the model API.
On macOS this runs the built-in ``caffeinate``; elsewhere it does nothing.
Closing a laptop lid still sleeps the machine unless it is on power with an
external display.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def keep_awake() -> Iterator[bool]:
    """Yields True if sleep is being prevented."""
    tool = shutil.which("caffeinate") if sys.platform == "darwin" else None
    if tool is None:
        yield False
        return
    try:
        # -d display, -i idle sleep, -m disk sleep, -s system sleep on AC power
        process = subprocess.Popen([tool, "-dims"], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
    except OSError:
        yield False
        return
    try:
        yield True
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
