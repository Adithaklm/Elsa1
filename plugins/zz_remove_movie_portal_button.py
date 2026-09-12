"""Disable the Movie Portal button on the bot /start screen."""

# commands.py imports MOVIE_WEB_URL into its own module namespace.
# Override that value after the commands plugin is loaded so the existing
# /start handler keeps all its normal buttons except Movie Portal.
try:
    from . import commands
    commands.MOVIE_WEB_URL = ""
except Exception:
    pass
