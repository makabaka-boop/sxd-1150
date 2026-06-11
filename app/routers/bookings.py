from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin_or_operator, require_all_authenticated
from app.models import (
    ApprovalNode,
    ApprovalRecord,
    BookingApplication,
    BookingRule,
    BookingRuleVersion,
    BookingStatus,
    User,
    Venue,
)
from app.schemas import (
    ApprovalRecordResponse,
    BookingApplicationCreate,
    BookingApplicationListResponse,
    BookingApplicationResponse,
    BookingApplicationUpdate,
    BookingCancelRequest,
    MessageResponse,
)

router = APIRouter(prefix="/api/bookings", tags=["预约申请管理"])


def _enrich_booking_response(booking: BookingApplication) -> BookingApplicationResponse:
    resp = BookingApplicationResponse.model_validate(booking)
    if booking.venue:
        resp.venue_name = booking.venue.name
    if booking.submitter:
        resp.submitter_name = booking.submitter.full_name or booking.submitter.username

    template = None
    if booking.rule and booking.rule.approval_template:
        template = booking.rule.approval_template

    if template:
        sorted_nodes = sorted(template.nodes, key=lambda n: n.order_index)
        if 0 <= booking.current_node_index < len(sorted_nodes):
            resp.current_node_name = sorted_nodes[booking.current_node_index].node_name

    enriched_records = []
    for r in booking.approval_records:
        r_resp = ApprovalRecordResponse.model_validate(r)
        if r.auditor:
            r_resp.auditor_name = r.auditor.full_name or r.auditor.username
        enriched_records.append(r_resp)
    resp.approval_records = enriched_records
    return resp


def _validate_booking_against_rule(db, booking_data, rule: BookingRule, version: int):
    rule_version = db.query(BookingRuleVersion).filter(
        BookingRuleVersion.rule_id == rule.id,
        BookingRuleVersion.version == version,
    ).first()
    if not rule_version:
        raise HTTPException(status_code=400, detail=f"规则版本 v{version} 不存在")

    start = booking_data.start_time if hasattr(booking_data, "start_time") and booking_data.start_time else None
    end = booking_data.end_time if hasattr(booking_data, "end_time") and booking_data.end_time else None
    booking_date = booking_data.booking_date if hasattr(booking_data, "booking_date") and booking_data.booking_date else None

    if start and end:
        duration_hours = (end - start).total_seconds() / 3600
        if duration_hours < rule_version.min_duration_hours:
            raise HTTPException(
                status_code=400,
                detail=f"预约时长 {duration_hours:.1f} 小时小于规则要求的最小时长 {rule_version.min_duration_hours} 小时",
            )
        if duration_hours > rule_version.max_duration_hours:
            raise HTTPException(
                status_code=400,
                detail=f"预约时长 {duration_hours:.1f} 小时超过规则允许的最大时长 {rule_version.max_duration_hours} 小时",
            )

    if booking_date:
        advance_days = (booking_date.date() - datetime.utcnow().date()).days
        if advance_days < 0:
            raise HTTPException(status_code=400, detail="不能预约过去的日期")
        if advance_days > rule_version.advance_days:
            raise HTTPException(
                status_code=400,
                detail=f"只能提前 {rule_version.advance_days} 天预约，当前提前 {advance_days} 天",
            )
        if not rule_version.allow_weekends:
            if booking_date.weekday() >= 5:
                raise HTTPException(status_code=400, detail="该规则不允许周末预约")


@router.post("", response_model=BookingApplicationResponse, status_code=status.HTTP_201_CREATED)
def create_booking(
    booking_in: BookingApplicationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_operator),
):
    rule = db.query(BookingRule).filter(
        BookingRule.id == booking_in.rule_id,
        BookingRule.is_deleted == False,
        BookingRule.is_active == True,
    ).first()
    if not rule:
        raise HTTPException(status_code=400, detail="预约规则不存在或已禁用/删除")

    venue = db.query(Venue).filter(
        Venue.id == rule.venue_id,
        Venue.is_deleted == False,
        Venue.is_active == True,
    ).first()
    if not venue:
        raise HTTPException(status_code=400, detail="关联场地不可用")

    current_version = rule.current_version
    _validate_booking_against_rule(db, booking_in, rule, current_version)

    if booking_in.start_time >= booking_in.end_time:
        raise HTTPException(status_code=400, detail="结束时间必须晚于开始时间")

    booking = BookingApplication(
        rule_id=booking_in.rule_id,
        rule_version=current_version,
        title=booking_in.title,
        purpose=booking_in.purpose,
        venue_id=rule.venue_id,
        booking_date=booking_in.booking_date,
        start_time=booking_in.start_time,
        end_time=booking_in.end_time,
        attendees=booking_in.attendees,
        contact_name=booking_in.contact_name,
        contact_phone=booking_in.contact_phone,
        status=BookingStatus.PENDING,
        current_node_index=0,
        submitted_by=current_user.id,
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)
    return _enrich_booking_response(booking)


@router.get("", response_model=BookingApplicationListResponse)
def list_bookings(
    skip: int = 0,
    limit: int = 100,
    status: BookingStatus = None,
    venue_id: int = None,
    my_only: bool = Query(False, description="仅查看我提交的申请"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_all_authenticated),
):
    query = db.query(BookingApplication)
    if my_only:
        query = query.filter(BookingApplication.submitted_by == current_user.id)
    if status:
        query = query.filter(BookingApplication.status == status)
    if venue_id:
        query = query.filter(BookingApplication.venue_id == venue_id)
    query = query.order_by(BookingApplication.created_at.desc())
    total = query.count()
    bookings = query.offset(skip).limit(limit).all()
    enriched = [_enrich_booking_response(b) for b in bookings]
    return BookingApplicationListResponse(items=enriched, total=total)


@router.get("/{booking_id}", response_model=BookingApplicationResponse)
def get_booking(
    booking_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_all_authenticated),
):
    booking = db.query(BookingApplication).filter(BookingApplication.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="预约申请不存在")
    return _enrich_booking_response(booking)


@router.put("/{booking_id}", response_model=BookingApplicationResponse)
def update_booking(
    booking_id: int,
    booking_in: BookingApplicationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_operator),
):
    booking = db.query(BookingApplication).filter(BookingApplication.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="预约申请不存在")

    if booking.submitted_by != current_user.id:
        raise HTTPException(status_code=403, detail="只能修改自己提交的预约申请")

    if booking.status not in [BookingStatus.PENDING, BookingStatus.RETURNED]:
        raise HTTPException(
            status_code=400,
            detail=f"当前状态为 {booking.status.value}，不允许修改。仅待审批(PENDING)或已退回(RETURNED)状态可修改",
        )

    if booking.status == BookingStatus.PENDING and booking.current_node_index > 0:
        raise HTTPException(
            status_code=400,
            detail="申请已进入审批流程，无法修改。请等待审批完成或退回后再修改",
        )

    update_data = booking_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(booking, field, value)

    rule = db.query(BookingRule).filter(BookingRule.id == booking.rule_id).first()
    if rule:
        _validate_booking_against_rule(db, booking, rule, booking.rule_version)

    if booking.start_time >= booking.end_time:
        raise HTTPException(status_code=400, detail="结束时间必须晚于开始时间")

    if booking.status == BookingStatus.RETURNED:
        booking.status = BookingStatus.PENDING
        booking.current_node_index = 0
        db.query(ApprovalRecord).filter(ApprovalRecord.booking_id == booking.id).delete()

    db.commit()
    db.refresh(booking)
    return _enrich_booking_response(booking)


@router.post("/{booking_id}/cancel", response_model=MessageResponse)
def cancel_booking(
    booking_id: int,
    cancel_req: BookingCancelRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_operator),
):
    booking = db.query(BookingApplication).filter(BookingApplication.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="预约申请不存在")

    if booking.submitted_by != current_user.id:
        raise HTTPException(status_code=403, detail="只能取消自己提交的预约申请")

    if booking.status in [BookingStatus.CANCELLED, BookingStatus.TERMINATED, BookingStatus.REJECTED]:
        raise HTTPException(
            status_code=400,
            detail=f"当前状态为 {booking.status.value}，无需重复取消",
        )

    booking.status = BookingStatus.CANCELLED
    booking.cancellation_reason = cancel_req.reason
    booking.cancelled_at = datetime.utcnow()
    booking.cancelled_by = current_user.id
    db.commit()
    return MessageResponse(message=f"预约已取消，原因：{cancel_req.reason}")
