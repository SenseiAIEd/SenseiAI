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

## The tutor (Sensei leads the session)

When the student taps **Start** in the app (5, 10 or 15 minutes), `tutor.py` runs the session:

1. Greets the student and asks them to put the notebook under the camera.
2. Watches the page. When it has settled (hand lifted) and has new writing, it sends that
   frame to the vision model, which returns JSON: the problem, the steps, the first wrong
   step, and a spoken sentence.
3. The code decides whether to speak (the model never decides on its own):
   - new mistake: hint level 1 (point at the line); the same mistake still there later: level 2
     (name the idea), then 3 (very specific, never the answer); at most one unprompted remark every 8 s
   - mistake fixed: short acknowledgement; finished and correct: praise, and ask them to explain
   - correct so far: stays quiet
4. The student's buttons: **Hint** and **Check my work** make it look now; **Repeat**; **End**.
5. One-minute warning, idle check-in after 90 s without writing, and at the end a short
   spoken summary (from the model) plus stats on the phone.

Every decision is logged to the session's `log.jsonl` (`tutor_assessment` with the model's
full reading and latency, `tutor_say` with why it spoke), next to the video.

### Eyes, ears, mouth

- **Eyes**: the student taps **What do you see?** and Sensei describes what's in view. The
  console's *Sensei's eyes* panel shows the exact frame the model last looked at
  (`judged_NNN.jpg` in the session folder) next to what it read and said, and how long it took.
  Anything in view counts, not just homework.
- **Ears**: the phone's mic is muted until the student turns on **voice mode**, and it mutes
  itself while Sensei talks. In voice mode the Spark detects each utterance, transcribes it
  (`ears.py`: faster-whisper `small.en` on the CPU, about 1 s per answer; it got 6/6 short
  math answers right where `base.en` got 2/6), shows "You said: ..." on the phone, and
  answers using the current camera frame too. `SENSEI_STT_MODEL`, `SENSEI_STT_LANGUAGE`
  (e.g. `bn`), or `SENSEI_STT_URL` for a GPU Whisper server; `SENSEI_STT=off` disables it.
- **Mouth**: the phone speaks whatever Sensei says (Android text-to-speech, offline).

Test the ears without a phone: `python fake_phone.py --tutor 5 --say question.wav`.

### Point it at the Spark's vision model

Any OpenAI-compatible `/v1/chat/completions` endpoint that accepts images works (vLLM,
llama.cpp server, Ollama, LiteLLM, your router). Add to `sensei.env` (or export):

```sh
SENSEI_LLM_URL=http://localhost:<port>/v1       # the model server on the Spark, not Open WebUI
SENSEI_LLM_MODEL=qwen3-vl-30b-a3b-gguf           # as listed by <url>/models
SENSEI_LLM_KEY=<key, if the server wants one>
```

Find it: `curl http://localhost:<port>/v1/models -H "Authorization: Bearer <key>"` should list
the model. Without `SENSEI_LLM_URL` the session still runs, but Sensei says its thinking part
isn't connected.

### Which model

Use a **thinking** (reasoning) vision model. Finding the first wrong line means checking every
line against the one above it; in our earlier 14-model benchmark the same Qwen3-VL weights
found 2/8 planted mistakes as *instruct* and 8/8 as *thinking*.

| Candidate on the Spark | Earlier benchmark (via OpenRouter) | Notes |
|---|---|---|
| `qwen3-vl-30b-a3b-thinking` | 8/8 mistakes, 0 false alarms, ~22 s | the default pick |
| `cosmos-reason2-8b` / `-32b` | not tested | Qwen3-VL-Instruct post-trained by NVIDIA for long chain-of-thought (physical/video reasoning); could be the fast reasoner, untested on algebra |
| `glm-4.6v-awq-4bit` | 7/8, ~55 s | best at pointing to the exact line; slow |
| `qwen3-vl-30b-a3b-gguf` (instruct) | 2/8 | fast but misses most mistakes: don't use for tutoring |

Thinking models reason before answering, so replies need room: `SENSEI_LLM_MAX_TOKENS`
(default 4096) and `SENSEI_LLM_TIMEOUT` (default 120 s). Reasoning in `<think>` tags, a
`reasoning_content` field (vLLM `--reasoning-parser`), or Cosmos's `<answer>` tags are all
handled. While the model thinks, Sensei answers Hint/Check taps with "Let me look at your work."

Race candidates on the sample pages (the router's model swap is excluded from the timing):

```sh
python eval_brain.py --models qwen3-vl-30b-a3b-thinking,cosmos-reason2-8b,cosmos-reason2-32b
```

### Score the model first

```sh
set -a; source sensei.env; set +a
python eval_brain.py --limit 6          # quick look
python eval_brain.py                    # all sample solutions
```

For each sample page: did it catch the one planted mistake in `bad_N`, did it leave the
correct `good_N` alone, and how long it took. Compare the flagged line with the answer key in
`datasets/samples/<subject>/README.md`. Aim for most mistakes caught, no false alarms on good
work, and a few seconds per page. If a model is slow or wrong, try another from the router.

### Try a session without the phone

```sh
python fake_phone.py --tutor 5 --seconds 120      # streams a sample page and taps Start
```

Or use the **Start / Hint / Check / End** buttons in the console while any phone is connected.

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
| POST | `/tutor` | `{"action": "start" \| "hint" \| "check" \| "repeat" \| "end", "minutes": 10}`, like the phone's buttons |
| POST | `/hush` | Stop the phone speaking |
| POST | `/hangup` | End the session and finalize the recording |
| GET | `/config` | ICE servers for the phone: the TURN relay with short-lived credentials, or `[]` |
| GET | `/status` | Connection state, route (direct or relay), tracks, fps, recording path, last spoken line |
| GET | `/snapshot.jpg` | Latest camera frame |
| GET | `/preview.mjpg` | Live MJPEG preview (about 8 fps) |
