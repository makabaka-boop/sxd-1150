from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin_or_operator, require_all_authenticated
from app.models import (
    ApprovalRecord,
    ApprovalTemplate,
    BookingApplication,
    BookingChangeApplication,
    BookingChangeWorkflowSnapshot,
    BookingRule,
    BookingRuleVersion,
    BookingStatus,
    User,
    Venue,
)
from app.schemas import (
    ApprovalRecordResponse,
    BookingChangeCreate,
    BookingChangeFieldDiff,
    BookingChangeListResponse,
    BookingChangeOriginalInfo,
    BookingChangeResponse,
    BookingChangeTargetInfo,
    BookingChangeUpdate,
    MessageResponse,
)
from app.utils.availability import (
    check_same_day_booking,
    validate_change_availability,
)
from app.workflow import (
    serialize_template_nodes,
)

router = APIRouter(prefix="/api/booking-changes", tags=["预约变更申请"])


FIELD_LABELS = {
    "booking_date": "预约日期",
    "start_time": "开始时间",
    "end_time": "结束时间",
    "attendees": "参会人数",
    "purpose": "用途说明",
}


def _compute_field_diffs(
    original: BookingChangeOriginalInfo, target: BookingChangeTargetInfo
) -> list:
    diffs = []
    field_map = {
        "booking_date": (original.booking_date, target.booking_date),
        "start_time": (original.start_time, target.start_time),
        "end_time": (original.end_time, target.end_time),
        "attendees": (original.attendees, target.attendees),
        "purpose": (original.purpose, target.purpose),
    }
    for field, (orig, tgt) in field_map.items():
        if orig != tgt:
            diffs.append(
                BookingChangeFieldDiff(
                    field=field,
                    field_name=FIELD_LABELS.get(field, field),
                    original_value=orig,
                    target_value=tgt,
                )
            )
    return diffs


def _get_change_current_node_name(db: Session, change: BookingChangeApplication) -> str | None:
    from app.workflow import get_change_nodes

    nodes = get_change_nodes(db, change)
    if 0 <= change.current_node_index < len(nodes):
        return nodes[change.current_node_index].node_name
    return None


def _enrich_change_response(
    change: BookingChangeApplication, db: Session = None
) -> BookingChangeResponse:
    original = BookingChangeOriginalInfo(
        booking_date=change.original_booking_date,
        start_time=change.original_start_time,
        end_time=change.original_end_time,
        attendees=change.original_attendees,
        purpose=change.original_purpose,
    )
    target = BookingChangeTargetInfo(
        booking_date=change.target_booking_date,
        start_time=change.target_start_time,
        end_time=change.target_end_time,
        attendees=change.target_attendees,
        purpose=change.target_purpose,
    )

    resp = BookingChangeResponse(
        id=change.id,
        booking_id=change.booking_id,
        rule_id=change.rule_id,
        rule_version=change.rule_version,
        status=change.status,
        current_node_index=change.current_node_index,
        submitted_by=change.submitted_by,
        submitted_at=change.submitted_at,
        updated_at=change.updated_at,
        change_reason=change.change_reason,
        original=original,
        target=target,
        diff=_compute_field_diffs(original, target),
    )

    try:
        if change.submitter:
            resp.submitter_name = change.submitter.full_name or change.submitter.username
    except Exception:
        pass

    try:
        if change.booking and change.booking.venue:
            resp.venue_id = change.booking.venue_id
            resp.venue_name = change.booking.venue.name
    except Exception:
        pass

    try:
        if db is not None:
            node_name = _get_change_current_node_name(db, change)
            if node_name:
                resp.current_node_name = node_name
    except Exception:
        pass

    enriched_records = []
    for r in change.approval_records:
        try:
            r_resp = ApprovalRecordResponse.model_validate(r)
            r_resp.booking_id = None
            if r.auditor:
                r_resp.auditor_name = r.auditor.full_name or r.auditor.username
            enriched_records.append(r_resp)
        except Exception:
            continue
    resp.approval_records = enriched_records
    return resp


def _validate_change_against_rule(db, change_data, rule: BookingRule, version: int):
    rule_version = db.query(BookingRuleVersion).filter(
        BookingRuleVersion.rule_id == rule.id,
        BookingRuleVersion.version == version,
    ).first()
    if not rule_version:
        raise HTTPException(status_code=400, detail=f"规则版本 v{version} 不存在")

    start = change_data.target_start_time
    end = change_data.target_end_time
    booking_date = change_data.target_booking_date

    if start and end:
        duration_hours = (end - start).total_seconds() / 3600
        if duration_hours < rule_version.min_duration_hours:
            raise HTTPException(
                status_code=400,
                detail=f"变更后时长 {duration_hours:.1f} 小时小于规则要求的最小时长 {rule_version.min_duration_hours} 小时",
            )
        if duration_hours > rule_version.max_duration_hours:
            raise HTTPException(
                status_code=400,
                detail=f"变更后时长 {duration_hours:.1f} 小时超过规则允许的最大时长 {rule_version.max_duration_hours} 小时",
            )

    if booking_date:
        advance_days = (booking_date.date() - datetime.utcnow().date()).days
        if advance_days < 0:
            raise HTTPException(status_code=400, detail="不能变更为过去的日期")
        if advance_days > rule_version.advance_days:
            raise HTTPException(
                status_code=400,
                detail=f"只能提前 {rule_version.advance_days} 天预约，当前提前 {advance_days} 天",
            )
        if not rule_version.allow_weekends:
            if booking_date.weekday() >= 5:
                raise HTTPException(status_code=400, detail="该规则不允许周末预约")


@router.post("", response_model=BookingChangeResponse, status_code=status.HTTP_201_CREATED)
def create_booking_change(
    change_in: BookingChangeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_operator),
):
    booking = db.query(BookingApplication).filter(
        BookingApplication.id == change_in.booking_id
    ).first()
    if not booking:
        raise HTTPException(status_code=404, detail="原预约申请不存在")

    if booking.submitted_by != current_user.id:
        raise HTTPException(status_code=403, detail="只能变更自己提交的预约申请")

    if booking.status not in [BookingStatus.PENDING, BookingStatus.APPROVING, BookingStatus.APPROVED]:
        raise HTTPException(
            status_code=400,
            detail=f"原预约状态为 {booking.status.value}，不允许发起变更。仅待审批、审批中、已通过状态可变更",
        )

    if booking.status == BookingStatus.APPROVED:
        if booking.booking_date.date() < datetime.utcnow().date():
            raise HTTPException(status_code=400, detail="已过期的预约不允许变更")

    pending_change = db.query(BookingChangeApplication).filter(
        BookingChangeApplication.booking_id == booking.id,
        BookingChangeApplication.status.in_([BookingStatus.PENDING, BookingStatus.APPROVING]),
    ).first()
    if pending_change:
        raise HTTPException(
            status_code=400,
            detail=f"该预约已存在进行中的变更申请（ID: {pending_change.id}），请等待审批完成或终止后再提交新的变更",
        )

    rule = db.query(BookingRule).filter(
        BookingRule.id == booking.rule_id,
        BookingRule.is_deleted == False,
        BookingRule.is_active == True,
    ).first()
    if not rule:
        raise HTTPException(status_code=400, detail="原预约关联规则不存在或已禁用/删除")

    venue = db.query(Venue).filter(
        Venue.id == rule.venue_id,
        Venue.is_deleted == False,
        Venue.is_active == True,
    ).first()
    if not venue:
        raise HTTPException(status_code=400, detail="关联场地不可用")

    target_booking_date = change_in.target_booking_date.date() if hasattr(change_in.target_booking_date, 'date') else change_in.target_booking_date
    same_day_reason = check_same_day_booking(target_booking_date, change_in.target_start_time, change_in.target_end_time)
    if same_day_reason:
        raise HTTPException(status_code=400, detail=same_day_reason)

    current_version = rule.current_version
    _validate_change_against_rule(db, change_in, rule, current_version)

    validate_change_availability(
        db=db,
        venue=venue,
        rule=rule,
        rule_version=current_version,
        booking_date=target_booking_date,
        start_time=change_in.target_start_time,
        end_time=change_in.target_end_time,
        original_booking_id=booking.id,
        exclude_change_id=None,
    )

    template = db.query(ApprovalTemplate).filter(
        ApprovalTemplate.id == rule.approval_template_id,
        ApprovalTemplate.is_deleted == False,
    ).first()
    if not template:
        raise HTTPException(status_code=400, detail="关联审批模板不存在或已删除")

    change = BookingChangeApplication(
        booking_id=booking.id,
        rule_id=booking.rule_id,
        rule_version=current_version,
        original_booking_date=booking.booking_date,
        original_start_time=booking.start_time,
        original_end_time=booking.end_time,
        original_attendees=booking.attendees,
        original_purpose=booking.purpose,
        target_booking_date=change_in.target_booking_date,
        target_start_time=change_in.target_start_time,
        target_end_time=change_in.target_end_time,
        target_attendees=change_in.target_attendees,
        target_purpose=change_in.target_purpose,
        change_reason=change_in.change_reason,
        status=BookingStatus.PENDING,
        current_node_index=0,
        submitted_by=current_user.id,
    )
    db.add(change)
    db.flush()

    snapshot = BookingChangeWorkflowSnapshot(
        change_id=change.id,
        template_id=template.id,
        template_name=template.name,
        venue_type=template.venue_type,
        nodes_json=serialize_template_nodes(template),
    )
    db.add(snapshot)

    db.commit()
    db.refresh(change)
    return _enrich_change_response(change, db)


@router.get("", response_model=BookingChangeListResponse)
def list_booking_changes(
    skip: int = 0,
    limit: int = 100,
    status: BookingStatus = None,
    booking_id: int = Query(None, description="按原预约ID筛选"),
    venue_id: int = Query(None, description="按场馆ID筛选"),
    submitted_by: int = Query(None, description="按申请人ID筛选"),
    my_only: bool = Query(False, description="仅查看我提交的变更申请"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_all_authenticated),
):
    query = db.query(BookingChangeApplication)
    if my_only:
        query = query.filter(BookingChangeApplication.submitted_by == current_user.id)
    if status:
        query = query.filter(BookingChangeApplication.status == status)
    if booking_id:
        query = query.filter(BookingChangeApplication.booking_id == booking_id)
    if submitted_by:
        query = query.filter(BookingChangeApplication.submitted_by == submitted_by)
    if venue_id:
        query = query.join(
            BookingApplication,
            BookingChangeApplication.booking_id == BookingApplication.id,
        ).filter(BookingApplication.venue_id == venue_id)

    query = query.order_by(BookingChangeApplication.submitted_at.desc())
    total = query.count()
    changes = query.offset(skip).limit(limit).all()
    enriched = [_enrich_change_response(c, db) for c in changes]
    return BookingChangeListResponse(items=enriched, total=total)


@router.get("/{change_id}", response_model=BookingChangeResponse)
def get_booking_change(
    change_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_all_authenticated),
):
    change = db.query(BookingChangeApplication).filter(
        BookingChangeApplication.id == change_id
    ).first()
    if not change:
        raise HTTPException(status_code=404, detail="预约变更申请不存在")
    return _enrich_change_response(change, db)


@router.put("/{change_id}", response_model=BookingChangeResponse)
def update_booking_change(
    change_id: int,
    change_in: BookingChangeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_operator),
):
    change = db.query(BookingChangeApplication).filter(
        BookingChangeApplication.id == change_id
    ).first()
    if not change:
        raise HTTPException(status_code=404, detail="预约变更申请不存在")

    if change.submitted_by != current_user.id:
        raise HTTPException(status_code=403, detail="只能修改自己提交的变更申请")

    if change.status not in [BookingStatus.PENDING, BookingStatus.RETURNED]:
        raise HTTPException(
            status_code=400,
            detail=f"当前状态为 {change.status.value}，不允许修改。仅待审批(PENDING)或已退回(RETURNED)状态可修改",
        )

    if change.status == BookingStatus.PENDING and change.current_node_index > 0:
        raise HTTPException(
            status_code=400,
            detail="变更申请已进入审批流程，无法修改。请等待审批完成或退回后再修改",
        )

    update_data = change_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(change, field, value)

    target_booking_date = change.target_booking_date.date() if hasattr(change.target_booking_date, 'date') else change.target_booking_date
    same_day_reason = check_same_day_booking(target_booking_date, change.target_start_time, change.target_end_time)
    if same_day_reason:
        raise HTTPException(status_code=400, detail=same_day_reason)

    rule = db.query(BookingRule).filter(BookingRule.id == change.rule_id).first()
    if rule:
        _validate_change_against_rule(db, change, rule, change.rule_version)

    booking = db.query(BookingApplication).filter(BookingApplication.id == change.booking_id).first()
    venue = db.query(Venue).filter(Venue.id == rule.venue_id).first() if rule else None
    if venue and rule and booking:
        validate_change_availability(
            db=db,
            venue=venue,
            rule=rule,
            rule_version=change.rule_version,
            booking_date=target_booking_date,
            start_time=change.target_start_time,
            end_time=change.target_end_time,
            original_booking_id=booking.id,
            exclude_change_id=change.id,
        )

    if change.status == BookingStatus.RETURNED:
        change.status = BookingStatus.PENDING
        change.current_node_index = 0
        db.query(ApprovalRecord).filter(
            ApprovalRecord.change_id == change.id,
            ApprovalRecord.change_type == "change",
        ).delete()

        if rule:
            template = db.query(ApprovalTemplate).filter(
                ApprovalTemplate.id == rule.approval_template_id,
                ApprovalTemplate.is_deleted == False,
            ).first()
            if template:
                db.query(BookingChangeWorkflowSnapshot).filter(
                    BookingChangeWorkflowSnapshot.change_id == change.id
                ).delete()
                new_snapshot = BookingChangeWorkflowSnapshot(
                    change_id=change.id,
                    template_id=template.id,
                    template_name=template.name,
                    venue_type=template.venue_type,
                    nodes_json=serialize_template_nodes(template),
                )
                db.add(new_snapshot)

    db.commit()
    db.refresh(change)
    return _enrich_change_response(change, db)
