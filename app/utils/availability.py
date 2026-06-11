from datetime import date, datetime, time, timedelta
from typing import List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import (
    BookingApplication,
    BookingRule,
    BookingRuleVersion,
    BookingStatus,
    Venue,
    VenueAvailableSlot,
    VenueUnavailablePeriod,
    Weekday,
)


def check_venue_active(venue: Venue) -> Optional[str]:
    if venue.is_deleted:
        return "场地已删除，不可预约"
    if not venue.is_active:
        return "场地已停用，不可预约"
    return None


def check_booking_date_rule(
    booking_date: date, rule: BookingRule, version: int
) -> Optional[str]:
    rule_version = None
    for v in rule.versions:
        if v.version == version:
            rule_version = v
            break
    if not rule_version:
        return f"规则版本 v{version} 不存在"

    advance_days = (booking_date - datetime.utcnow().date()).days
    if advance_days < 0:
        return "不能预约过去的日期"
    if advance_days > rule_version.advance_days:
        return f"只能提前 {rule_version.advance_days} 天预约，当前提前 {advance_days} 天"
    if not rule_version.allow_weekends and booking_date.weekday() >= 5:
        return "该规则不允许周末预约"
    return None


def check_time_in_available_slots(
    db: Session,
    venue_id: int,
    booking_date: date,
    start_time: datetime,
    end_time: datetime,
) -> Optional[str]:
    weekday = Weekday(booking_date.weekday())
    slots = (
        db.query(VenueAvailableSlot)
        .filter(
            VenueAvailableSlot.venue_id == venue_id,
            VenueAvailableSlot.weekday == weekday,
            VenueAvailableSlot.is_active == True,
        )
        .all()
    )

    if not slots:
        return f"场地在{weekday.name}未配置开放时段，不可预约"

    s_time = start_time.time()
    e_time = end_time.time()

    covered = False
    for slot in slots:
        if s_time >= slot.start_time and e_time <= slot.end_time:
            covered = True
            break

    if not covered:
        slot_desc = "、".join(
            f"{s.start_time.strftime('%H:%M')}-{s.end_time.strftime('%H:%M')}"
            for s in slots
        )
        return f"预约时间 {s_time.strftime('%H:%M')}-{e_time.strftime('%H:%M')} 不在开放时段内（{weekday.name}开放时段：{slot_desc}）"

    return None


def check_unavailable_periods(
    db: Session,
    venue_id: int,
    booking_date: date,
    start_time: datetime,
    end_time: datetime,
) -> Optional[str]:
    periods = (
        db.query(VenueUnavailablePeriod)
        .filter(
            VenueUnavailablePeriod.venue_id == venue_id,
            VenueUnavailablePeriod.is_active == True,
            VenueUnavailablePeriod.start_date <= booking_date,
            VenueUnavailablePeriod.end_date >= booking_date,
        )
        .all()
    )

    if not periods:
        return None

    s_time = start_time.time()
    e_time = end_time.time()

    for period in periods:
        if period.start_time is not None and period.end_time is not None:
            if s_time < period.end_time and e_time > period.start_time:
                return f"场地在 {period.start_date}~{period.end_date} 的 {period.start_time.strftime('%H:%M')}-{period.end_time.strftime('%H:%M')} 不可预约（{period.reason}）"
        else:
            return f"场地在 {period.start_date}~{period.end_date} 全天不可预约（{period.reason}）"

    return None


def check_booking_time_overlap(
    db: Session,
    venue_id: int,
    start_time: datetime,
    end_time: datetime,
    exclude_booking_id: Optional[int] = None,
) -> Optional[str]:
    conflict_statuses = [
        BookingStatus.PENDING,
        BookingStatus.APPROVING,
        BookingStatus.APPROVED,
    ]
    query = db.query(BookingApplication).filter(
        BookingApplication.venue_id == venue_id,
        BookingApplication.status.in_(conflict_statuses),
        BookingApplication.start_time < end_time,
        BookingApplication.end_time > start_time,
    )
    if exclude_booking_id is not None:
        query = query.filter(BookingApplication.id != exclude_booking_id)

    conflicts = query.all()
    if not conflicts:
        return None

    conflict_ids = [str(b.id) for b in conflicts]
    return f"预约时间与已有预约（ID: {', '.join(conflict_ids)}）存在时间冲突"


def validate_booking_availability(
    db: Session,
    venue: Venue,
    rule: BookingRule,
    rule_version: int,
    booking_date: date,
    start_time: datetime,
    end_time: datetime,
    exclude_booking_id: Optional[int] = None,
) -> None:
    reason = check_venue_active(venue)
    if reason:
        raise HTTPException(status_code=400, detail=reason)

    reason = check_booking_date_rule(booking_date, rule, rule_version)
    if reason:
        raise HTTPException(status_code=400, detail=reason)

    reason = check_time_in_available_slots(
        db, venue.id, booking_date, start_time, end_time
    )
    if reason:
        raise HTTPException(status_code=400, detail=reason)

    reason = check_unavailable_periods(
        db, venue.id, booking_date, start_time, end_time
    )
    if reason:
        raise HTTPException(status_code=400, detail=reason)

    reason = check_booking_time_overlap(
        db, venue.id, start_time, end_time, exclude_booking_id
    )
    if reason:
        raise HTTPException(status_code=400, detail=reason)


def compute_booking_conflict_status(
    db: Session,
    booking: BookingApplication,
) -> Tuple[Optional[str], Optional[str]]:
    if booking.status in [
        BookingStatus.CANCELLED,
        BookingStatus.TERMINATED,
        BookingStatus.REJECTED,
    ]:
        return None, None

    venue = db.query(Venue).filter(Venue.id == booking.venue_id).first()
    if not venue:
        return "venue_not_found", "场地不存在"

    venue_reason = check_venue_active(venue)
    if venue_reason:
        return "venue_unavailable", venue_reason

    booking_date = booking.booking_date.date() if hasattr(booking.booking_date, 'date') else booking.booking_date

    slot_reason = check_time_in_available_slots(
        db, venue.id, booking_date, booking.start_time, booking.end_time
    )
    if slot_reason:
        return "outside_available_slots", slot_reason

    unavailable_reason = check_unavailable_periods(
        db, venue.id, booking_date, booking.start_time, booking.end_time
    )
    if unavailable_reason:
        return "in_unavailable_period", unavailable_reason

    overlap_reason = check_booking_time_overlap(
        db, venue.id, booking.start_time, booking.end_time, booking.id
    )
    if overlap_reason:
        return "time_conflict", overlap_reason

    return None, None


def get_available_time_slots(
    db: Session,
    venue_id: int,
    query_date: date,
    slot_duration_minutes: int = 30,
) -> List[dict]:
    weekday = Weekday(query_date.weekday())
    slots = (
        db.query(VenueAvailableSlot)
        .filter(
            VenueAvailableSlot.venue_id == venue_id,
            VenueAvailableSlot.weekday == weekday,
            VenueAvailableSlot.is_active == True,
        )
        .order_by(VenueAvailableSlot.start_time)
        .all()
    )

    if not slots:
        return []

    unavailable_periods = (
        db.query(VenueUnavailablePeriod)
        .filter(
            VenueUnavailablePeriod.venue_id == venue_id,
            VenueUnavailablePeriod.is_active == True,
            VenueUnavailablePeriod.start_date <= query_date,
            VenueUnavailablePeriod.end_date >= query_date,
        )
        .all()
    )

    conflict_statuses = [
        BookingStatus.PENDING,
        BookingStatus.APPROVING,
        BookingStatus.APPROVED,
    ]
    existing_bookings = (
        db.query(BookingApplication)
        .filter(
            BookingApplication.venue_id == venue_id,
            BookingApplication.status.in_(conflict_statuses),
        )
        .all()
    )
    day_bookings = [
        b for b in existing_bookings
        if (b.start_time.date() if hasattr(b.start_time, 'date') else b.start_time) == query_date
    ]

    result = []
    for slot in slots:
        current = datetime.combine(query_date, slot.start_time)
        slot_end = datetime.combine(query_date, slot.end_time)
        delta = timedelta(minutes=slot_duration_minutes)

        while current + delta <= slot_end:
            seg_start = current
            seg_end = current + delta
            available = True
            reason = None

            for up in unavailable_periods:
                if up.start_time is not None and up.end_time is not None:
                    if seg_start.time() < up.end_time and seg_end.time() > up.start_time:
                        available = False
                        reason = up.reason
                        break
                else:
                    available = False
                    reason = up.reason
                    break

            if available:
                for b in day_bookings:
                    if seg_start < b.end_time and seg_end > b.start_time:
                        available = False
                        reason = f"与预约 #{b.id} 冲突"
                        break

            result.append({
                "start_time": seg_start,
                "end_time": seg_end,
                "available": available,
                "unavailable_reason": reason,
            })
            current = seg_end

    return result
