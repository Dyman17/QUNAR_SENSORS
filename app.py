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


class ControlConfig(BaseModel):
    relay1_mode: str = Field("manual", pattern="^(manual|auto)$")
    relay2_mode: str = Field("manual", pattern="^(manual|auto)$")

    relay1_manual_command: int = Field(0, ge=0, le=1)
    relay2_manual_command: int = Field(0, ge=0, le=1)

    # Auto pump settings (soil is expected to be percent: 0..100, higher = wetter)
    pump_on_below: float = Field(30.0, ge=0.0, le=100.0)
    pump_off_above: float = Field(35.0, ge=0.0, le=100.0)
    pump_max_on_sec: int = Field(60, ge=5, le=3600)

    # Auto lamp settings (light sensors are expected 0/1 where 0 = dark)
    lamp_on_when_dark: bool = True


class ReceiverState:
    def __init__(self) -> None:
        self.lock = Lock()
        self.control = ControlConfig()
        self.last_computed: dict[str, Any] | None = None
        self._pump_on_since: datetime | None = None
        self._pump_last_command: int = 0
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
        return {
            "control": state.control.model_dump(),
            "last_packet": state.last_packet,
            "last_normalized": state.last_normalized,
            "last_received_at": state.last_received_at,
            "last_computed": state.last_computed,
        }


def _compute_auto_pump_command(packet: dict[str, Any] | None, now: datetime, control: ControlConfig) -> int:
    if not packet or packet.get("soil") is None:
        state._pump_on_since = None
        state._pump_last_command = 0
        return 0

    try:
        soil = float(packet["soil"])
    except Exception:
        soil = None

    if soil is None:
        state._pump_on_since = None
        state._pump_last_command = 0
        return 0

    desired = state._pump_last_command
    if desired == 0 and soil < control.pump_on_below:
        desired = 1
        state._pump_on_since = now
    elif desired == 1 and soil > control.pump_off_above:
        desired = 0
        state._pump_on_since = None

    if desired == 1 and state._pump_on_since is not None:
        if (now - state._pump_on_since).total_seconds() >= control.pump_max_on_sec:
            desired = 0
            state._pump_on_since = None

    state._pump_last_command = desired
    return desired


def _compute_auto_lamp_command(packet: dict[str, Any] | None, control: ControlConfig) -> int:
    if not packet:
        return 0
    l1 = packet.get("light1")
    l2 = packet.get("light2")
    if l1 is None or l2 is None:
        return 0
    try:
        l1i = int(l1)
        l2i = int(l2)
    except Exception:
        return 0

    dark = (l1i == 0) or (l2i == 0)
    if control.lamp_on_when_dark:
        return 1 if dark else 0
    return 0 if dark else 1


def compute_commands(packet: dict[str, Any] | None) -> tuple[RelayState, dict[str, Any]]:
    now = datetime.now(timezone.utc)
    control = state.control

    relay1_cmd = control.relay1_manual_command if control.relay1_mode == "manual" else _compute_auto_pump_command(packet, now, control)
    relay2_cmd = control.relay2_manual_command if control.relay2_mode == "manual" else _compute_auto_lamp_command(packet, control)

    meta = {
        "relay1_mode": control.relay1_mode,
        "relay2_mode": control.relay2_mode,
        "computed_at": now.isoformat(),
    }
    return RelayState(relay1_command=relay1_cmd, relay2_command=relay2_cmd), meta


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    dashboard_state = current_dashboard_state()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "control": dashboard_state["control"],
            "last_packet": dashboard_state["last_packet"],
            "last_normalized": dashboard_state["last_normalized"],
            "last_received_at": dashboard_state["last_received_at"],
            "last_computed": dashboard_state["last_computed"],
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
    relay1_mode: str = Form(...),
    relay2_mode: str = Form(...),
    relay1_manual_command: int = Form(...),
    relay2_manual_command: int = Form(...),
    pump_on_below: float = Form(...),
    pump_off_above: float = Form(...),
    pump_max_on_sec: int = Form(...),
    lamp_on_when_dark: int = Form(1),
):
    try:
        config = ControlConfig(
            relay1_mode=relay1_mode,
            relay2_mode=relay2_mode,
            relay1_manual_command=relay1_manual_command,
            relay2_manual_command=relay2_manual_command,
            pump_on_below=pump_on_below,
            pump_off_above=pump_off_above,
            pump_max_on_sec=pump_max_on_sec,
            lamp_on_when_dark=bool(int(lamp_on_when_dark)),
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors())

    with state.lock:
        state.control = config
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/commands")
def update_commands_api(config: ControlConfig):
    with state.lock:
        state.control = config
    return {"saved": True, **config.model_dump()}


@app.get("/api/current")
def get_current_state():
    dashboard_state = current_dashboard_state()
    return {
        "control": dashboard_state["control"],
        "last_received_at": dashboard_state["last_received_at"],
        "packet": dashboard_state["last_packet"],
        "normalized_fields": dashboard_state["last_normalized"],
        "computed": dashboard_state["last_computed"],
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
        relay_state, meta = compute_commands(state.last_packet)
        state.last_computed = {"relay_state": relay_state.model_dump(), **meta}

    return JSONResponse(
        {
            "accepted": True,
            "device_id": payload.device_id,
            "received_at": state.last_received_at,
            "relay1_command": relay_state.relay1_command,
            "relay2_command": relay_state.relay2_command,
            "relay1_mode": state.control.relay1_mode,
            "relay2_mode": state.control.relay2_mode,
            "normalized_fields": normalized,
        }
    )
