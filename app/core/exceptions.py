class IntegrationError(Exception):
    """Base exception for all integration errors."""
    pass

class TokenFetchError(IntegrationError):
    """Raised when pulling an auth token fails."""
    pass

class BackendExecutorError(IntegrationError):
    """Raised when the backend API call fails."""
    pass

class ExecutionNotFoundError(Exception):
    """Raised when an execution correlation_id is not found."""
    pass

class SessionNotFoundError(Exception):
    """Raised when a session_id is not found."""
    pass
