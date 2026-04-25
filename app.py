from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from threading import Condition, Lock
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, ValidationError


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

ESP_PACKET_ALIASES = {
    "t1": "temperature1",
    "temp1": "temperature1",
    "temp": "temperature1",
    "temperature": "temperature1",
    "air_temp": "temperature1",
    "h1": "humidity1",
    "hum1": "humidity1",
    "humidity": "humidity1",
    "air_humidity": "humidity1",
    "humanity": "humidity1",
    "t2": "temperature2",
    "temp2": "temperature2",
    "tempSoil": "temperature2",
    "soil_temp": "temperature2",
    "temperature_soil": "temperature2",
    "h2": "humidity2",
    "hum2": "humidity2",
    "humiditySoil": "humidity2",
    "soilPercent": "soil",
    "soil_percent": "soil",
    "soil_moisture": "soil",
    "soil_humidity": "soil",
    "humanitySoil": "soil",
    "soil_humanity": "soil",
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


class UnityCommandsPayload(BaseModel):
    """
    Minimal commands payload for Unity clients.

    Intended JSON:
      {"device_id": 1, "device_token": "...", "relay1": 0|1, "relay2": true|false}
    """

    device_id: int | None = None
    device_token: str | None = None

    relay1: int | None = Field(None, ge=0, le=1)
    relay2: bool | None = None

    relay1_mode: str | None = Field(None, pattern="^(manual|auto)$")
    relay2_mode: str | None = Field(None, pattern="^(manual|auto)$")


class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(system|user|assistant)$")
    content: str = Field(..., min_length=1)


class AiChatPayload(BaseModel):
    messages: list[ChatMessage] | None = None
    message: str | None = None


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _compute_online_seconds(received_at_iso: str | None) -> tuple[bool, int | None]:
    received = _parse_iso_datetime(received_at_iso)
    if not received:
        return False, None
    now = datetime.now(timezone.utc)
    seconds = max(0, int((now - received).total_seconds()))
    return seconds <= 30, seconds


def _age_seconds(received_at_iso: str | None) -> int | None:
    received = _parse_iso_datetime(received_at_iso)
    if not received:
        return None
    now = datetime.now(timezone.utc)
    return max(0, int((now - received).total_seconds()))


def _unity_current_payload(dashboard_state: dict[str, Any]) -> dict[str, Any]:
    packet = dashboard_state.get("last_packet") or {}
    computed = dashboard_state.get("last_computed") or {}
    control = dashboard_state.get("control") or {}
    received_at = dashboard_state.get("last_received_at")
    online, seconds_ago = _compute_online_seconds(received_at)

    relay_state = (computed.get("relay_state") or {}) if isinstance(computed, dict) else {}

    sensors = {
        "air": {
            "temperature": packet.get("temperature1"),
            "humidity": packet.get("humidity1"),
        },
        "soil": {
            "humidity": packet.get("soil"),
            "temperature": packet.get("temperature2"),
        },
        "light": {
            "l1": packet.get("light1"),
            "l2": packet.get("light2"),
        },
        "wifi": {
            "rssi": packet.get("wifi_rssi"),
        },
    }

    relays = {
        "state": {
            "relay1": packet.get("relay1_state"),
            "relay2": packet.get("relay2_state"),
        },
        "command": {
            "relay1": relay_state.get("relay1_command"),
            "relay2": relay_state.get("relay2_command"),
        },
        "mode": {
            "relay1": control.get("relay1_mode"),
            "relay2": control.get("relay2_mode"),
        },
    }

    return {
        "ok": True,
        "received_at": received_at,
        "online": online,
        "seconds_ago": seconds_ago,
        "device_id": packet.get("device_id"),
        "sensors": sensors,
        "relays": relays,
    }
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

        self.video_lock = Lock()
        self.video_cond = Condition(self.video_lock)
        self.video_seq: int = 0
        self.last_jpeg: bytes | None = None
        self.last_jpeg_at: str | None = None
        self.last_jpeg_device_id: str | None = None

        self.ai_lock = Lock()
        self.last_ai_analysis: dict[str, Any] | None = None
        self.last_ai_analysis_at: str | None = None


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
        relay_state, meta = compute_commands(state.last_packet)
        state.last_computed = {"relay_state": relay_state.model_dump(), **meta}
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/commands")
def update_commands_api(config: ControlConfig):
    with state.lock:
        state.control = config
        relay_state, meta = compute_commands(state.last_packet)
        state.last_computed = {"relay_state": relay_state.model_dump(), **meta}
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


@app.get("/api/unity/current")
def unity_current():
    dashboard_state = current_dashboard_state()
    return _unity_current_payload(dashboard_state)


@app.post("/api/unity/commands")
def unity_commands(payload: UnityCommandsPayload):
    if payload.relay1 is None and payload.relay2 is None and payload.relay1_mode is None and payload.relay2_mode is None:
        raise HTTPException(status_code=400, detail="No commands provided.")

    with state.lock:
        control = state.control.model_copy(deep=True)

        if payload.relay1_mode is not None:
            control.relay1_mode = payload.relay1_mode
        if payload.relay2_mode is not None:
            control.relay2_mode = payload.relay2_mode

        # Default to manual when sending direct relay commands.
        if payload.relay1 is not None:
            control.relay1_mode = "manual"
            control.relay1_manual_command = int(payload.relay1)
        if payload.relay2 is not None:
            control.relay2_mode = "manual"
            control.relay2_manual_command = 1 if bool(payload.relay2) else 0

        state.control = control

        relay_state, meta = compute_commands(state.last_packet)
        state.last_computed = {"relay_state": relay_state.model_dump(), **meta}

    return {
        "accepted": True,
        "relay1_command": relay_state.relay1_command,
        "relay2_command": relay_state.relay2_command,
        "control": state.control.model_dump(),
    }


def _require_esp_cam_token(request: Request) -> None:
    expected = os.getenv("ESP_CAM_TOKEN")
    if not expected:
        return
    token = request.headers.get("x-esp-cam-token") or request.query_params.get("token")
    if not token or token != expected:
        raise HTTPException(status_code=401, detail="Invalid ESP-CAM token.")


def _require_ai_token(request: Request) -> None:
    expected = os.getenv("AI_API_TOKEN")
    if not expected:
        return
    token = request.headers.get("x-ai-token") or request.query_params.get("ai_token")
    if not token or token != expected:
        raise HTTPException(status_code=401, detail="Invalid AI token.")


def _get_openai_client():
    try:
        from openai import OpenAI
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OpenAI library missing: {exc}")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured.")
    return OpenAI()


def _openai_model_id() -> tuple[str, str]:
    primary = os.getenv("OPENAI_MODEL", "o4")
    fallback = os.getenv("OPENAI_FALLBACK_MODEL", "o4-mini")
    return primary, fallback


def _response_json(resp) -> dict[str, Any]:
    text = getattr(resp, "output_text", None)
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        return {}


@app.post("/api/esp-cam/frame")
async def receive_esp_cam_frame(request: Request):
    """
    Accept a single JPEG frame and store only the latest one in memory.

    Supported content-types:
      - image/jpeg (raw body)
      - application/octet-stream (raw body)
      - multipart/form-data (field name: frame|file|image)

    Optional auth:
      - set env var ESP_CAM_TOKEN and send ?token=... or header X-ESP-CAM-Token
    """

    _require_esp_cam_token(request)

    device_id = request.query_params.get("device_id") or request.headers.get("x-device-id")
    content_type = (request.headers.get("content-type") or "").lower()
    jpeg: bytes | None = None

    if content_type.startswith("image/jpeg") or content_type.startswith("application/octet-stream"):
        jpeg = await request.body()
    elif content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("frame") or form.get("file") or form.get("image")
        if upload is not None and hasattr(upload, "read"):
            jpeg = await upload.read()
    else:
        raise HTTPException(status_code=415, detail="Expected image/jpeg, application/octet-stream, or multipart/form-data.")

    if not jpeg or len(jpeg) < 10:
        raise HTTPException(status_code=400, detail="Empty JPEG payload.")

    received_at = utc_now_iso()
    with state.video_cond:
        state.last_jpeg = jpeg
        state.last_jpeg_at = received_at
        state.last_jpeg_device_id = str(device_id) if device_id is not None else None
        state.video_seq += 1
        state.video_cond.notify_all()

    return JSONResponse({"accepted": True, "received_at": received_at, "bytes": len(jpeg)})


@app.post("/api/ai/analyze")
def ai_analyze(request: Request):
    """
    Analyze the latest dashboard JSON via OpenAI and return a structured JSON verdict.

    Optional auth:
      - set env var AI_API_TOKEN and send ?ai_token=... or header X-AI-Token
    """

    _require_ai_token(request)

    dashboard_state = current_dashboard_state()
    packet = dashboard_state.get("last_packet")
    if not packet:
        raise HTTPException(status_code=404, detail="No ESP packet received yet.")

    video = video_status()

    client = _get_openai_client()
    model, fallback = _openai_model_id()

    schema = {
        "type": "object",
        "properties": {
            "score": {"type": "integer", "description": "0..100 overall score of system health and data quality."},
            "verdict": {"type": "string", "description": "Short honest verdict in Russian."},
            "anomalies": {"type": "array", "items": {"type": "string"}, "description": "Detected anomalies or risks."},
            "recommendations": {"type": "array", "items": {"type": "string"}, "description": "Concrete next actions."},
            "confidence": {"type": "number", "description": "0..1 confidence in the assessment."},
        },
        "required": ["score", "verdict", "anomalies", "recommendations", "confidence"],
        "additionalProperties": False,
    }

    system_msg = (
        "Ты честный инженер-аналитик IoT/датчиков. "
        "Оцени качество данных и состояние системы по последнему JSON. "
        "Не выдумывай факты. Если данных мало — так и скажи. "
        "Ответ строго в JSON по схеме."
    )

    input_items = [
        {"role": "system", "content": system_msg},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "last_received_at": dashboard_state.get("last_received_at"),
                    "packet": packet,
                    "computed": dashboard_state.get("last_computed"),
                    "control": dashboard_state.get("control"),
                    "video": video,
                },
                ensure_ascii=False,
            ),
        },
    ]

    def call(model_id: str):
        return client.responses.create(
            model=model_id,
            input=input_items,
            text={"format": {"type": "json_schema", "name": "sensor_analysis", "schema": schema, "strict": True}},
        )

    try:
        resp = call(model)
    except Exception:
        resp = call(fallback)
        model = fallback

    analysis = _response_json(resp)
    if not analysis:
        raise HTTPException(status_code=502, detail="OpenAI returned non-JSON output.")

    received_at = utc_now_iso()
    with state.ai_lock:
        state.last_ai_analysis = analysis
        state.last_ai_analysis_at = received_at

    return {"ok": True, "model": model, "generated_at": received_at, "analysis": analysis}


@app.post("/api/ai/chat")
def ai_chat(payload: AiChatPayload, request: Request):
    """
    Chat bot (OpenAI) with current dashboard context.

    Optional auth:
      - set env var AI_API_TOKEN and send ?ai_token=... or header X-AI-Token
    """

    _require_ai_token(request)

    dashboard_state = current_dashboard_state()
    packet = dashboard_state.get("last_packet")
    video = video_status()

    if payload.messages is None:
        if not payload.message:
            raise HTTPException(status_code=400, detail="Provide 'message' or 'messages'.")
        messages = [ChatMessage(role="user", content=payload.message)]
    else:
        messages = payload.messages
        if not messages:
            raise HTTPException(status_code=400, detail="Empty messages.")

    client = _get_openai_client()
    model, fallback = _openai_model_id()

    system_msg = (
        "Ты помощник для системы QUNAR Sensors. "
        "Отвечай на русском, кратко и по делу. "
        "Если спрашивают про датчики/реле/видео — опирайся на текущий JSON состояния. "
        "Если данных нет — скажи что нет."
    )

    context = json.dumps(
        {
            "last_received_at": dashboard_state.get("last_received_at"),
            "packet": packet,
            "computed": dashboard_state.get("last_computed"),
            "control": dashboard_state.get("control"),
            "video": video,
        },
        ensure_ascii=False,
    )

    input_items: list[dict[str, Any]] = [
        {"role": "system", "content": system_msg},
        {"role": "system", "content": f"Текущее состояние (JSON): {context}"},
    ]
    input_items.extend([m.model_dump() for m in messages])

    def call(model_id: str):
        return client.responses.create(model=model_id, input=input_items)

    try:
        resp = call(model)
    except Exception:
        resp = call(fallback)
        model = fallback

    reply = getattr(resp, "output_text", "") or ""
    if not reply.strip():
        raise HTTPException(status_code=502, detail="OpenAI returned empty output.")

    return {"ok": True, "model": model, "reply": reply}


@app.get("/api/video/status")
def video_status():
    with state.video_lock:
        received_at = state.last_jpeg_at
        size = len(state.last_jpeg) if state.last_jpeg is not None else None
        device_id = state.last_jpeg_device_id

    age_sec = _age_seconds(received_at)
    return {
        "has_frame": received_at is not None,
        "last_received_at": received_at,
        "age_sec": age_sec,
        "bytes": size,
        "device_id": device_id,
    }


@app.get("/api/video/latest.jpg")
def video_latest_jpeg():
    with state.video_lock:
        jpeg = state.last_jpeg
        received_at = state.last_jpeg_at

    if not jpeg:
        raise HTTPException(status_code=404, detail="No frame received yet.")

    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
    }
    if received_at:
        headers["X-Frame-Received-At"] = received_at
    return Response(content=jpeg, media_type="image/jpeg", headers=headers)


def _mjpeg_generator(boundary: str, keepalive_sec: float = 2.0):
    last_seq = -1
    last_send = 0.0
    while True:
        try:
            with state.video_cond:
                if state.last_jpeg is None:
                    state.video_cond.wait(timeout=1.0)
                    continue

                now_mono = time.monotonic()
                timeout = max(0.1, keepalive_sec - (now_mono - last_send))
                if state.video_seq == last_seq:
                    state.video_cond.wait(timeout=timeout)

                jpeg = state.last_jpeg
                received_at = state.last_jpeg_at
                seq = state.video_seq

            if jpeg is None:
                continue

            now_mono = time.monotonic()
            if seq == last_seq and (now_mono - last_send) < keepalive_sec:
                continue

            head = (
                f"--{boundary}\r\n"
                f"Content-Type: image/jpeg\r\n"
                f"Content-Length: {len(jpeg)}\r\n"
            ).encode("utf-8")
            if received_at:
                head += f"X-Frame-Received-At: {received_at}\r\n".encode("utf-8")
            head += b"\r\n"

            yield head + jpeg + b"\r\n"

            last_seq = seq
            last_send = time.monotonic()
        except GeneratorExit:
            return
        except Exception:
            return


def _video_stream_response():
    boundary = "frame"
    return StreamingResponse(
        _mjpeg_generator(boundary=boundary),
        media_type=f"multipart/x-mixed-replace; boundary={boundary}",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"},
    )


@app.get("/api/video/stream.mjpeg")
def video_stream_mjpeg():
    return _video_stream_response()


@app.get("/api/video/stream")
def video_stream_alias():
    return _video_stream_response()


@app.get("/api/unity/video/stream.mjpeg")
def unity_video_stream_mjpeg():
    return _video_stream_response()


@app.get("/api/unity/video/latest.jpg")
def unity_video_latest_jpeg():
    return video_latest_jpeg()


@app.get("/api/unity/video")
def unity_video_default():
    return _video_stream_response()


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
