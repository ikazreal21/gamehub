"""Pydantic models for API."""
from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class PortMapping(BaseModel):
    host: int
    container: int
    protocol: str = "udp"  # udp/tcp/both


class CreateServerRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    game: str  # template id: palworld, minecraft, valheim, zomboid, generic
    ports: Optional[List[PortMapping]] = None  # override defaults
    env: Optional[Dict[str, str]] = None
    image: Optional[str] = None  # override (generic / custom)
    rcon_password: Optional[str] = None
    extra_args: Optional[str] = None
    start: bool = True  # False = create container only, don't start it
    dockerfile: Optional[str] = None  # paste a Dockerfile -> built server-side, tagged per-server


class UpdateEnvRequest(BaseModel):
    env: Dict[str, str]


class RconRequest(BaseModel):
    command: str


class ConfigSaveRequest(BaseModel):
    content: str


class BackupResponse(BaseModel):
    filename: str
    size_bytes: int
    created: str


class ServerStatus(BaseModel):
    id: str
    name: str
    game: str
    state: str  # running, stopped, missing, etc.
    image: str
    ports: List[Dict[str, Any]] = []
    cpu_percent: Optional[float] = None
    mem_mb: Optional[float] = None
    players_online: Optional[int] = None
    uptime: Optional[str] = None
