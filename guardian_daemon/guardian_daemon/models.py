"""
SQLAlchemy models for guardian_daemon.

Key design decisions:
- Session.id is an autoincrement primary key (not using logind session_id as PK)
- logind_session_id is stored but is transient and can be reused by the system
- Unique constraint on (username, date, start_time) to prevent duplicate sessions
"""

from datetime import datetime
from datetime import date as date_type
from typing import Optional

from sqlalchemy import (
    String,
    Integer,
    Float,
    DateTime,
    Date,
    Text,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


class Session(Base):
    """
    Represents a user session.
    
    Note: The id is an autoincrement value, NOT the logind session ID.
    The logind_session_id is stored separately as it's transient and can be
    reused by the system for different users on different days.
    """

    __tablename__ = "sessions"

    # Primary key - autoincrement
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # User information
    username: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    uid: Mapped[int] = mapped_column(Integer, nullable=False)

    # Date tracking
    date: Mapped[date_type] = mapped_column(Date, nullable=False, index=True)

    # Session tracking - logind_session_id is transient!
    logind_session_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Timestamps
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    # Duration in seconds
    duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Session metadata
    desktop: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    service: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Indexes and constraints
    __table_args__ = (
        Index("idx_username_date", "username", "date"),
        Index("idx_username_logind", "username", "logind_session_id"),
        UniqueConstraint("username", "date", "start_time", name="uq_user_date_start"),
    )

    def __repr__(self) -> str:
        return (
            f"<Session(id={self.id}, username={self.username}, "
            f"date={self.date}, logind_session_id={self.logind_session_id})>"
        )


class UserSettings(Base):
    """User settings and configuration."""

    __tablename__ = "user_settings"

    username: Mapped[str] = mapped_column(String(255), primary_key=True)
    settings: Mapped[str] = mapped_column(Text, nullable=False)  # JSON string

    def __repr__(self) -> str:
        return f"<UserSettings(username={self.username})>"


class Meta(Base):
    """Metadata key-value storage."""

    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)

    def __repr__(self) -> str:
        return f"<Meta(key={self.key}, value={self.value})>"


class History(Base):
    """Historical session summaries by user and date."""

    __tablename__ = "history"

    username: Mapped[str] = mapped_column(String(255), primary_key=True)
    date: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD format

    total_screen_time: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    first_login: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    last_logout: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    quota_exceeded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bonus_time_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[str] = mapped_column(String(50), nullable=False)

    def __repr__(self) -> str:
        return f"<History(username={self.username}, date={self.date})>"
