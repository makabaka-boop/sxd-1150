from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin, require_all_authenticated
from app.models import (
    ApprovalNode,
    ApprovalTemplate,
    BookingApplication,
    BookingRule,
    TimeoutStrategy,
    User,
)
from app.schemas import (
    ApprovalTemplateCreate,
    ApprovalTemplateListResponse,
    ApprovalTemplateResponse,
    ApprovalTemplateUpdate,
    DeleteCheckResponse,
    DeleteResultResponse,
    DeleteType,
)

router = APIRouter(prefix="/api/templates", tags=["审批模板管理"])


def _check_node_orders(nodes_data):
    orders = [n.order_index for n in nodes_data]
    if len(orders) != len(set(orders)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="节点 order_index 不能重复",
        )
    for node in nodes_data:
        if node.timeout_strategy == TimeoutStrategy.TRANSFER and not node.transfer_to_user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"节点 '{node.node_name}' 超时策略为转交(TRANSFER)时，必须指定 transfer_to_user_id",
            )


@router.post("", response_model=ApprovalTemplateResponse, status_code=status.HTTP_201_CREATED)
def create_template(
    template_in: ApprovalTemplateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    _check_node_orders(template_in.nodes)

    template = ApprovalTemplate(
        name=template_in.name,
        description=template_in.description,
        venue_type=template_in.venue_type,
    )
    db.add(template)
    db.flush()

    for node_data in template_in.nodes:
        node = ApprovalNode(
            template_id=template.id,
            node_name=node_data.node_name,
            order_index=node_data.order_index,
            auditor_role=node_data.auditor_role,
            timeout_minutes=node_data.timeout_minutes,
            timeout_strategy=node_data.timeout_strategy,
            transfer_to_user_id=node_data.transfer_to_user_id,
        )
        db.add(node)

    db.commit()
    db.refresh(template)
    return template


@router.get("", response_model=ApprovalTemplateListResponse)
def list_templates(
    skip: int = 0,
    limit: int = 100,
    venue_type: str = None,
    include_deleted: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_all_authenticated),
):
    query = db.query(ApprovalTemplate)
    if not include_deleted:
        query = query.filter(ApprovalTemplate.is_deleted == False)
    if venue_type:
        query = query.filter(ApprovalTemplate.venue_type == venue_type)
    total = query.count()
    templates = query.offset(skip).limit(limit).all()
    return ApprovalTemplateListResponse(items=templates, total=total)


@router.get("/{template_id}", response_model=ApprovalTemplateResponse)
def get_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_all_authenticated),
):
    template = db.query(ApprovalTemplate).filter(ApprovalTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="审批模板不存在")
    return template


@router.put("/{template_id}", response_model=ApprovalTemplateResponse)
def update_template(
    template_id: int,
    template_in: ApprovalTemplateUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    template = db.query(ApprovalTemplate).filter(
        ApprovalTemplate.id == template_id,
        ApprovalTemplate.is_deleted == False,
    ).first()
    if not template:
        raise HTTPException(status_code=404, detail="审批模板不存在或已删除")

    update_data = template_in.model_dump(exclude_unset=True, exclude={"nodes"})
    for field, value in update_data.items():
        setattr(template, field, value)

    if template_in.nodes is not None:
        _check_node_orders(template_in.nodes)
        db.query(ApprovalNode).filter(ApprovalNode.template_id == template.id).delete()
        for node_data in template_in.nodes:
            node = ApprovalNode(
                template_id=template.id,
                node_name=node_data.node_name,
                order_index=node_data.order_index,
                auditor_role=node_data.auditor_role,
                timeout_minutes=node_data.timeout_minutes,
                timeout_strategy=node_data.timeout_strategy,
                transfer_to_user_id=node_data.transfer_to_user_id,
            )
            db.add(node)

    db.commit()
    db.refresh(template)
    return template


@router.get("/{template_id}/delete-check", response_model=DeleteCheckResponse)
def check_template_delete(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    template = db.query(ApprovalTemplate).filter(ApprovalTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="审批模板不存在")

    rule_count = db.query(BookingRule).filter(
        BookingRule.approval_template_id == template_id
    ).count()

    booking_count = db.query(BookingApplication).join(BookingRule).filter(
        BookingRule.approval_template_id == template_id
    ).count()

    total_deps = rule_count + booking_count

    if total_deps > 0:
        return DeleteCheckResponse(
            can_delete=True,
            delete_type=DeleteType.SOFT,
            message=f"该模板已关联 {rule_count} 条预约规则、{booking_count} 条预约记录，只能进行软删除（标记删除，保留历史数据）",
            dependency_count=total_deps,
        )
    else:
        return DeleteCheckResponse(
            can_delete=True,
            delete_type=DeleteType.HARD,
            message="该模板未被任何预约规则或预约使用，可以进行硬删除彻底移除",
            dependency_count=0,
        )


@router.delete("/{template_id}", response_model=DeleteResultResponse)
def delete_template(
    template_id: int,
    hard_delete: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    template = db.query(ApprovalTemplate).filter(ApprovalTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="审批模板不存在")

    rule_count = db.query(BookingRule).filter(
        BookingRule.approval_template_id == template_id
    ).count()

    booking_count = db.query(BookingApplication).join(BookingRule).filter(
        BookingRule.approval_template_id == template_id
    ).count()

    total_deps = rule_count + booking_count

    if total_deps > 0:
        if hard_delete:
            return DeleteResultResponse(
                success=False,
                delete_type=DeleteType.NONE,
                message=f"该模板已关联 {rule_count} 条预约规则、{booking_count} 条预约记录，不允许硬删除。请使用软删除",
            )
        template.is_deleted = True
        template.is_active = False
        db.commit()
        return DeleteResultResponse(
            success=True,
            delete_type=DeleteType.SOFT,
            message=f"已软删除审批模板，保留 {total_deps} 条历史关联数据",
        )
    else:
        db.delete(template)
        db.commit()
        return DeleteResultResponse(
            success=True,
            delete_type=DeleteType.HARD,
            message="已彻底删除审批模板（硬删除）",
        )
