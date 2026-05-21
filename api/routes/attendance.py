from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
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
from datetime import date, datetime, timedelta
from typing import Optional
import io
import xlsxwriter

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


@router.get("/{tenant_slug}/attendance/export")
def export_attendance_xlsx(
    tenant_slug: str,
    user_id: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    group_id: Optional[int] = None,
    department_id: Optional[int] = None,
    current_user: dict = Depends(require_role("admin", "manager")),
):
    """
    Экспорт журнала посещений в Excel (.xlsx).
    Те же фильтры что у /attendance + group_id/department_id.
    Возвращает поток с готовым файлом, имя файла зашито в Content-Disposition.
    """
    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        q = (
            tenant_db.query(Attendance, User, Group, Department, Device)
            .join(User, Attendance.user_id == User.id)
            .outerjoin(Group, User.group_id == Group.id)
            .outerjoin(Department, User.department_id == Department.id)
            .outerjoin(Device, Attendance.device_id == Device.id)
        )

        if user_id:
            q = q.filter(Attendance.user_id == user_id)
        if date_from:
            q = q.filter(Attendance.date >= date_from)
        if date_to:
            q = q.filter(Attendance.date <= date_to)
        if group_id:
            q = q.filter(User.group_id == group_id)
        if department_id:
            q = q.filter(User.department_id == department_id)

        rows = q.order_by(
            Attendance.date.desc(),
            Attendance.marked_at.desc(),
        ).all()

        buf = io.BytesIO()
        wb = xlsxwriter.Workbook(buf, {"in_memory": True})
        ws = wb.add_worksheet("Посещения")

        # форматы
        f_header = wb.add_format({
            "bold": True, "bg_color": "#305496", "font_color": "white",
            "align": "center", "valign": "vcenter", "border": 1,
        })
        f_date = wb.add_format({"num_format": "yyyy-mm-dd", "border": 1})
        f_dt = wb.add_format({"num_format": "yyyy-mm-dd hh:mm:ss", "border": 1})
        f_cell = wb.add_format({"border": 1})
        f_status_auto = wb.add_format({
            "border": 1, "bg_color": "#E2EFDA", "align": "center",
        })
        f_status_manual = wb.add_format({
            "border": 1, "bg_color": "#FFF2CC", "align": "center",
        })

        headers = [
            ("ID", 6),
            ("ФИО", 28),
            ("Email", 26),
            ("Отдел", 22),
            ("Группа", 14),
            ("Дата", 12),
            ("Отмечено в", 20),
            ("Статус", 10),
            ("Устройство", 22),
            ("Примечание", 30),
        ]
        for col, (title, width) in enumerate(headers):
            ws.set_column(col, col, width)
            ws.write(0, col, title, f_header)

        ws.set_row(0, 22)
        ws.freeze_panes(1, 0)
        ws.autofilter(0, 0, max(len(rows), 1), len(headers) - 1)

        for r, (att, usr, grp, dept, dev) in enumerate(rows, start=1):
            ws.write_number(r, 0, att.id, f_cell)
            ws.write_string(r, 1, usr.full_name, f_cell)
            ws.write_string(r, 2, usr.email or "", f_cell)
            ws.write_string(r, 3, dept.name if dept else "", f_cell)
            ws.write_string(r, 4, grp.name if grp else "", f_cell)
            ws.write_datetime(r, 5, datetime.combine(att.date, datetime.min.time()), f_date)
            if att.marked_at:
                ws.write_datetime(r, 6, att.marked_at.replace(tzinfo=None), f_dt)
            else:
                ws.write_string(r, 6, "", f_cell)
            ws.write_string(
                r, 7,
                "авто" if att.status == "auto" else "вручную",
                f_status_auto if att.status == "auto" else f_status_manual,
            )
            ws.write_string(r, 8, dev.name if dev else "", f_cell)
            ws.write_string(r, 9, att.note or "", f_cell)

        # сводка под таблицей
        summary_row = len(rows) + 2
        f_summary = wb.add_format({"italic": True, "font_color": "#595959"})
        ws.write(summary_row, 0, f"Всего записей: {len(rows)}", f_summary)
        if date_from or date_to:
            ws.write(
                summary_row + 1, 0,
                f"Период: {date_from or '...'} — {date_to or '...'}",
                f_summary,
            )

        wb.close()
        buf.seek(0)

        # имя файла с меткой времени, чтобы не перетирался в браузере
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"attendance_{tenant_slug}_{stamp}.xlsx"

        return StreamingResponse(
            buf,
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )
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