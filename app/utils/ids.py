import uuid

def generate_correlation_id() -> str:
    """Generate a UUID v4 string for correlation_id."""
    return str(uuid.uuid4())

def generate_session_id() -> str:
    """Generate a UUID v4 string for session_id."""
    return str(uuid.uuid4())
