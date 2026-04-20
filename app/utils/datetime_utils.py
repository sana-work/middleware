from datetime import datetime, timezone

def utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)

def format_iso(dt: datetime) -> str:
    """Format a datetime to a standard ISO 8601 string ending in Z."""
    if dt is None:
        return ""
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    # Ensure it's in UTC and formatted with Z instead of +00:00
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def normalize_iso_string(ts_str: str) -> str:
    """
    Takes an ISO string (possibly with Z, +00:00, or missing offset) 
    and returns a normalized ISO string ending in Z.
    """
    if not ts_str:
        return ""
    try:
        # Handle the common 'Z' suffix by replacing it with +00:00 for fromisoformat
        clean_ts = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_ts)
        return format_iso(dt)
    except (ValueError, TypeError):
        # Fallback to current time if unparseable, or just return as is if it's already a partial
        logger_placeholder = "Could not parse timestamp, returning as is"
        return ts_str
