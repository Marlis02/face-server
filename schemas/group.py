from pydantic import BaseModel
from datetime import datetime


class GroupCreate(BaseModel):
    name: str
    department_id: int


class GroupResponse(BaseModel):
    id: int
    name: str
    department_id: int
    created_at: datetime

    class Config:
        from_attributes = True