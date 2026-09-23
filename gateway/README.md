# Sensei gateway (DGX Spark)

Receives the live call from the Sensei Cam app, records it, and lets you speak through
the phone.

```
Pixel (Sensei Cam)  --WebRTC: camera video + mic audio-->  gateway on the Spark
                    <--data channel: "say" instructions---   |- sessions/<time>/session.mp4
                                                             |- sessions/<time>/log.jsonl
                                                             '- console at http://<spark>:8787
```

The vision model will run on the same box. Later milestones read `Session.latest_frame`
and call `/say` themselves instead of a person typing.

## Run

```sh
cd gateway
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8787
```

Open `http://<spark-address>:8787` in a browser on any machine that can reach the Spark
(for example over Tailscale: `http://spark-e257.tail803c7f.ts.net:8787`). You get:

- a live preview of the phone camera,
- a box to type an instruction, which the phone speaks aloud, plus quick-instruction buttons,
- **Stop speaking** and **End session** buttons.

Each call is recorded to `sessions/<start time>/`:

- `session.mp4`: the full video and the student's audio,
- `log.jsonl`: every event, timed in seconds from the start of the recording
  (`say`, `phone:spoken`, `connection`, ...), so instructions can be lined up with the video.

## Networking

- **Same Wi-Fi or hotspot** (the demo setup, works with no internet): the phone uses the Spark's LAN IP.
- **Tailscale** (convenient for testing from anywhere): install Tailscale on the phone, join
  the same tailnet, and use the Spark's MagicDNS name. WebRTC media flows over the tailnet
  directly. Tailscale Funnel only carries HTTPS, not WebRTC media, so the phone must be on
  the tailnet, not just able to open the public URL.
- Open port 8787 (TCP) and allow UDP between phone and Spark if a firewall is on.

## Test without the phone

```sh
python fake_phone.py --server http://localhost:8787 --seconds 30   # streams a sample page
pytest                                                             # end-to-end test
```

`fake_phone.py` makes the same WebRTC call the app does, so the console, recording and
`/say` can all be checked before the phone is set up.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/offer` | Phone's WebRTC offer in, answer out; starts a session (replaces any old one) |
| POST | `/say` | `{"text": "..."}` -> spoken on the phone (409 if no phone) |
| POST | `/hush` | Stop the phone speaking |
| POST | `/hangup` | End the session and finalize the recording |
| GET | `/status` | Connection state, tracks, fps, recording path, last spoken line |
| GET | `/snapshot.jpg` | Latest camera frame |
| GET | `/preview.mjpg` | Live MJPEG preview (about 8 fps) |
