from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from core.tenant_db import get_tenant_db
from core.dependencies import require_role, get_current_user
from models.tenant import Attendance, User, Device
from schemas.attendance import AttendanceCreate, AttendanceResponse
from datetime import date

router = APIRouter()


@router.post("/{tenant_slug}/attendance", response_model=AttendanceResponse)
def mark_attendance(
    tenant_slug: str,
    data: AttendanceCreate,
    current_user: dict = Depends(require_role("admin", "manager")),
):
    """
    Отметить посещение вручную.
    Admin и manager могут отмечать любого пользователя.
    """

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        # проверяем что пользователь существует
        user = tenant_db.query(User).filter(
            User.id == data.user_id,
            User.is_active == True,
        ).first()
        if not user:
            raise HTTPException(
                status_code=404,
                detail="Пользователь не найден"
            )

        # проверяем что устройство существует
        if data.device_id:
            device = tenant_db.query(Device).filter(
                Device.id == data.device_id,
                Device.is_active == True,
            ).first()
            if not device:
                raise HTTPException(
                    status_code=404,
                    detail="Устройство не найдено"
                )

        today = date.today()

        # проверяем что сегодня ещё не отмечен
        existing = tenant_db.query(Attendance).filter(
            Attendance.user_id == data.user_id,
            Attendance.date == today,
        ).first()
        if existing:
            raise HTTPException(
                status_code=400,
                detail="Пользователь уже отмечен сегодня"
            )

        attendance = Attendance(
            user_id=data.user_id,
            device_id=data.device_id,
            date=today,
            status="manual", 
            note=data.note,
        )
        tenant_db.add(attendance)
        tenant_db.commit()
        tenant_db.refresh(attendance)

        return attendance
    finally:
        tenant_db.close()


@router.get("/{tenant_slug}/attendance", response_model=list[AttendanceResponse])
def get_attendance(
    tenant_slug: str,
    user_id: int = None,
    date_from: date = None,
    date_to: date = None,
    current_user: dict = Depends(require_role("admin", "manager")),
):
    """
    Список посещений с фильтрами.
    Примеры:
      /attendance?user_id=5
      /attendance?date_from=2026-04-01&date_to=2026-04-30
      /attendance?user_id=5&date_from=2026-04-01
    """

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        query = tenant_db.query(Attendance)

        if user_id:
            query = query.filter(Attendance.user_id == user_id)
        if date_from:
            query = query.filter(Attendance.date >= date_from)
        if date_to:
            query = query.filter(Attendance.date <= date_to)

        return query.order_by(Attendance.marked_at.desc()).all()
    finally:
        tenant_db.close()


@router.get("/{tenant_slug}/attendance/my", response_model=list[AttendanceResponse])
def get_my_attendance(
    tenant_slug: str,
    date_from: date = None,
    date_to: date = None,
    current_user: dict = Depends(get_current_user),
):
    """
    Личная посещаемость текущего пользователя.
    Доступно всем авторизованным.
    """

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        query = tenant_db.query(Attendance).filter(
            Attendance.user_id == current_user["user_id"]
        )

        if date_from:
            query = query.filter(Attendance.date >= date_from)
        if date_to:
            query = query.filter(Attendance.date <= date_to)

        return query.order_by(Attendance.marked_at.desc()).all()
    finally:
        tenant_db.close()


@router.delete("/{tenant_slug}/attendance/{attendance_id}")
def delete_attendance(
    tenant_slug: str,
    attendance_id: int,
    current_user: dict = Depends(require_role("admin")),
):
    """Удалить запись посещения. Только admin."""

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        attendance = tenant_db.query(Attendance).filter(
            Attendance.id == attendance_id
        ).first()

        if not attendance:
            raise HTTPException(
                status_code=404,
                detail="Запись не найдена"
            )

        tenant_db.delete(attendance)
        tenant_db.commit()

        return {"message": "Запись удалена"}
    finally:
        tenant_db.close()