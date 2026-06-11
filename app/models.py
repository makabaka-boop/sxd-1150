from datetime import datetime, timedelta
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


class UserRole(str, PyEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    AUDITOR = "auditor"


class BookingStatus(str, PyEnum):
    DRAFT = "draft"
    PENDING = "pending"
    APPROVING = "approving"
    RETURNED = "returned"
    APPROVED = "approved"
    REJECTED = "rejected"
    TERMINATED = "terminated"
    CANCELLED = "cancelled"


class ApprovalAction(str, PyEnum):
    AGREE = "agree"
    RETURN = "return"
    TERMINATE = "terminate"


class TimeoutStrategy(str, PyEnum):
    REMIND = "remind"
    TRANSFER = "transfer"
    REJECT = "reject"


class DeleteType(str, PyEnum):
    NONE = "none"
    SOFT = "soft"
    HARD = "hard"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(100))
    role = Column(Enum(UserRole), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    submitted_bookings = relationship(
        "BookingApplication",
        foreign_keys="BookingApplication.submitted_by",
        back_populates="submitter",
    )
    approval_records = relationship("ApprovalRecord", back_populates="auditor")


class Venue(Base):
    __tablename__ = "venues"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    venue_type = Column(String(50), index=True, nullable=False)
    description = Column(Text)
    location = Column(String(200))
    capacity = Column(Integer)
    is_active = Column(Boolean, default=True)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    booking_rules = relationship("BookingRule", back_populates="venue")


class ApprovalTemplate(Base):
    __tablename__ = "approval_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    venue_type = Column(String(50), index=True, nullable=False)
    is_active = Column(Boolean, default=True)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    nodes = relationship(
        "ApprovalNode",
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="ApprovalNode.order_index",
    )
    booking_rules = relationship("BookingRule", back_populates="approval_template")


class ApprovalNode(Base):
    __tablename__ = "approval_nodes"

    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(Integer, ForeignKey("approval_templates.id"), nullable=False)
    node_name = Column(String(100), nullable=False)
    order_index = Column(Integer, nullable=False)
    auditor_role = Column(Enum(UserRole), default=UserRole.AUDITOR)
    timeout_minutes = Column(Integer, default=1440)
    timeout_strategy = Column(Enum(TimeoutStrategy), default=TimeoutStrategy.REMIND)
    transfer_to_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    template = relationship("ApprovalTemplate", back_populates="nodes")


class BookingRule(Base):
    __tablename__ = "booking_rules"

    id = Column(Integer, primary_key=True, index=True)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)
    approval_template_id = Column(Integer, ForeignKey("approval_templates.id"), nullable=False)
    current_version = Column(Integer, default=1)
    is_active = Column(Boolean, default=True)
    is_deleted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    venue = relationship("Venue", back_populates="booking_rules")
    approval_template = relationship("ApprovalTemplate", back_populates="booking_rules")
    versions = relationship(
        "BookingRuleVersion",
        back_populates="rule",
        cascade="all, delete-orphan",
        order_by="BookingRuleVersion.version.desc()",
    )
    bookings = relationship("BookingApplication", back_populates="rule")


class BookingRuleVersion(Base):
    __tablename__ = "booking_rule_versions"

    id = Column(Integer, primary_key=True, index=True)
    rule_id = Column(Integer, ForeignKey("booking_rules.id"), nullable=False)
    version = Column(Integer, nullable=False)
    advance_days = Column(Integer, nullable=False, default=7)
    max_duration_hours = Column(Float, nullable=False, default=4.0)
    min_duration_hours = Column(Float, nullable=False, default=1.0)
    allow_weekends = Column(Boolean, default=True)
    allow_holidays = Column(Boolean, default=False)
    extra_config = Column(JSON, default=dict)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("users.id"))

    __table_args__ = (UniqueConstraint("rule_id", "version", name="uq_rule_version"),)

    rule = relationship("BookingRule", back_populates="versions")


class BookingApplication(Base):
    __tablename__ = "booking_applications"

    id = Column(Integer, primary_key=True, index=True)
    rule_id = Column(Integer, ForeignKey("booking_rules.id"), nullable=False)
    rule_version = Column(Integer, nullable=False)
    title = Column(String(200), nullable=False)
    purpose = Column(Text)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)
    booking_date = Column(DateTime, nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    attendees = Column(Integer, default=0)
    contact_name = Column(String(100))
    contact_phone = Column(String(50))
    status = Column(Enum(BookingStatus), default=BookingStatus.PENDING, nullable=False)
    current_node_index = Column(Integer, default=0)
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    submitted_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    cancellation_reason = Column(Text)
    cancelled_at = Column(DateTime)
    cancelled_by = Column(Integer, ForeignKey("users.id"))

    rule = relationship("BookingRule", back_populates="bookings")
    submitter = relationship("User", foreign_keys=[submitted_by], back_populates="submitted_bookings")
    venue = relationship("Venue")
    approval_records = relationship(
        "ApprovalRecord",
        back_populates="booking",
        cascade="all, delete-orphan",
        order_by="ApprovalRecord.created_at",
    )


class ApprovalRecord(Base):
    __tablename__ = "approval_records"

    id = Column(Integer, primary_key=True, index=True)
    booking_id = Column(Integer, ForeignKey("booking_applications.id"), nullable=False)
    node_index = Column(Integer, nullable=False)
    node_name = Column(String(100), nullable=False)
    auditor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(Enum(ApprovalAction), nullable=False)
    comment = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    booking = relationship("BookingApplication", back_populates="approval_records")
    auditor = relationship("User", back_populates="approval_records")
