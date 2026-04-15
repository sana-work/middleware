from datetime import datetime, timezone

def utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)

def format_iso(dt: datetime) -> str:
    """Format a datetime to an ISO 8601 string."""
    return dt.isoformat()
