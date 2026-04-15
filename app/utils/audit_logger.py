import structlog
from typing import Any, Dict, List, Optional

logger = structlog.get_logger("audit")

# Fields to redact for security
SENSITIVE_FIELDS = {
    "token", 
    "access_token", 
    "clientSecret", 
    "Config-ID", 
    "X-Authorization-Coin",
    "password"
}

# Whitelist for body logging to avoid context leakage
ALLOWED_BODY_FIELDS = {
    "session_id",
    "correlation_id",
    "soeid",
    "metadata"
}

class AuditLogger:
    """
    Structured audit logger with strict redaction and context leakage protection.
    """

    @staticmethod
    def log_event(
        event_name: str, 
        user_id: str, 
        correlation_id: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None
    ):
        """Log a structured audit event."""
        payload = {}
        if data:
            for k, v in data.items():
                if k in SENSITIVE_FIELDS:
                    payload[k] = "***"
                elif k == "context" or k == "request_context":
                    # Redact context to avoid sensitive user input leakage
                    payload[k] = f"REDACTED (length: {len(str(v))})"
                elif k == "body" and isinstance(v, dict):
                    # Partial whitelist for bodies
                    payload[k] = {
                        bk: (bv if bk in ALLOWED_BODY_FIELDS else "***")
                        for bk, bv in v.items()
                    }
                else:
                    payload[k] = v

        logger.info(
            event_name,
            user_id=user_id,
            correlation_id=correlation_id,
            **payload
        )

audit_logger = AuditLogger()
