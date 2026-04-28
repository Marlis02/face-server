from pydantic import BaseModel
from datetime import datetime, date
from typing import Optional


class AttendanceCreate(BaseModel):
    user_id: int
    device_id: Optional[int] = None
    note: Optional[str] = None


class AttendanceResponse(BaseModel):
    id: int
    user_id: int
    device_id: Optional[int]
    date: date
    marked_at: datetime
    status: str
    note: Optional[str]

    class Config:
        from_attributes = True


class UserAttendanceStat(BaseModel):
    user_id: int
    full_name: str
    total_days: int
    present_days: int
    absent_days: int
    percent: float


class GroupAttendanceStat(BaseModel):
    group_id: int
    group_name: str
    department_name: str
    total_users: int
    present_today: int
    absent_today: int
    percent_today: float


class DailyAttendance(BaseModel):
    date: date
    present_count: int
    absent_count: int
    total_count: int
    percent: float


class UserDailyDetail(BaseModel):
    user_id: int
    full_name: str
    status: str            # present / absent
    marked_at: Optional[datetime]
    device_id: Optional[int]