from datetime import date, datetime, time
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import (
    ApprovalAction,
    BookingStatus,
    DeleteType,
    TimeoutStrategy,
    UserRole,
    Weekday,
)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    username: Optional[str] = None


class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    role: UserRole


class UserCreate(UserBase):
    password: str = Field(..., min_length=6)


class UserLogin(BaseModel):
    username: str
    password: str


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=6)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    role: UserRole
    is_active: bool
    created_at: datetime


class UserListResponse(BaseModel):
    items: List[UserResponse]
    total: int


class VenueBase(BaseModel):
    name: str = Field(..., max_length=100)
    venue_type: str = Field(..., max_length=50)
    description: Optional[str] = None
    location: Optional[str] = None
    capacity: Optional[int] = Field(None, ge=0)


class VenueCreate(VenueBase):
    pass


class VenueUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    venue_type: Optional[str] = Field(None, max_length=50)
    description: Optional[str] = None
    location: Optional[str] = None
    capacity: Optional[int] = Field(None, ge=0)
    is_active: Optional[bool] = None


class VenueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    venue_type: str
    description: Optional[str] = None
    location: Optional[str] = None
    capacity: Optional[int] = None
    is_active: bool
    is_deleted: bool
    created_at: datetime


class VenueListResponse(BaseModel):
    items: List[VenueResponse]
    total: int


class VenueAvailableSlotCreate(BaseModel):
    weekday: Weekday
    start_time: time
    end_time: time
    is_active: bool = True

    @field_validator("end_time")
    @classmethod
    def check_time_range(cls, v, info):
        start = info.data.get("start_time")
        if start is not None and v <= start:
            raise ValueError("结束时间必须晚于开始时间")
        return v


class VenueAvailableSlotUpdate(BaseModel):
    weekday: Optional[Weekday] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    is_active: Optional[bool] = None

    @field_validator("end_time")
    @classmethod
    def check_time_range(cls, v, info):
        start = info.data.get("start_time")
        if start is not None and v is not None and v <= start:
            raise ValueError("结束时间必须晚于开始时间")
        return v


class VenueAvailableSlotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    venue_id: int
    weekday: Weekday
    start_time: time
    end_time: time
    is_active: bool
    created_at: datetime


class VenueAvailableSlotListResponse(BaseModel):
    items: List[VenueAvailableSlotResponse]
    total: int


class VenueUnavailablePeriodCreate(BaseModel):
    start_date: date
    end_date: date
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    reason: str = Field(..., min_length=1, max_length=200)
    is_active: bool = True

    @field_validator("end_date")
    @classmethod
    def check_date_range(cls, v, info):
        start = info.data.get("start_date")
        if start is not None and v < start:
            raise ValueError("结束日期不能早于开始日期")
        return v

    @field_validator("end_time")
    @classmethod
    def check_time_range(cls, v, info):
        start = info.data.get("start_time")
        if start is not None and v is not None and v <= start:
            raise ValueError("结束时间必须晚于开始时间")
        if (start is None) != (v is None):
            raise ValueError("开始时间和结束时间必须同时提供或同时为空")
        return v


class VenueUnavailablePeriodUpdate(BaseModel):
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    reason: Optional[str] = Field(None, min_length=1, max_length=200)
    is_active: Optional[bool] = None


class VenueUnavailablePeriodResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    venue_id: int
    start_date: date
    end_date: date
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    reason: str
    is_active: bool
    created_at: datetime


class VenueUnavailablePeriodListResponse(BaseModel):
    items: List[VenueUnavailablePeriodResponse]
    total: int


class AvailableTimeSlotResponse(BaseModel):
    start_time: datetime
    end_time: datetime
    available: bool
    unavailable_reason: Optional[str] = None


class VenueAvailableTimeSlotsResponse(BaseModel):
    venue_id: int
    date: date
    slots: List[AvailableTimeSlotResponse]


class ApprovalNodeBase(BaseModel):
    node_name: str = Field(..., max_length=100)
    order_index: int = Field(..., ge=0)
    auditor_role: UserRole = UserRole.AUDITOR
    timeout_minutes: int = Field(1440, ge=1)
    timeout_strategy: TimeoutStrategy = TimeoutStrategy.REMIND
    transfer_to_user_id: Optional[int] = None


class ApprovalNodeCreate(ApprovalNodeBase):
    pass


class ApprovalNodeUpdate(BaseModel):
    node_name: Optional[str] = Field(None, max_length=100)
    order_index: Optional[int] = Field(None, ge=0)
    auditor_role: Optional[UserRole] = None
    timeout_minutes: Optional[int] = Field(None, ge=1)
    timeout_strategy: Optional[TimeoutStrategy] = None
    transfer_to_user_id: Optional[int] = None


class ApprovalNodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    template_id: int
    node_name: str
    order_index: int
    auditor_role: UserRole
    timeout_minutes: int
    timeout_strategy: TimeoutStrategy
    transfer_to_user_id: Optional[int] = None


class ApprovalTemplateBase(BaseModel):
    name: str = Field(..., max_length=100)
    description: Optional[str] = None
    venue_type: str = Field(..., max_length=50)


class ApprovalTemplateCreate(ApprovalTemplateBase):
    nodes: List[ApprovalNodeCreate] = Field(..., min_length=1)


class ApprovalTemplateUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    venue_type: Optional[str] = Field(None, max_length=50)
    is_active: Optional[bool] = None
    nodes: Optional[List[ApprovalNodeCreate]] = None


class ApprovalTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str] = None
    venue_type: str
    is_active: bool
    is_deleted: bool
    created_at: datetime
    nodes: List[ApprovalNodeResponse] = []


class ApprovalTemplateListResponse(BaseModel):
    items: List[ApprovalTemplateResponse]
    total: int


class BookingRuleVersionBase(BaseModel):
    advance_days: int = Field(7, ge=0)
    max_duration_hours: float = Field(4.0, gt=0)
    min_duration_hours: float = Field(1.0, gt=0)
    allow_weekends: bool = True
    allow_holidays: bool = False
    extra_config: Optional[Dict[str, Any]] = None
    description: Optional[str] = None

    @field_validator("min_duration_hours")
    @classmethod
    def check_min_duration(cls, v, info):
        max_dur = info.data.get("max_duration_hours")
        if max_dur is not None and v > max_dur:
            raise ValueError("min_duration_hours 不能大于 max_duration_hours")
        return v


class BookingRuleVersionCreate(BookingRuleVersionBase):
    pass


class BookingRuleVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_id: int
    version: int
    advance_days: int
    max_duration_hours: float
    min_duration_hours: float
    allow_weekends: bool
    allow_holidays: bool
    extra_config: Optional[Dict[str, Any]] = None
    description: Optional[str] = None
    created_at: datetime
    created_by: Optional[int] = None


class BookingRuleBase(BaseModel):
    venue_id: int
    approval_template_id: int


class BookingRuleCreate(BookingRuleBase):
    rule_config: BookingRuleVersionCreate


class BookingRuleUpdate(BaseModel):
    approval_template_id: Optional[int] = None
    is_active: Optional[bool] = None
    rule_config: Optional[BookingRuleVersionCreate] = None


class BookingRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    venue_id: int
    approval_template_id: int
    current_version: int
    is_active: bool
    is_deleted: bool
    created_at: datetime
    current_version_data: Optional[BookingRuleVersionResponse] = None
    versions: List[BookingRuleVersionResponse] = []


class BookingRuleListResponse(BaseModel):
    items: List[BookingRuleResponse]
    total: int


class DeleteCheckResponse(BaseModel):
    can_delete: bool
    delete_type: DeleteType
    message: str
    dependency_count: int = 0


class DeleteResultResponse(BaseModel):
    success: bool
    delete_type: DeleteType
    message: str


class BookingApplicationBase(BaseModel):
    rule_id: int
    title: str = Field(..., max_length=200)
    purpose: Optional[str] = None
    booking_date: datetime
    start_time: datetime
    end_time: datetime
    attendees: int = Field(0, ge=0)
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None


class BookingApplicationCreate(BookingApplicationBase):
    pass


class BookingApplicationUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    purpose: Optional[str] = None
    booking_date: Optional[datetime] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    attendees: Optional[int] = Field(None, ge=0)
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None


class BookingCancelRequest(BaseModel):
    reason: str = Field(..., min_length=1)


class ApprovalRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_id: int
    node_index: int
    node_name: str
    auditor_id: int
    auditor_name: Optional[str] = None
    action: ApprovalAction
    comment: Optional[str] = None
    created_at: datetime


class BookingApplicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_id: int
    rule_version: int
    title: str
    purpose: Optional[str] = None
    venue_id: int
    venue_name: Optional[str] = None
    booking_date: datetime
    start_time: datetime
    end_time: datetime
    attendees: int
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    status: BookingStatus
    current_node_index: int
    current_node_name: Optional[str] = None
    submitted_by: int
    submitter_name: Optional[str] = None
    submitted_at: datetime
    updated_at: datetime
    cancellation_reason: Optional[str] = None
    cancelled_at: Optional[datetime] = None
    approval_records: List[ApprovalRecordResponse] = []
    conflict_status: Optional[str] = None
    unavailability_reason: Optional[str] = None


class BookingApplicationListResponse(BaseModel):
    items: List[BookingApplicationResponse]
    total: int


class ApprovalActionRequest(BaseModel):
    action: ApprovalAction
    comment: Optional[str] = None

    @field_validator("comment")
    @classmethod
    def return_requires_comment(cls, v, info):
        if info.data.get("action") == ApprovalAction.RETURN and (not v or len(v.strip()) == 0):
            raise ValueError("退回操作必须填写修改意见")
        return v


class MessageResponse(BaseModel):
    message: str
