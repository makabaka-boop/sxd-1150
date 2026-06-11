from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin, require_all_authenticated
from app.models import BookingApplication, User, Venue
from app.schemas import (
    DeleteCheckResponse,
    DeleteResultResponse,
    DeleteType,
    VenueCreate,
    VenueListResponse,
    VenueResponse,
    VenueUpdate,
)

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
