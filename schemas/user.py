from pydantic import BaseModel, EmailStr
from datetime import datetime
from typing import Optional


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str
    role: str = "user"          # admin / manager / user
    department_id: Optional[int] = None
    group_id: Optional[int] = None


class UserUpdate(BaseModel):
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: Optional[str] = None
    department_id: Optional[int] = None
    group_id: Optional[int] = None
    is_active: Optional[bool] = None


class UserResponse(BaseModel):
    id: int
    email: str
    first_name: str
    last_name: str
    role: str
    department_id: Optional[int]
    group_id: Optional[int]
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True
        
        
class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str