from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin, require_all_authenticated
from app.models import (
    BookingApplication,
    User,
    Venue,
    VenueAvailableSlot,
    VenueUnavailablePeriod,
)
from app.schemas import (
    AvailableTimeSlotResponse,
    DeleteCheckResponse,
    DeleteResultResponse,
    DeleteType,
    VenueAvailableSlotCreate,
    VenueAvailableSlotListResponse,
    VenueAvailableSlotResponse,
    VenueAvailableSlotUpdate,
    VenueAvailableTimeSlotsResponse,
    VenueCreate,
    VenueListResponse,
    VenueResponse,
    VenueUnavailablePeriodCreate,
    VenueUnavailablePeriodListResponse,
    VenueUnavailablePeriodResponse,
    VenueUnavailablePeriodUpdate,
    VenueUpdate,
)
from app.utils.availability import get_available_time_slots

router = APIRouter(prefix="/api/venues", tags=["场地管理"])


@router.post("", response_model=VenueResponse, status_code=status.HTTP_201_CREATED)
def create_venue(
    venue_in: VenueCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    venue = Venue(**venue_in.model_dump())
    db.add(venue)
    db.commit()
    db.refresh(venue)
    return venue


@router.get("", response_model=VenueListResponse)
def list_venues(
    skip: int = 0,
    limit: int = 100,
    venue_type: str = None,
    include_deleted: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_all_authenticated),
):
    query = db.query(Venue)
    if not include_deleted:
        query = query.filter(Venue.is_deleted == False)
    if venue_type:
        query = query.filter(Venue.venue_type == venue_type)
    total = query.count()
    venues = query.offset(skip).limit(limit).all()
    return VenueListResponse(items=venues, total=total)


@router.get("/{venue_id}", response_model=VenueResponse)
def get_venue(
    venue_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_all_authenticated),
):
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if not venue:
        raise HTTPException(status_code=404, detail="场地不存在")
    return venue


@router.put("/{venue_id}", response_model=VenueResponse)
def update_venue(
    venue_id: int,
    venue_in: VenueUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    venue = db.query(Venue).filter(Venue.id == venue_id, Venue.is_deleted == False).first()
    if not venue:
        raise HTTPException(status_code=404, detail="场地不存在或已删除")
    update_data = venue_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(venue, field, value)
    db.commit()
    db.refresh(venue)
    return venue


@router.get("/{venue_id}/delete-check", response_model=DeleteCheckResponse)
def check_venue_delete(
    venue_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if not venue:
        raise HTTPException(status_code=404, detail="场地不存在")

    booking_count = db.query(BookingApplication).filter(
        BookingApplication.venue_id == venue_id
    ).count()

    if booking_count > 0:
        return DeleteCheckResponse(
            can_delete=True,
            delete_type=DeleteType.SOFT,
            message=f"该场地已关联 {booking_count} 条预约记录，只能进行软删除（标记删除，保留历史数据）",
            dependency_count=booking_count,
        )
    else:
        return DeleteCheckResponse(
            can_delete=True,
            delete_type=DeleteType.HARD,
            message="该场地未被任何预约使用，可以进行硬删除彻底移除",
            dependency_count=0,
        )


@router.delete("/{venue_id}", response_model=DeleteResultResponse)
def delete_venue(
    venue_id: int,
    hard_delete: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if not venue:
        raise HTTPException(status_code=404, detail="场地不存在")

    booking_count = db.query(BookingApplication).filter(
        BookingApplication.venue_id == venue_id
    ).count()

    if booking_count > 0:
        if hard_delete:
            return DeleteResultResponse(
                success=False,
                delete_type=DeleteType.NONE,
                message=f"该场地已关联 {booking_count} 条预约记录，不允许硬删除。请使用软删除（hard_delete=false）",
            )
        venue.is_deleted = True
        venue.is_active = False
        db.commit()
        return DeleteResultResponse(
            success=True,
            delete_type=DeleteType.SOFT,
            message=f"已软删除场地，保留 {booking_count} 条历史预约记录",
        )
    else:
        db.delete(venue)
        db.commit()
        return DeleteResultResponse(
            success=True,
            delete_type=DeleteType.HARD,
            message="已彻底删除场地（硬删除）",
        )


@router.post(
    "/{venue_id}/available-slots",
    response_model=VenueAvailableSlotResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_available_slot(
    venue_id: int,
    slot_in: VenueAvailableSlotCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    venue = db.query(Venue).filter(Venue.id == venue_id, Venue.is_deleted == False).first()
    if not venue:
        raise HTTPException(status_code=404, detail="场地不存在或已删除")

    existing = db.query(VenueAvailableSlot).filter(
        VenueAvailableSlot.venue_id == venue_id,
        VenueAvailableSlot.weekday == slot_in.weekday,
        VenueAvailableSlot.start_time == slot_in.start_time,
        VenueAvailableSlot.end_time == slot_in.end_time,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="该场地在同一天已存在相同时段配置")

    slot = VenueAvailableSlot(
        venue_id=venue_id,
        weekday=slot_in.weekday,
        start_time=slot_in.start_time,
        end_time=slot_in.end_time,
        is_active=slot_in.is_active,
    )
    db.add(slot)
    db.commit()
    db.refresh(slot)
    return slot


@router.get(
    "/{venue_id}/available-slots",
    response_model=VenueAvailableSlotListResponse,
)
def list_available_slots(
    venue_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_all_authenticated),
):
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if not venue:
        raise HTTPException(status_code=404, detail="场地不存在")

    slots = (
        db.query(VenueAvailableSlot)
        .filter(VenueAvailableSlot.venue_id == venue_id)
        .order_by(VenueAvailableSlot.weekday, VenueAvailableSlot.start_time)
        .all()
    )
    return VenueAvailableSlotListResponse(items=slots, total=len(slots))


@router.put(
    "/{venue_id}/available-slots/{slot_id}",
    response_model=VenueAvailableSlotResponse,
)
def update_available_slot(
    venue_id: int,
    slot_id: int,
    slot_in: VenueAvailableSlotUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    slot = db.query(VenueAvailableSlot).filter(
        VenueAvailableSlot.id == slot_id,
        VenueAvailableSlot.venue_id == venue_id,
    ).first()
    if not slot:
        raise HTTPException(status_code=404, detail="开放时段不存在")

    update_data = slot_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(slot, field, value)
    db.commit()
    db.refresh(slot)
    return slot


@router.delete(
    "/{venue_id}/available-slots/{slot_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_available_slot(
    venue_id: int,
    slot_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    slot = db.query(VenueAvailableSlot).filter(
        VenueAvailableSlot.id == slot_id,
        VenueAvailableSlot.venue_id == venue_id,
    ).first()
    if not slot:
        raise HTTPException(status_code=404, detail="开放时段不存在")
    db.delete(slot)
    db.commit()


@router.post(
    "/{venue_id}/unavailable-periods",
    response_model=VenueUnavailablePeriodResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_unavailable_period(
    venue_id: int,
    period_in: VenueUnavailablePeriodCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    venue = db.query(Venue).filter(Venue.id == venue_id, Venue.is_deleted == False).first()
    if not venue:
        raise HTTPException(status_code=404, detail="场地不存在或已删除")

    period = VenueUnavailablePeriod(
        venue_id=venue_id,
        start_date=period_in.start_date,
        end_date=period_in.end_date,
        start_time=period_in.start_time,
        end_time=period_in.end_time,
        reason=period_in.reason,
        is_active=period_in.is_active,
    )
    db.add(period)
    db.commit()
    db.refresh(period)
    return period


@router.get(
    "/{venue_id}/unavailable-periods",
    response_model=VenueUnavailablePeriodListResponse,
)
def list_unavailable_periods(
    venue_id: int,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_all_authenticated),
):
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if not venue:
        raise HTTPException(status_code=404, detail="场地不存在")

    query = db.query(VenueUnavailablePeriod).filter(
        VenueUnavailablePeriod.venue_id == venue_id
    )
    if not include_inactive:
        query = query.filter(VenueUnavailablePeriod.is_active == True)
    periods = query.order_by(VenueUnavailablePeriod.start_date).all()
    return VenueUnavailablePeriodListResponse(items=periods, total=len(periods))


@router.put(
    "/{venue_id}/unavailable-periods/{period_id}",
    response_model=VenueUnavailablePeriodResponse,
)
def update_unavailable_period(
    venue_id: int,
    period_id: int,
    period_in: VenueUnavailablePeriodUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    period = db.query(VenueUnavailablePeriod).filter(
        VenueUnavailablePeriod.id == period_id,
        VenueUnavailablePeriod.venue_id == venue_id,
    ).first()
    if not period:
        raise HTTPException(status_code=404, detail="不可预约时段不存在")

    update_data = period_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(period, field, value)
    db.commit()
    db.refresh(period)
    return period


@router.delete(
    "/{venue_id}/unavailable-periods/{period_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_unavailable_period(
    venue_id: int,
    period_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    period = db.query(VenueUnavailablePeriod).filter(
        VenueUnavailablePeriod.id == period_id,
        VenueUnavailablePeriod.venue_id == venue_id,
    ).first()
    if not period:
        raise HTTPException(status_code=404, detail="不可预约时段不存在")
    db.delete(period)
    db.commit()


@router.get(
    "/{venue_id}/available-times",
    response_model=VenueAvailableTimeSlotsResponse,
)
def get_venue_available_times(
    venue_id: int,
    query_date: date = Query(..., description="查询日期"),
    slot_duration: int = Query(30, ge=15, le=120, description="时段粒度（分钟）"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_all_authenticated),
):
    venue = db.query(Venue).filter(Venue.id == venue_id, Venue.is_deleted == False).first()
    if not venue:
        raise HTTPException(status_code=404, detail="场地不存在或已删除")
    if not venue.is_active:
        raise HTTPException(status_code=400, detail="场地已停用")

    slots = get_available_time_slots(db, venue_id, query_date, slot_duration)
    return VenueAvailableTimeSlotsResponse(
        venue_id=venue_id,
        date=query_date,
        slots=[AvailableTimeSlotResponse(**s) for s in slots],
    )
