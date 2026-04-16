import logging
import sys
import os
from logging.handlers import RotatingFileHandler
import structlog
from app.core.config import settings

def setup_logging():
    """Configure structured JSON logging for both Console and File."""
    
    # Ensure logs directory exists
    log_dir = os.path.dirname(settings.LOG_FILE_PATH)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # Map text levels to logging constants
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    # Multi-handler setup for standard library
    stdout_handler = logging.StreamHandler(sys.stdout)
    file_handler = RotatingFileHandler(
        settings.LOG_FILE_PATH,
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )

    logging.basicConfig(
        format="%(message)s",
        level=log_level,
        handlers=[stdout_handler, file_handler]
    )

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

def get_logger(name: str):
    return structlog.get_logger(name)
