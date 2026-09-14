# BMW CarData → Imou Camera

Standalone Raspberry Pi automation that reads BMW CarData location events and controls an Imou camera.

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

Behavior:

- first location inside target: Imou `Car`
- outside → inside target: Imou `Car`
- inside → outside target: Imou `ZoomOut`
- no transition: no camera request

BMW CarData publishes coordinates, not formatted street addresses. Configure target latitude, longitude, and radius in `.env`. MQTT streaming avoids BMW REST API daily quota.

## BMW authorization

BMW CarData uses OAuth 2.0 Device Authorization Grant with PKCE.

Prerequisites:

- BMW ConnectedDrive account with vehicle mapped to account as primary user.
- Active BMW CarData access.
- CarData API and CarData Stream subscriptions enabled.

Portal setup:

1. Open your regional My BMW portal and select vehicle → BMW CarData.
2. Create a new CarData client ID. Client ID is not BMW login email.
3. Request access to `CarData API` and `CarData Stream`; allow time for BMW permissions to propagate.
4. Configure stream descriptors and include:

   - `vehicle.cabin.infotainment.navigation.currentLocation.latitude`
   - `vehicle.cabin.infotainment.navigation.currentLocation.longitude`

Run device authorization from a machine with browser access:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -u scripts/authorize_bmw.py \
  --client-id YOUR_BMW_CLIENT_ID \
  --output data/bmw-tokens.json
```

Open printed BMW verification URL, enter user code if requested, and approve device. Helper polls BMW token endpoint and writes token JSON.

Token response contains `client_id`, `gcid`, `access_token`, `refresh_token`, and `id_token`. Service needs `client_id`, `gcid`, and `refresh_token`; it refreshes short-lived tokens and persists rotated state in Docker volume `/data/bmw-tokens.json`.

Never commit token JSON or `.env`.

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
| `TARGET_LATITUDE` | target latitude |
| `TARGET_LONGITUDE` | target longitude |
| `TARGET_RADIUS_METERS` | target geofence radius, e.g. `100` |
| `IMOU_BASE_URL` | Imou OpenAPI base URL |
| `IMOU_APP_ID` | Imou app ID |
| `IMOU_APP_SECRET` | Imou app secret |
| `IMOU_DEVICE_ID` | Imou camera device ID |
| `IMOU_CHANNEL_ID` | camera channel, usually `0` |

Optional `BMW_ID_TOKEN` can seed first connection. Service refreshes and persists current BMW tokens in Docker volume `/data/bmw-tokens.json`.

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

Set `IMOU_DRY_RUN=true` to validate BMW stream and geofence without moving camera. Set `false` for live camera actions.

## CarData Archive Explorer

The repository includes a separate local application for inspecting BMW CarData ZIP archives. It does not use BMW or Imou credentials and does not change the live camera automation.

Features:

- secure ZIP validation with path traversal, symbolic-link, size, and compression-ratio checks;
- immutable original archive copy identified by SHA-256;
- automatic CSV, JSON/NDJSON, and Parquet ingestion into DuckDB;
- complete file and field catalogue, including unsupported files;
- timestamp discovery, cross-dataset timeline, domain views, correlation windows, and maps;
- dedicated seat/profile investigation with state, error, constraint, command-source, and value summaries;
- raw record provenance, read-only SQL, and archive-to-archive schema comparison;
- redacted CSV export by default.

Run locally:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-explorer.txt
python -m archive_explorer serve
```

Open `http://127.0.0.1:8501` and upload the ZIP. For large archives, ingest from the command line first:

```bash
python -m archive_explorer ingest /path/to/bmw-cardata.zip
python -m archive_explorer serve
```

Run with Docker:

```bash
docker compose --profile archive-explorer up --build archive-explorer
```

Local Python mode stores explorer data under `data/archive-explorer/`, which is ignored by Git. Docker mode uses the persistent `bmw-cardata-archive-data` volume. The web port is bound to localhost only. Keep original archives, generated DuckDB files, screenshots, and raw exports out of source control because they may contain VIN, precise location, and personal telemetry.

Run tests:

```bash
pip install -r requirements-dev.txt
python -m pytest
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
| `PI_DEPLOY_PATH` | `/home/pi/repos/bmw-cardata-imou` |

Workflow assumes Pi user can write deployment path and run Docker. Deployment workflow is manual-only; run `Deploy Raspberry Pi` from GitHub Actions when deployment is desired.

BMW CarData limits: REST API access is limited to 50 requests per day, while this service uses MQTT streaming and makes no BMW REST vehicle-data calls. BMW allows one stream connection per GCID and monitors rapid connection attempts; the service disables MQTT library auto-reconnect and waits at least 60 seconds between application reconnect attempts.

## Recovery

If BMW authorization is revoked, run `authorize_bmw.py` again and replace `BMW_REFRESH_TOKEN`. Remove old token state before restart:

```bash
docker compose down
docker volume rm bmw-cardata-imou_bmw-cardata-state
docker compose up -d --build
```

This removes only persisted BMW token/state volume; `.env` remains.
