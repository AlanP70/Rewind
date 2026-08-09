"""The one exception services raise for problems the user can fix.

Shared rather than per-service so the CLI has a single thing to catch. Anything
else reaching the top level is a bug and should keep its traceback.

The two subclasses exist because slice 3 gave these errors a second audience.
The CLI only ever needed "can the user fix this?", which the base class answers,
and it still catches only the base. HTTP needs more: an unknown course and a
re-upload of an already-chunked document are both the caller's fault, but 404 and
409 tell them different things, and collapsing both to 400 would make the API
disagree with the CLI about what a re-upload means. The distinction is carried by
the type rather than by matching on message text, which would break the first
time an error message is reworded.

They are not a general error hierarchy and should not grow into one. Add a
subclass when an entrypoint has to *act* differently, not to categorise.
"""


class ServiceError(Exception):
    """Something the caller can act on. The CLI prints it and exits non-zero."""


class NotFoundError(ServiceError):
    """Something the request named does not exist. HTTP 404."""


class ConflictError(ServiceError):
    """The request contradicts state that already exists, and could destroy it if
    honoured. HTTP 409, and `--force` on the CLI."""
