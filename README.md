# BMW CarData → Imou Camera

Local Raspberry Pi automation replacing old Azure Function App BMW polling.

Flow:

```text
BMW CarData MQTT stream
        ↓
latitude + longitude pairing
        ↓
target geofence transition
        ↓
Imou turnCollection
```

Behavior preserved from `cleanfileshare`:

- first location inside target: Imou `Car`
- outside → inside target: Imou `Car`
- inside → outside target: Imou `ZoomOut`
- no transition: no camera request

BMW CarData publishes coordinates, not formatted street addresses. Configure target latitude, longitude, and radius in `.env`. MQTT streaming avoids BMW REST API daily quota.

## BMW authorization

1. Create BMW CarData client ID.
2. Enable `CarData API` and `CarData Stream` scopes.
3. Configure stream descriptors and include:

   - `vehicle.cabin.infotainment.navigation.currentLocation.latitude`
   - `vehicle.cabin.infotainment.navigation.currentLocation.longitude`

4. Run device authorization from a machine with browser access:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python scripts/authorize_bmw.py --client-id YOUR_BMW_CLIENT_ID --output data/bmw-tokens.json
```

Open printed BMW URL, approve device. Token file contains `client_id`, `gcid`, and `refresh_token` needed by service.

## `.env`

Copy template:

```bash
cp .env.example .env
```

Set these values:

| Variable | Value |
|---|---|
| `BMW_CLIENT_ID` | BMW CarData client ID |
| `BMW_GCID` | `gcid` from `bmw-tokens.json` |
| `BMW_REFRESH_TOKEN` | `refresh_token` from `bmw-tokens.json` |
| `BMW_VIN` | 17-character uppercase VIN |
| `TARGET_LATITUDE` | latitude for target street |
| `TARGET_LONGITUDE` | longitude for target street |
| `TARGET_RADIUS_METERS` | geofence radius covering target street, e.g. `100` |
| `IMOU_APP_ID` | existing Imou app ID |
| `IMOU_APP_SECRET` | existing Imou app secret |
| `IMOU_DEVICE_ID` | existing camera device ID |
| `IMOU_CHANNEL_ID` | existing channel, old implementation uses `0` |

Optional `BMW_ID_TOKEN` only helps first connection. Service refreshes and persists current BMW tokens in Docker volume `/data/bmw-tokens.json`.

## Run locally

```bash
cp .env.example .env
# edit .env
docker compose up -d --build
docker compose logs -f bmw-cardata-imou
```

Test Imou without waiting for BMW event:

```bash
docker compose run --rm bmw-cardata-imou python -m app.cli Car
docker compose run --rm bmw-cardata-imou python -m app.cli ZoomOut
```

Start with `IMOU_DRY_RUN=true` to validate BMW stream and geofence without moving camera. Set `false` only after logs show BMW MQTT connection and location data.

Run tests:

```bash
pip install -r requirements-dev.txt
pytest
```

## Raspberry Pi deployment

Create deployment directory and `.env` on Pi once. Keep `.env` out of GitHub. GitHub Actions copies application files and runs Docker Compose.

Required GitHub repository secrets:

| Secret | Value |
|---|---|
| `PI_HOST` | Raspberry Pi hostname or IP |
| `PI_SSH_PORT` | `21` |
| `PI_USER` | Raspberry Pi SSH user |
| `PI_SSH_PASSWORD` | Raspberry Pi SSH password |
| `PI_DEPLOY_PATH` | `/repos/bmw-cardata-imou` |

Workflow assumes Pi user can write deployment path and run Docker. Push to `main` or run `Deploy Raspberry Pi` manually.

## Recovery

If BMW authorization is revoked, run `authorize_bmw.py` again and replace `BMW_REFRESH_TOKEN`. Remove old token state before restart:

```bash
docker compose down
docker volume rm bmw-cardata-imou_bmw-cardata-state
docker compose up -d --build
```

This removes only persisted BMW token/state volume; `.env` remains.
