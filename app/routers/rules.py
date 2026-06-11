from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin, require_all_authenticated
from app.models import (
    ApprovalTemplate,
    BookingApplication,
    BookingRule,
    BookingRuleVersion,
    User,
    Venue,
)
from app.schemas import (
    BookingRuleCreate,
    BookingRuleListResponse,
    BookingRuleResponse,
    BookingRuleUpdate,
    BookingRuleVersionResponse,
    DeleteCheckResponse,
    DeleteResultResponse,
    DeleteType,
)

router = APIRouter(prefix="/api/rules", tags=["预约规则管理"])


def _enrich_rule_response(rule: BookingRule) -> BookingRuleResponse:
    current_version_data = None
    for v in rule.versions:
        if v.version == rule.current_version:
            current_version_data = v
            break
    response = BookingRuleResponse.model_validate(rule)
    if current_version_data:
        response.current_version_data = BookingRuleVersionResponse.model_validate(
            current_version_data
        )
    return response


@router.post("", response_model=BookingRuleResponse, status_code=status.HTTP_201_CREATED)
def create_rule(
    rule_in: BookingRuleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    venue = db.query(Venue).filter(
        Venue.id == rule_in.venue_id, Venue.is_deleted == False
    ).first()
    if not venue:
        raise HTTPException(status_code=400, detail="关联场地不存在或已删除")

    template = db.query(ApprovalTemplate).filter(
        ApprovalTemplate.id == rule_in.approval_template_id,
        ApprovalTemplate.is_deleted == False,
    ).first()
    if not template:
        raise HTTPException(status_code=400, detail="关联审批模板不存在或已删除")

    if venue.venue_type != template.venue_type:
        raise HTTPException(
            status_code=400,
            detail=f"场地类型 '{venue.venue_type}' 与审批模板类型 '{template.venue_type}' 不匹配，不能绑定",
        )

    rule = BookingRule(
        venue_id=rule_in.venue_id,
        approval_template_id=rule_in.approval_template_id,
        current_version=1,
    )
    db.add(rule)
    db.flush()

    version = BookingRuleVersion(
        rule_id=rule.id,
        version=1,
        advance_days=rule_in.rule_config.advance_days,
        max_duration_hours=rule_in.rule_config.max_duration_hours,
        min_duration_hours=rule_in.rule_config.min_duration_hours,
        allow_weekends=rule_in.rule_config.allow_weekends,
        allow_holidays=rule_in.rule_config.allow_holidays,
        extra_config=rule_in.rule_config.extra_config or {},
        description=rule_in.rule_config.description,
        created_by=current_user.id,
    )
    db.add(version)
    db.commit()
    db.refresh(rule)
    return _enrich_rule_response(rule)


@router.get("", response_model=BookingRuleListResponse)
def list_rules(
    skip: int = 0,
    limit: int = 100,
    venue_id: int = None,
    include_deleted: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_all_authenticated),
):
    query = db.query(BookingRule)
    if not include_deleted:
        query = query.filter(BookingRule.is_deleted == False)
    if venue_id:
        query = query.filter(BookingRule.venue_id == venue_id)
    total = query.count()
    rules = query.offset(skip).limit(limit).all()
    enriched = [_enrich_rule_response(r) for r in rules]
    return BookingRuleListResponse(items=enriched, total=total)


@router.get("/{rule_id}", response_model=BookingRuleResponse)
def get_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_all_authenticated),
):
    rule = db.query(BookingRule).filter(BookingRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="预约规则不存在")
    return _enrich_rule_response(rule)


@router.get("/{rule_id}/versions/{version}", response_model=BookingRuleVersionResponse)
def get_rule_version(
    rule_id: int,
    version: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_all_authenticated),
):
    v = db.query(BookingRuleVersion).filter(
        BookingRuleVersion.rule_id == rule_id,
        BookingRuleVersion.version == version,
    ).first()
    if not v:
        raise HTTPException(status_code=404, detail="该版本不存在")
    return v


@router.put("/{rule_id}", response_model=BookingRuleResponse)
def update_rule(
    rule_id: int,
    rule_in: BookingRuleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    rule = db.query(BookingRule).filter(
        BookingRule.id == rule_id, BookingRule.is_deleted == False
    ).first()
    if not rule:
        raise HTTPException(status_code=404, detail="预约规则不存在或已删除")

    if rule_in.approval_template_id is not None:
        template = db.query(ApprovalTemplate).filter(
            ApprovalTemplate.id == rule_in.approval_template_id,
            ApprovalTemplate.is_deleted == False,
        ).first()
        if not template:
            raise HTTPException(status_code=400, detail="关联审批模板不存在或已删除")
        if rule.venue and rule.venue.venue_type != template.venue_type:
            raise HTTPException(
                status_code=400,
                detail=f"场地类型 '{rule.venue.venue_type}' 与审批模板类型 '{template.venue_type}' 不匹配，不能绑定",
            )
        rule.approval_template_id = rule_in.approval_template_id

    if rule_in.is_active is not None:
        rule.is_active = rule_in.is_active

    if rule_in.rule_config is not None:
        new_version_num = rule.current_version + 1
        new_version = BookingRuleVersion(
            rule_id=rule.id,
            version=new_version_num,
            advance_days=rule_in.rule_config.advance_days,
            max_duration_hours=rule_in.rule_config.max_duration_hours,
            min_duration_hours=rule_in.rule_config.min_duration_hours,
            allow_weekends=rule_in.rule_config.allow_weekends,
            allow_holidays=rule_in.rule_config.allow_holidays,
            extra_config=rule_in.rule_config.extra_config or {},
            description=rule_in.rule_config.description,
            created_by=current_user.id,
        )
        db.add(new_version)
        rule.current_version = new_version_num

    db.commit()
    db.refresh(rule)
    return _enrich_rule_response(rule)


@router.get("/{rule_id}/delete-check", response_model=DeleteCheckResponse)
def check_rule_delete(
    rule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    rule = db.query(BookingRule).filter(BookingRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="预约规则不存在")

    booking_count = db.query(BookingApplication).filter(
        BookingApplication.rule_id == rule_id
    ).count()

    if booking_count > 0:
        return DeleteCheckResponse(
            can_delete=True,
            delete_type=DeleteType.SOFT,
            message=f"该规则已产生 {booking_count} 条预约审批记录，只能进行软删除（标记删除，保留历史数据用于追溯）",
            dependency_count=booking_count,
        )
    else:
        return DeleteCheckResponse(
            can_delete=True,
            delete_type=DeleteType.HARD,
            message="该规则从未被任何预约使用，可以进行硬删除彻底移除",
            dependency_count=0,
        )


@router.delete("/{rule_id}", response_model=DeleteResultResponse)
def delete_rule(
    rule_id: int,
    hard_delete: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    rule = db.query(BookingRule).filter(BookingRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="预约规则不存在")

    booking_count = db.query(BookingApplication).filter(
        BookingApplication.rule_id == rule_id
    ).count()

    if booking_count > 0:
        if hard_delete:
            return DeleteResultResponse(
                success=False,
                delete_type=DeleteType.NONE,
                message=f"该规则已关联 {booking_count} 条预约记录，不允许硬删除，以免破坏历史审批链路。请使用软删除（hard_delete=false）",
            )
        rule.is_deleted = True
        rule.is_active = False
        db.commit()
        return DeleteResultResponse(
            success=True,
            delete_type=DeleteType.SOFT,
            message=f"已软删除预约规则，保留 {booking_count} 条历史预约记录及其版本快照",
        )
    else:
        db.query(BookingRuleVersion).filter(
            BookingRuleVersion.rule_id == rule_id
        ).delete(synchronize_session=False)
        db.delete(rule)
        db.commit()
        return DeleteResultResponse(
            success=True,
            delete_type=DeleteType.HARD,
            message="已彻底删除预约规则及其所有版本（硬删除）",
        )
