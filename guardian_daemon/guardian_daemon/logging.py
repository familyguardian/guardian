"""
Guardian logging setup module.
Configures log level, format, and target based on config.yaml/default-config.yaml.
"""

import logging
import os

import structlog
import yaml


def load_logging_config():
    """
    Loads logging configuration from config.yaml or default-config.yaml.
    Returns dict with level, format, target from the 'logging' section.
    """
    config_path = os.path.join(os.path.dirname(__file__), "../config.yaml")
    default_path = os.path.join(os.path.dirname(__file__), "../default-config.yaml")
    config = {}
    if os.path.exists(default_path):
        with open(default_path, "r") as f:
            config = yaml.safe_load(f)
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            user_config = yaml.safe_load(f)
        if user_config:
            config.update(user_config)
    # Extract logging config section
    logging_cfg = config.get("logging", {})
    level = logging_cfg.get("level", "INFO")
    fmt = logging_cfg.get("format", "plain")
    target = logging_cfg.get("target", "stdout")
    return {"level": level, "format": fmt, "target": target}


def setup_logging():
    """
    Sets up structlog and stdlib logging according to config.
    """
    cfg = load_logging_config()
    level = getattr(logging, cfg["level"].upper(), logging.INFO)
    processors = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
    ]
    if cfg["format"] == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())
    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(
        level=level,
        format="%(message)s",
    )


def get_logger(name):
    setup_logging()
    return structlog.get_logger(name)
