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

With [uv](https://docs.astral.sh/uv/) (install it once with `curl -LsSf https://astral.sh/uv/install.sh | sh`):

```sh
cd gateway
uv venv                                  # creates .venv
source .venv/bin/activate
uv pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8787
```

Or with plain pip: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.

Keep it running after you close the terminal with `tmux`, or run it in the background.

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

Three ways for the phone to reach the gateway:

| Mode | Phone needs | App server field | Internet |
|---|---|---|---|
| Same Wi-Fi / hotspot (the Friday demo) | nothing | `http://<spark-LAN-ip>:8787` | not needed |
| Tailscale app on the phone | Tailscale, same tailnet | `http://spark-e257.tail803c7f.ts.net:8787` | needed |
| **Funnel + TURN relay** (from anywhere) | nothing | `https://spark-e257.tail803c7f.ts.net:8443` + access key | needed |

### From anywhere: Funnel + TURN relay

Funnel only carries TCP, but WebRTC media is UDP straight between phone and Spark. So the
gateway also runs a TURN relay (coturn): the phone sends its media to the relay over TLS
through Funnel, and the relay hands it to the gateway on the Spark.

```
phone --HTTPS :8443--> Funnel --> gateway :8787          (call setup, /config, console)
phone --TLS   :10000-> Funnel --> coturn  :3478 --UDP--> gateway   (video + audio)
```

On the Spark, once:

```sh
sudo apt install coturn
sudo systemctl disable --now coturn   # we run coturn ourselves with our config, not the stock service
cd gateway
./setup_funnel.sh spark-e257.tail803c7f.ts.net
# or reuse an existing key:  SENSEI_KEY=<your key> ./setup_funnel.sh spark-e257.tail803c7f.ts.net
sudo tailscale funnel --bg --https=8443 http://localhost:8787
sudo tailscale funnel --bg --tls-terminated-tcp=10000 tcp://localhost:3478
tailscale funnel status
```

`setup_funnel.sh` writes `sensei.env` (access key, TURN secret, TURN address) and
`turnserver.conf`, both gitignored, and prints the phone settings. Then run, each in its own
tmux window:

```sh
turnserver -c turnserver.conf
set -a; source sensei.env; set +a; uvicorn server:app --host 0.0.0.0 --port 8787
```

- **Access key**: with `SENSEI_KEY` set, every request needs it (`X-Sensei-Key` header,
  `Authorization: Bearer`, or `?key=`), because the gateway is on the public internet.
  Open the console at `https://spark-e257.tail803c7f.ts.net:8443/?key=<key>`.
- **Relay lock-down**: coturn listens only on 127.0.0.1 (reachable only via Funnel), accepts
  only short-lived passwords the gateway issues (`/config`, valid 6 h), and relays only to
  the Spark's own LAN address, so it can't be used as an open relay.
- **Check the path**: the console and `/status` show `route`: `relay <ip>:<port 49160-49200>`
  means through coturn; `host ...` means direct.
- Trade-offs: every frame goes via Tailscale's Funnel servers (more delay), and TCP
  retransmits lost packets (video can stutter on bad networks). Use the LAN mode for the demo.

Test it without the phone, from a machine outside the Spark's network (on the Spark itself
the call would just go direct): `python fake_phone.py --server https://spark-e257.tail803c7f.ts.net:8443 --key <key>`,
then check that the console shows `via relay ...`.

### Firewall

If `ufw` is on: allow TCP 8787 from the LAN/tailnet for the direct modes, and UDP between
the phone and the Spark. The relay mode needs nothing extra (coturn and the gateway talk
on the Spark itself).

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
| GET | `/config` | ICE servers for the phone: the TURN relay with short-lived credentials, or `[]` |
| GET | `/status` | Connection state, route (direct or relay), tracks, fps, recording path, last spoken line |
| GET | `/snapshot.jpg` | Latest camera frame |
| GET | `/preview.mjpg` | Live MJPEG preview (about 8 fps) |
