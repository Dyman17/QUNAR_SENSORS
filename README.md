# ESP Receiver Site

Separate mini-site for Render. This service accepts raw JSON packets from ESP32, normalizes the fields, keeps only the current packet in memory, shows it on a dashboard, and returns relay commands back to the device.

## Main endpoint for ESP

```text
POST /api/esp
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

## Notes

- No database is used.
- The service stores only the latest packet in process memory.
- If Render restarts the service, the dashboard state resets until the next ESP packet arrives.
