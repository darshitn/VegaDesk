"""Shared execution errors used across tool and system-action boundaries."""


class ToolError(Exception):
    """A rejected action; callers report it without writing a success receipt."""
