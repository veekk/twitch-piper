"""Set a recognizable Linux process name without changing Python's runtime."""
from pathlib import Path
import sys


def set_process_name(name):
    # Linux comm is the name shown by ps/top and desktop process monitors.
    # It is limited to 15 bytes, excluding the trailing NUL.
    if sys.platform.startswith('linux'):
        try:
            Path('/proc/self/comm').write_text(name.encode('utf-8')[:15].decode('utf-8', 'ignore'))
        except OSError:
            pass
    # When available, also update the full command-line title.
    try:
        from setproctitle import setproctitle
    except ImportError:
        return
    setproctitle(name)
