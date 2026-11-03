from django.utils.functional import SimpleLazyObject


def _enabled() -> bool:
    try:
        from .models import McpSettings

        return McpSettings.load().is_active
    except Exception:  # noqa: BLE001 — before the first migration there is no table
        return False


def mcp(request):
    """``mcp_enabled`` for the account tabs; evaluated only if a template asks."""
    return {"mcp_enabled": SimpleLazyObject(_enabled)}
