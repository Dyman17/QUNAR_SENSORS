from __future__ import annotations

import os
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, ValidationError


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

ESP_PACKET_ALIASES = {
    "t1": "temperature1",
    "temp1": "temperature1",
    "h1": "humidity1",
    "hum1": "humidity1",
    "t2": "temperature2",
    "temp2": "temperature2",
    "h2": "humidity2",
    "hum2": "humidity2",
    "soilPercent": "soil",
    "soil_percent": "soil",
    "soil_moisture": "soil",
    "relay1": "relay1_state",
    "relay2": "relay2_state",
    "relay1State": "relay1_state",
    "relay2State": "relay2_state",
    "rssi": "wifi_rssi",
    "heap": "free_heap",
    "uptime": "uptime_sec",
    "fw": "firmware_version",
    "fw_version": "firmware_version",
}


class PacketPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    device_id: int
    device_token: str = Field(..., min_length=1)
    recorded_at: datetime | None = None
    temperature1: float | None = None
    humidity1: float | None = None
    temperature2: float | None = None
    humidity2: float | None = None
    soil: float | None = None
    light1: int | None = None
    light2: int | None = None
    relay1_state: int | None = None
    relay2_state: int | None = None
    wifi_rssi: int | None = None
    free_heap: int | None = None
    uptime_sec: int | None = None
    firmware_version: str | None = None


class RelayState(BaseModel):
    relay1_command: int = Field(1, ge=0, le=1)
    relay2_command: int = Field(0, ge=0, le=1)


class ReceiverState:
    def __init__(self) -> None:
        self.lock = Lock()
        self.relay1_command = 1
        self.relay2_command = 0
        self.last_packet: dict[str, Any] | None = None
        self.last_normalized: dict[str, Any] | None = None
        self.last_received_at: str | None = None


state = ReceiverState()
app = FastAPI(title="ESP Packet Receiver", version="0.1.0")
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def mask_token(token: str) -> str:
    if len(token) <= 6:
        return "*" * len(token)
    return f"{token[:4]}...{token[-2:]}"


def normalize_packet(raw_packet: dict[str, Any]) -> tuple[PacketPayload, dict[str, Any]]:
    normalized: dict[str, Any] = {}
    for key, value in raw_packet.items():
        if value is None:
            continue
        normalized[ESP_PACKET_ALIASES.get(key, key)] = value

    try:
        payload = PacketPayload.model_validate(normalized)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors())

    normalized["device_token"] = mask_token(payload.device_token)
    return payload, normalized


def current_dashboard_state() -> dict[str, Any]:
    with state.lock:
        relay_state = {
            "relay1_command": state.relay1_command,
            "relay2_command": state.relay2_command,
        }
        return {
            "relay_state": relay_state,
            "last_packet": state.last_packet,
            "last_normalized": state.last_normalized,
            "last_received_at": state.last_received_at,
        }


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    dashboard_state = current_dashboard_state()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "relay_state": dashboard_state["relay_state"],
            "last_packet": dashboard_state["last_packet"],
            "last_normalized": dashboard_state["last_normalized"],
            "last_received_at": dashboard_state["last_received_at"],
            "api_url": os.getenv("PUBLIC_API_URL", "/api/esp"),
        },
    )


@app.get("/health")
def health():
    dashboard_state = current_dashboard_state()
    return {
        "status": "ok",
        "has_packet": dashboard_state["last_packet"] is not None,
        "last_received_at": dashboard_state["last_received_at"],
    }


@app.post("/commands")
def update_commands_form(
    relay1_command: int = Form(...),
    relay2_command: int = Form(...),
):
    relay_state = RelayState(relay1_command=relay1_command, relay2_command=relay2_command)
    with state.lock:
        state.relay1_command = relay_state.relay1_command
        state.relay2_command = relay_state.relay2_command
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/commands")
def update_commands_api(relay_state: RelayState):
    with state.lock:
        state.relay1_command = relay_state.relay1_command
        state.relay2_command = relay_state.relay2_command
    return {"saved": True, **relay_state.model_dump()}


@app.get("/api/current")
def get_current_state():
    dashboard_state = current_dashboard_state()
    return {
        "relay_state": dashboard_state["relay_state"],
        "last_received_at": dashboard_state["last_received_at"],
        "packet": dashboard_state["last_packet"],
        "normalized_fields": dashboard_state["last_normalized"],
    }


@app.post("/api/esp")
def receive_esp_packet(raw_packet: dict[str, Any]):
    payload, normalized = normalize_packet(raw_packet)

    with state.lock:
        state.last_packet = {
            **payload.model_dump(exclude={"device_token"}, exclude_none=True),
            "device_token": mask_token(payload.device_token),
        }
        state.last_normalized = normalized
        state.last_received_at = utc_now_iso()
        relay1_command = state.relay1_command
        relay2_command = state.relay2_command

    return JSONResponse(
        {
            "accepted": True,
            "device_id": payload.device_id,
            "received_at": state.last_received_at,
            "relay1_command": relay1_command,
            "relay2_command": relay2_command,
            "normalized_fields": normalized,
        }
    )
