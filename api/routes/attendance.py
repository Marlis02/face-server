from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from core.tenant_db import get_tenant_db
from core.dependencies import require_role, get_current_user
from models.tenant import Attendance, User, Group, Department
from schemas.attendance import (
    AttendanceCreate,
    AttendanceResponse,
    UserAttendanceStat,
    GroupAttendanceStat,
    DailyAttendance,
    UserDailyDetail,
)
from datetime import date, timedelta
from typing import Optional
from models.tenant import Device

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
        
        
        

# ─── Статистика ───────────────────────────────────────────────

@router.get("/{tenant_slug}/stats/today", response_model=list[GroupAttendanceStat])
def stats_today(
    tenant_slug: str,
    current_user: dict = Depends(require_role("admin", "manager")),
):
    """
    Статистика за сегодня по всем группам.
    Показывает сколько человек пришло в каждой группе.
    """

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        today = date.today()
        groups = tenant_db.query(Group).all()
        result = []

        for group in groups:
            # все активные пользователи группы
            total_users = tenant_db.query(User).filter(
                User.group_id == group.id,
                User.is_active == True,
            ).count()

            if total_users == 0:
                continue

            # кто отмечен сегодня
            present_today = tenant_db.query(Attendance).join(User).filter(
                User.group_id == group.id,
                Attendance.date == today,
            ).count()

            absent_today = total_users - present_today
            percent = round(present_today / total_users * 100, 1)

            dept = tenant_db.query(Department).filter(
                Department.id == group.department_id
            ).first()

            result.append(GroupAttendanceStat(
                group_id=group.id,
                group_name=group.name,
                department_name=dept.name if dept else "",
                total_users=total_users,
                present_today=present_today,
                absent_today=absent_today,
                percent_today=percent,
            ))

        return result
    finally:
        tenant_db.close()


@router.get("/{tenant_slug}/stats/group/{group_id}", response_model=list[UserAttendanceStat])
def stats_group(
    tenant_slug: str,
    group_id: int,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    current_user: dict = Depends(require_role("admin", "manager")),
):
    """
    Статистика посещаемости по группе за период.
    По умолчанию — последние 30 дней.
    """

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        # по умолчанию последние 30 дней
        if not date_from:
            date_from = date.today() - timedelta(days=30)
        if not date_to:
            date_to = date.today()

        # всего рабочих дней в периоде
        total_days = (date_to - date_from).days + 1

        users = tenant_db.query(User).filter(
            User.group_id == group_id,
            User.is_active == True,
        ).all()

        if not users:
            raise HTTPException(status_code=404, detail="Группа не найдена или пуста")

        result = []

        for user in users:
            present_days = tenant_db.query(Attendance).filter(
                Attendance.user_id == user.id,
                Attendance.date >= date_from,
                Attendance.date <= date_to,
            ).count()

            absent_days = total_days - present_days
            percent = round(present_days / total_days * 100, 1)

            result.append(UserAttendanceStat(
                user_id=user.id,
                full_name=user.full_name,
                employee_id=user.employee_id,
                total_days=total_days,
                present_days=present_days,
                absent_days=absent_days,
                percent=percent,
            ))

        # сортируем по проценту посещаемости
        result.sort(key=lambda x: x.percent, reverse=True)

        return result
    finally:
        tenant_db.close()


@router.get("/{tenant_slug}/stats/user/{user_id}", response_model=list[DailyAttendance])
def stats_user(
    tenant_slug: str,
    user_id: int,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    current_user: dict = Depends(require_role("admin", "manager")),
):
    """
    Детальная статистика по пользователю — по дням.
    Показывает каждый день: был / не был.
    """

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        user = tenant_db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")

        if not date_from:
            date_from = date.today() - timedelta(days=30)
        if not date_to:
            date_to = date.today()

        # все отметки пользователя за период
        attendances = tenant_db.query(Attendance).filter(
            Attendance.user_id == user_id,
            Attendance.date >= date_from,
            Attendance.date <= date_to,
        ).all()

        # множество дней когда был
        present_dates = {a.date for a in attendances}

        result = []
        current = date_from

        while current <= date_to:
            is_present = current in present_dates
            result.append(DailyAttendance(
                date=current,
                present_count=1 if is_present else 0,
                absent_count=0 if is_present else 1,
                total_count=1,
                percent=100.0 if is_present else 0.0,
            ))
            current += timedelta(days=1)

        return result
    finally:
        tenant_db.close()


@router.get("/{tenant_slug}/stats/group/{group_id}/daily", response_model=list[DailyAttendance])
def stats_group_daily(
    tenant_slug: str,
    group_id: int,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    current_user: dict = Depends(require_role("admin", "manager")),
):
    """
    Статистика посещаемости группы по дням.
    Показывает динамику — как менялась посещаемость.
    """

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        if not date_from:
            date_from = date.today() - timedelta(days=30)
        if not date_to:
            date_to = date.today()

        total_users = tenant_db.query(User).filter(
            User.group_id == group_id,
            User.is_active == True,
        ).count()

        if total_users == 0:
            raise HTTPException(status_code=404, detail="Группа не найдена или пуста")

        # считаем по дням через SQL
        daily_counts = tenant_db.query(
            Attendance.date,
            func.count(Attendance.id).label("count"),
        ).join(User).filter(
            User.group_id == group_id,
            Attendance.date >= date_from,
            Attendance.date <= date_to,
        ).group_by(Attendance.date).all()

        # словарь дата → количество
        counts_by_date = {row.date: row.count for row in daily_counts}

        result = []
        current = date_from

        while current <= date_to:
            present = counts_by_date.get(current, 0)
            absent = total_users - present
            percent = round(present / total_users * 100, 1)

            result.append(DailyAttendance(
                date=current,
                present_count=present,
                absent_count=absent,
                total_count=total_users,
                percent=percent,
            ))
            current += timedelta(days=1)

        return result
    finally:
        tenant_db.close()


@router.get("/{tenant_slug}/stats/group/{group_id}/detail", response_model=list[UserDailyDetail])
def stats_group_detail_today(
    tenant_slug: str,
    group_id: int,
    for_date: Optional[date] = None,
    current_user: dict = Depends(require_role("admin", "manager")),
):
    """
    Детальный список — кто пришёл кто нет за конкретный день.
    По умолчанию — сегодня.
    """

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        target_date = for_date or date.today()

        users = tenant_db.query(User).filter(
            User.group_id == group_id,
            User.is_active == True,
        ).all()

        if not users:
            raise HTTPException(status_code=404, detail="Группа не найдена или пуста")

        # все отметки за этот день для этой группы
        user_ids = [u.id for u in users]
        attendances = tenant_db.query(Attendance).filter(
            Attendance.user_id.in_(user_ids),
            Attendance.date == target_date,
        ).all()

        attendance_by_user = {a.user_id: a for a in attendances}

        result = []
        for user in users:
            att = attendance_by_user.get(user.id)
            result.append(UserDailyDetail(
                user_id=user.id,
                full_name=user.full_name,
                employee_id=user.employee_id,
                status="present" if att else "absent",
                marked_at=att.marked_at if att else None,
                device_id=att.device_id if att else None,
            ))

        # сначала присутствующие
        result.sort(key=lambda x: (x.status == "absent", x.full_name))

        return result
    finally:
        tenant_db.close()


@router.get("/{tenant_slug}/stats/summary")
def stats_summary(
    tenant_slug: str,
    current_user: dict = Depends(require_role("admin", "manager")),
):
    """
    Общая сводка по организации за сегодня.
    Быстрый обзор — всего людей, пришло, не пришло.
    """

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        today = date.today()

        total_users = tenant_db.query(User).filter(
            User.is_active == True,
        ).count()

        present_today = tenant_db.query(Attendance).filter(
            Attendance.date == today,
        ).count()

        absent_today = total_users - present_today
        percent = round(present_today / total_users * 100, 1) if total_users > 0 else 0.0

        total_groups = tenant_db.query(Group).count()
        total_departments = tenant_db.query(Department).count()

        return {
            "date": today,
            "total_users": total_users,
            "present_today": present_today,
            "absent_today": absent_today,
            "percent_today": percent,
            "total_groups": total_groups,
            "total_departments": total_departments,
        }
    finally:
        tenant_db.close()