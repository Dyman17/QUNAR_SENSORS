# ESP Receiver Site

Separate mini-site for Render. This service accepts raw JSON packets from ESP32, normalizes the fields, keeps only the current packet in memory, shows it on a dashboard, and returns relay commands back to the device.

## Main endpoint for ESP

```text
POST /api/esp
```

## Unity endpoints (recommended)

Unity should not depend on raw ESP field names. Use these stable endpoints:

```text
GET  /api/unity/current
POST /api/unity/commands
```

## ESP-CAM video (MJPEG)

This service can store only the latest JPEG frame in memory and expose it as an MJPEG stream for browsers/Unity.

```text
POST /api/esp-cam/frame
GET  /api/video/stream.mjpeg
GET  /api/video/latest.jpg
GET  /api/video/status

# Unity aliases
GET  /api/unity/video
GET  /api/unity/video/stream.mjpeg
GET  /api/unity/video/latest.jpg
```

Upload a single JPEG frame (example):

```bash
curl -X POST "http://127.0.0.1:8000/api/esp-cam/frame?device_id=cam1" \
  -H "Content-Type: image/jpeg" \
  --data-binary "@frame.jpg"
```

Optional auth: set env var `ESP_CAM_TOKEN`, then send `?token=...` or header `X-ESP-CAM-Token`.

## AI analysis + chat (OpenAI)

Set env vars:

```text
OPENAI_API_KEY=...
OPENAI_MODEL=o4               # default in code
OPENAI_FALLBACK_MODEL=o4-mini # used if primary fails
AI_API_TOKEN=...              # optional: protect endpoints
```

Endpoints:

```text
POST /api/ai/analyze
POST /api/ai/chat
```

If `AI_API_TOKEN` is set, send header `X-AI-Token: ...` (or query `?ai_token=...`).

`/api/unity/current` returns:

- `sensors.air.temperature`, `sensors.air.humidity`
- `sensors.soil.humidity`, `sensors.soil.temperature`
- `relays.state.relay1/relay2` (если ESP присылает)
- `relays.command.relay1/relay2` (команды, которые сервер сейчас выдаёт)

`/api/unity/commands` accepts minimal JSON:

```json
{ "relay1": 0, "relay2": true }
```

Example packet:

```json
{
  "device_id": 3,
  "device_token": "test-token",
  "t1": 24.5,
  "h1": 61,
  "t2": 24.2,
  "h2": 60,
  "soilPercent": 47.1,
  "light1": 1,
  "light2": 1,
  "relay1": 1,
  "relay2": 0,
  "rssi": -63,
  "heap": 182344,
  "uptime": 5231,
  "fw": "1.1.0"
}
```

Example response:

```json
{
  "accepted": true,
  "device_id": 3,
  "received_at": "2026-04-22T00:00:00+00:00",
  "relay1_command": 1,
  "relay2_command": 0,
  "normalized_fields": {
    "device_id": 3,
    "device_token": "test-token",
    "temperature1": 24.5
  }
}
```

## Local run

```bash
cd esp-receiver-site
pip install -r requirements.txt
uvicorn app:app --reload
```

Open:

```text
http://127.0.0.1:8000
```

## Render deploy

You can deploy this folder as a separate Render web service.

- Root directory: `esp-receiver-site`
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`

Or use the included `render.yaml`.

Render Python version note:

- Render's default Python version can be newer than FastAPI/Pydantic wheels support.
- This repo pins Python with `.python-version` to `3.11.11`.
- If needed, you can also set Render env var `PYTHON_VERSION=3.11.11`.

## Notes

- No database is used.
- The service stores only the latest packet in process memory.
- If Render restarts the service, the dashboard state resets until the next ESP packet arrives.
