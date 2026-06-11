from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin_or_auditor, require_all_authenticated
from app.models import (
    ApprovalAction,
    ApprovalNode,
    ApprovalRecord,
    ApprovalTemplate,
    BookingApplication,
    BookingRule,
    BookingStatus,
    TimeoutStrategy,
    User,
    UserRole,
)
from app.schemas import (
    ApprovalActionRequest,
    ApprovalRecordResponse,
    BookingApplicationListResponse,
    BookingApplicationResponse,
    MessageResponse,
)
from app.workflow import (
    get_booking_current_node,
    get_booking_nodes,
    has_booking_finished_all_nodes,
)

router = APIRouter(prefix="/api/approvals", tags=["审批工作流"])


def _enrich_booking(
    booking: BookingApplication, db: Session = None
) -> BookingApplicationResponse:
    from app.routers.bookings import _enrich_booking_response
    try:
        return _enrich_booking_response(booking, db)
    except Exception:
        return _enrich_booking_response(booking, None)


@router.get("/pending", response_model=BookingApplicationListResponse)
def list_pending_approvals(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_auditor),
):
    pending_statuses = [BookingStatus.PENDING, BookingStatus.APPROVING]
    query = db.query(BookingApplication).filter(
        BookingApplication.status.in_(pending_statuses)
    )
    query = query.order_by(BookingApplication.submitted_at.asc())
    total = query.count()
    bookings = query.offset(skip).limit(limit).all()
    enriched = [_enrich_booking(b, db) for b in bookings]
    return BookingApplicationListResponse(items=enriched, total=total)


@router.get("/{booking_id}/workflow", response_model=BookingApplicationResponse)
def get_approval_workflow(
    booking_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_all_authenticated),
):
    booking = db.query(BookingApplication).filter(BookingApplication.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="预约申请不存在")
    return _enrich_booking(booking, db)


@router.get("/{booking_id}/records", response_model=List[ApprovalRecordResponse])
def list_approval_records(
    booking_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_all_authenticated),
):
    booking = db.query(BookingApplication).filter(BookingApplication.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="预约申请不存在")

    records = db.query(ApprovalRecord).filter(
        ApprovalRecord.booking_id == booking_id
    ).order_by(ApprovalRecord.created_at.asc()).all()

    result = []
    for r in records:
        r_resp = ApprovalRecordResponse.model_validate(r)
        if r.auditor:
            r_resp.auditor_name = r.auditor.full_name or r.auditor.username
        result.append(r_resp)
    return result


@router.post("/{booking_id}/action", response_model=BookingApplicationResponse)
def perform_approval_action(
    booking_id: int,
    action_req: ApprovalActionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_auditor),
):
    booking = db.query(BookingApplication).filter(BookingApplication.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="预约申请不存在")

    if booking.status not in [BookingStatus.PENDING, BookingStatus.APPROVING]:
        raise HTTPException(
            status_code=400,
            detail=f"当前状态为 {booking.status.value}，无法执行审批操作",
        )

    nodes = get_booking_nodes(db, booking)
    total_nodes = len(nodes)
    if total_nodes == 0:
        raise HTTPException(status_code=400, detail="该预约未配置审批流程")

    if has_booking_finished_all_nodes(db, booking):
        booking.status = BookingStatus.APPROVED
        db.commit()
        db.refresh(booking)
        return _enrich_booking(booking, db)

    if booking.current_node_index >= total_nodes:
        if booking.workflow_snapshot:
            raise HTTPException(
                status_code=400,
                detail=f"该预约快照共 {total_nodes} 个节点，当前节点索引 {booking.current_node_index} 超出范围，请检查数据完整性",
            )
        else:
            booking.status = BookingStatus.APPROVED
            db.commit()
            db.refresh(booking)
            return _enrich_booking(booking, db)

    current_node = get_booking_current_node(db, booking)
    if not current_node:
        raise HTTPException(status_code=400, detail="无法定位当前审批节点")

    if current_node.auditor_role == UserRole.ADMIN and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="该节点需要管理员角色审批")
    if current_node.auditor_role == UserRole.AUDITOR and current_user.role not in [UserRole.AUDITOR, UserRole.ADMIN]:
        raise HTTPException(status_code=403, detail="该节点需要审核员角色审批")

    record = ApprovalRecord(
        booking_id=booking.id,
        node_index=booking.current_node_index,
        node_name=current_node.node_name,
        auditor_id=current_user.id,
        action=action_req.action,
        comment=action_req.comment,
    )
    db.add(record)

    if action_req.action == ApprovalAction.AGREE:
        next_index = booking.current_node_index + 1
        if next_index >= total_nodes:
            booking.status = BookingStatus.APPROVED
            booking.current_node_index = next_index
        else:
            booking.status = BookingStatus.APPROVING
            booking.current_node_index = next_index

    elif action_req.action == ApprovalAction.RETURN:
        booking.status = BookingStatus.RETURNED

    elif action_req.action == ApprovalAction.TERMINATE:
        booking.status = BookingStatus.TERMINATED

    db.commit()
    db.refresh(booking)
    return _enrich_booking(booking, db)


@router.post("/{booking_id}/timeout-check", response_model=MessageResponse)
def check_and_handle_timeout(
    booking_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_or_auditor),
):
    booking = db.query(BookingApplication).filter(BookingApplication.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="预约申请不存在")

    if booking.status not in [BookingStatus.PENDING, BookingStatus.APPROVING]:
        return MessageResponse(message=f"当前状态 {booking.status.value} 无需超时检查")

    nodes = get_booking_nodes(db, booking)
    total_nodes = len(nodes)
    if total_nodes == 0 or booking.current_node_index >= total_nodes:
        return MessageResponse(message="无待处理节点")

    current_node = get_booking_current_node(db, booking)
    if not current_node:
        return MessageResponse(message="无法定位当前审批节点")

    last_record = db.query(ApprovalRecord).filter(
        ApprovalRecord.booking_id == booking_id
    ).order_by(ApprovalRecord.created_at.desc()).first()

    start_time = last_record.created_at if last_record else booking.submitted_at
    elapsed_minutes = (datetime.utcnow() - start_time).total_seconds() / 60
    timeout_minutes = current_node.timeout_minutes

    if elapsed_minutes < timeout_minutes:
        return MessageResponse(
            message=f"节点 '{current_node.node_name}' 已等待 {elapsed_minutes:.0f} 分钟，未超时（阈值 {timeout_minutes} 分钟）"
        )

    strategy = current_node.timeout_strategy

    if strategy == TimeoutStrategy.REMIND:
        return MessageResponse(
            message=f"[超时提醒] 节点 '{current_node.node_name}' 已超时 {elapsed_minutes - timeout_minutes:.0f} 分钟，请尽快处理"
        )

    elif strategy == TimeoutStrategy.TRANSFER:
        if not current_node.transfer_to_user_id:
            return MessageResponse(
                message=f"[超时转交失败] 节点未指定转交人，请人工处理"
            )
        transfer_user = db.query(User).filter(
            User.id == current_node.transfer_to_user_id, User.is_active == True
        ).first()
        if not transfer_user:
            return MessageResponse(
                message=f"[超时转交失败] 指定转交用户不存在或已禁用，请人工处理"
            )
        record = ApprovalRecord(
            booking_id=booking.id,
            node_index=booking.current_node_index,
            node_name=current_node.node_name,
            auditor_id=current_user.id,
            action=ApprovalAction.AGREE,
            comment=f"[系统超时转交] 自动转交至 {transfer_user.full_name or transfer_user.username}，由系统代签通过当前节点",
        )
        db.add(record)
        next_index = booking.current_node_index + 1
        if next_index >= total_nodes:
            booking.status = BookingStatus.APPROVED
        else:
            booking.current_node_index = next_index
        db.commit()
        return MessageResponse(
            message=f"[超时转交成功] 节点 '{current_node.node_name}' 已自动转交并通过，转交人：{transfer_user.full_name or transfer_user.username}"
        )

    elif strategy == TimeoutStrategy.REJECT:
        record = ApprovalRecord(
            booking_id=booking.id,
            node_index=booking.current_node_index,
            node_name=current_node.node_name,
            auditor_id=current_user.id,
            action=ApprovalAction.TERMINATE,
            comment=f"[系统超时驳回] 节点 '{current_node.node_name}' 审批超时，自动终止申请",
        )
        db.add(record)
        booking.status = BookingStatus.TERMINATED
        db.commit()
        return MessageResponse(
            message=f"[超时驳回] 节点 '{current_node.node_name}' 审批超时，申请已自动终止"
        )

    return MessageResponse(message="未知超时策略")
