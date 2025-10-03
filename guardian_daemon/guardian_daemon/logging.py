"""
Guardian logging setup module.
Configures log level, format, and target based on the application config.
"""

import logging

import structlog

_logging_configured = False


def setup_logging(config):
    """
    Sets up structlog and stdlib logging according to the provided config.
    This function should only be called once at application startup.
    """
    global _logging_configured
    if _logging_configured:
        return

    logging_cfg = config.get("logging", {})
    level = getattr(logging, logging_cfg.get("level", "INFO").upper(), logging.INFO)
    fmt = logging_cfg.get("format", "plain")

    processors = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if fmt == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Basic configuration for non-structlog loggers
    logging.basicConfig(
        level=level,
        format="%(message)s",
    )
    _logging_configured = True


def get_logger(name):
    """
    Returns a configured structlog logger instance.
    """
    return structlog.get_logger(name)
