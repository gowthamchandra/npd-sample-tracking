# Sample Tracking Console

Local rebuild of the Retool sample tracking app.

## Run

```bash
python3 server.py
```

Open:

- `http://127.0.0.1:8000/login.html`
- post-login app: `http://127.0.0.1:8000/`

Demo users (editable in `server.py`):

- `admin` / `Admin@123`
- `quality` / `Quality@123`
- `logistics` / `Logistics@123`
- `marketing` / `Marketing@123`

## Included flows

- Create and review lots
- Add lab analyses per lot
- Create shipments and update delivery status
- Log customer feedback for delivered shipments

The app uses a local SQLite database file: `sample_tracking.db`.

## Deploy (Render)

This repo includes `render.yaml` and a `Dockerfile`.

1. In Render: New → Blueprint
2. Select this GitHub repo
3. Deploy. Render mounts a persistent disk and stores the SQLite DB at `/var/data/sample_tracking.db`.

## Demo seed data

By default, a fresh database starts empty. To seed demo data on first boot, set `SEED_DEMO_DATA=1` before running.
