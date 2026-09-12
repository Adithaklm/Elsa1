"""Fix the uptime value used by script.FILE_MSG in file delivery notifications."""
import time

from info import BOT_START_TIME
from Script import script


_UNITS = (
    ("week", 60 * 60 * 24 * 7),
    ("day", 60 * 60 * 24),
    ("hour", 60 * 60),
    ("min", 60),
    ("sec", 1),
)


def _human_uptime(seconds):
    parts = []
    for unit, divisor in _UNITS:
        amount, seconds = divmod(int(seconds), divisor)
        if amount:
            parts.append(f'{amount} {unit}{"" if amount == 1 else "s"}')
    return ", ".join(parts) or "0 sec"


_original_file_msg = script.FILE_MSG


class _UptimeFileMessage(str):
    def format(self, *args, **kwargs):
        if len(args) >= 4 and args[3] in ("", None):
            values = list(args)
            values[3] = _human_uptime(max(0, time.time() - BOT_START_TIME))
            args = tuple(values)
        return super().format(*args, **kwargs)


script.FILE_MSG = _UptimeFileMessage(_original_file_msg)
