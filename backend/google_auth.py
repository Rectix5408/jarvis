"""Compatibility facade for the canonical Jarvis Google OAuth service.

The normal connector migrated from Device Flow to the Authorization Code Web
Flow. Keeping this module prevents older imports from failing while ensuring
there is only one credential reader and token writer.
"""

from backend.tools.google_auth import (  # noqa: F401
    get_service,
    get_status,
    is_authenticated,
    revoke,
)


def get_credentials():
    """Return credentials from the canonical server-side token store."""
    from backend.tools.google_auth import _load_credentials

    return _load_credentials()


def start_device_flow() -> dict:
    """Deprecated compatibility call; Device Flow is intentionally disabled."""
    return {"error": "device_flow_disabled"}


def get_flow_status() -> dict:
    """Deprecated compatibility status without token-writing behavior."""
    return {"status": "disabled", "message": "device_flow_disabled"}
