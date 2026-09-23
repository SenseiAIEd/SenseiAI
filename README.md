# SenseiAI

Sensei is a multilingual Socratic tutor: it reads a student's handwritten work, finds the
first step that went wrong, and asks a guiding question instead of giving the answer.
**Sensei Desk** is its physical form: a phone camera on a desk head that watches the
student work on paper and talks with them.

## Milestones (working back from the goal)

1. **Pipes** (done): the phone streams live video and the student's voice to the DGX Spark,
   the Spark records each session, and the Spark can make the phone speak.
2. **Eyes + Brain** (this milestone): a 5-15 minute session the student starts from the app.
   Sensei reads the notebook with a vision model on the Spark, stays quiet while the work is
   right, asks escalating Socratic questions about the first mistake, and wraps up at the end.
3. **Ears**: speech-to-text on the student's audio, so they can answer out loud.
4. **Body**: the pan-tilt head chooses where to look.

## Layout

| Path | What |
|---|---|
| `app/` | Sensei Cam: React Native (Expo) Android app for the phone ([README](app/README.md)) |
| `gateway/` | Gateway on the DGX Spark: WebRTC receiver, recorder, operator console ([README](gateway/README.md)) |
| `datasets/` | Handwritten-notes pages and sample solutions (good and one-mistake) for the tutor |

## Quick start

1. On the Spark: `cd gateway && uv venv && source .venv/bin/activate && uv pip install -r requirements.txt && uvicorn server:app --host 0.0.0.0 --port 8787`
2. Build and install the APK on the phone (see `app/README.md`), open it, enter the Spark address, tap **Connect**.
3. Open `http://<spark-address>:8787` in a browser to watch and type instructions for the phone to speak.
