from pydantic import BaseModel
from datetime import datetime


class DepartmentCreate(BaseModel):
    name: str


class DepartmentResponse(BaseModel):
    id: int
    name: str
    created_at: datetime

    class Config:
        from_attributes = True