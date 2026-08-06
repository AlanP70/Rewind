"""The one exception services raise for problems the user can fix.

Shared rather than per-service so the CLI has a single thing to catch. Anything
else reaching the top level is a bug and should keep its traceback.
"""


class ServiceError(Exception):
    """Something the caller can act on. The CLI prints it and exits non-zero."""
