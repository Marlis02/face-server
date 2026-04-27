from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class DeviceCreate(BaseModel):
    name: str
    login: str
    password: str
    location: Optional[str] = None


class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    login: Optional[str] = None
    password: Optional[str] = None
    location: Optional[str] = None
    is_active: Optional[bool] = None


class DeviceResponse(BaseModel):
    id: int
    name: str
    login: str
    token: Optional[str]
    location: Optional[str]
    is_active: bool
    last_seen_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class DeviceLoginRequest(BaseModel):
    login: str
    password: str


class DeviceLoginResponse(BaseModel):
    device_token: str
    device_id: int
    device_name: str


class DeviceInitResponse(BaseModel):
    device_id: int
    device_name: str
    location: Optional[str]
    organization_name: str
    organization_type: str