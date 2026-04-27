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