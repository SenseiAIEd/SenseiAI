"""
Sensei's ears: turns the student's microphone audio (from the WebRTC call) into text.

  Ears         reads the audio track, finds each stretch of speech (energy-based voice
               activity detection), and hands the audio to a transcriber
  Transcriber  speech -> text: faster-whisper on the Spark's CPU by default, or any
               OpenAI-compatible /audio/transcriptions server (SENSEI_STT_URL)

The phone only sends real audio in voice mode (its mic is muted otherwise) and mutes
itself while Sensei is speaking, so Sensei never hears and answers its own voice.

Config:
  SENSEI_STT_MODEL     faster-whisper model (default small.en: base.en misheard 4 of 6 short math
                       answers in our test, small.en got all 6, ~1 s each on CPU; "small" or "large-v3" for
                       other languages such as Bengali)
  SENSEI_STT_LANGUAGE  e.g. "en", "bn" (default: detect)
  SENSEI_STT_URL       use a server instead, e.g. http://localhost:8020/v1 (vLLM Whisper)
  SENSEI_STT_KEY       its key, if any
"""
import asyncio
import io
import logging
import os
import re
import time
import wave
from typing import Awaitable, Callable, Optional

import av
import httpx
import numpy as np

log = logging.getLogger("sensei.ears")

RATE = 16000          # what speech-to-text models expect
FRAME_S = 0.02        # analysis step
START_FRAMES = 3      # 60 ms of speech starts an utterance
END_SILENCE_S = 0.8   # this much silence ends it (students pause to think; don't cut them off)
MIN_SPEECH_S = 0.3    # shorter blips (a cough, a tap) are ignored
MAX_SPEECH_S = 15.0   # a very long turn is cut here and transcribed
PREROLL_S = 0.3       # keep the moment just before speech was detected
MIN_LEVEL = 0.01      # RMS below this is never speech (a muted mic sends zeros)

# Whisper writes words even when nobody spoke: fed a breath, a chair or a quiet room it answers
# "Thank you.", "Okay.", "Silence." or "Thanks for watching!" (it learned them from subtitles).
# Replaying the recorded sessions through these ears (25 Sep, 144 utterances) found 35 such
# phantoms, and Sensei had answered some of them ("You're welcome! Let's get back to solving.").
# Two checks, tuned on that replay: they dropped all 35 and none of the real questions or answers.
#  1. Silero VAD (shipped with faster-whisper) must find at least MIN_VOICED_S of actual speech;
#     the energy detector above only knows that something was loud.
#  2. One of Whisper's stock phrases, with Whisper itself unsure anyone spoke (no_speech_prob),
#     is a phantom. A clearly spoken "thank you" (no_speech_prob 0.2 or less) still gets through.
MIN_VOICED_S = float(os.environ.get("SENSEI_STT_MIN_VOICED_S", 0.4))
PHANTOM_NO_SPEECH = 0.4
PHANTOMS = {"thank you", "thanks", "thank you very much", "thanks for watching", "thank you for watching",
            "bye", "bye bye", "goodbye", "you", "silence", "okay", "ok", "please", "so", "the end", "hmm",
            "uh", "um", "i", "yeah", "no", "perfect", "hello", "see you again", "see you next time"}


def voiced_seconds(audio: np.ndarray) -> float:
    """How much of the clip Silero VAD thinks is speech."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    stamps = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=300))
    return sum(s["end"] - s["start"] for s in stamps) / RATE


def is_phantom(text: str, no_speech_prob: float) -> bool:
    words = " ".join(re.findall(r"[a-z']+", text.lower()))
    return not words or (words in PHANTOMS and no_speech_prob >= PHANTOM_NO_SPEECH)


# A student pausing to think mid-sentence isn't done talking. The recordings have one sentence
# split in two at such pauses ("Yeah, this is the." / "I want to do."), and Sensei answered the
# first half. A transcript that ends on a word a sentence can't end on is held for HOLD_S; if
# the student carries on, the two are joined into one turn. Finished sentences aren't delayed.
HOLD_S = float(os.environ.get("SENSEI_STT_HOLD_S", 1.5))
DANGLING = {"and", "or", "but", "so", "because", "cause", "if", "then", "the", "a", "an", "to", "of",
            "in", "on", "at", "for", "with", "from", "by", "like", "is", "are", "was", "my", "your",
            "this", "that", "which", "what", "when", "where", "um", "uh", "equals", "plus", "minus",
            "times", "over", "than", "into"}


def sounds_unfinished(text: str) -> bool:
    t = text.strip()
    if t.endswith(("...", "\u2026", ",")):
        return True
    if t.endswith("?"):
        return False
    words = re.findall(r"[a-z0-9']+", t.lower())  # "x equals 5" ends on 5, not "equals"
    return bool(words) and words[-1] in DANGLING


class NotSpeech(Exception):
    """The transcriber decided nobody actually spoke; `reason` says why."""

    def __init__(self, reason: str, text: str = ""):
        super().__init__(reason)
        self.reason, self.text = reason, text


_MODELS: dict = {}  # loaded speech models, shared by every call in this process


class Transcriber:
    """Speech (16 kHz mono float32) -> text."""

    def __init__(self):
        self.url = os.environ.get("SENSEI_STT_URL", "").rstrip("/")
        self.key = os.environ.get("SENSEI_STT_KEY", "")
        self.model_name = os.environ.get("SENSEI_STT_MODEL", "small.en")
        self.language = os.environ.get("SENSEI_STT_LANGUAGE") or None
        self._model = None

    @property
    def name(self) -> str:
        return f"server {self.url}" if self.url else f"faster-whisper {self.model_name} (cpu)"

    def load(self):
        if not self.url and self._model is None:
            if self.model_name not in _MODELS:
                from faster_whisper import WhisperModel
                _MODELS[self.model_name] = WhisperModel(self.model_name, device="cpu", compute_type="int8")
            self._model = _MODELS[self.model_name]

    def __call__(self, audio: np.ndarray) -> str:
        if self.url:
            return self._via_server(audio)
        self.load()
        segments, _ = self._model.transcribe(audio, language=self.language, beam_size=1,
                                             vad_filter=False, condition_on_previous_text=False)
        segments = list(segments)
        text = " ".join(s.text.strip() for s in segments).strip()
        if segments and is_phantom(text, max(s.no_speech_prob for s in segments)):
            raise NotSpeech("stock phrase", text)
        return text

    def _via_server(self, audio: np.ndarray) -> str:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        data = {"model": self.model_name}
        if self.language:
            data["language"] = self.language
        res = httpx.post(f"{self.url}/audio/transcriptions", timeout=30, data=data,
                         headers={"Authorization": f"Bearer {self.key}"} if self.key else {},
                         files={"file": ("speech.wav", buf.getvalue(), "audio/wav")})
        res.raise_for_status()
        return (res.json().get("text") or "").strip()


class Ears:
    """Finds utterances in an audio track and transcribes each one."""

    def __init__(self, transcribe: Callable[[np.ndarray], str],
                 on_text: Callable[[str, dict], Awaitable[None]],
                 listening: Callable[[], bool] = lambda: True,
                 on_noise: Optional[Callable[[dict], None]] = None,
                 voiced: Callable[[np.ndarray], float] = voiced_seconds):
        self.transcribe = transcribe
        self.on_text = on_text
        self.on_noise = on_noise or (lambda info: None)  # something loud that wasn't speech
        self.voiced = voiced
        self.listening = listening  # voice mode on?
        self.resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        self.pending = np.zeros(0, np.float32)
        self.noise = MIN_LEVEL          # running estimate of the background level
        self.preroll: list[np.ndarray] = []
        self.speech: list[np.ndarray] = []
        self.voiced_run = 0
        self.silence_s = 0.0
        self.busy = False               # a transcription is running
        self.inflight = 0               # utterances waiting for or in transcription
        self.held: Optional[tuple[str, dict]] = None  # an unfinished sentence, waiting for the rest
        self._held_task: Optional[asyncio.Task] = None
        self._one_at_a_time = asyncio.Lock()  # utterances are transcribed in order, none dropped

    async def run(self, track):
        from aiortc.mediastreams import MediaStreamError
        while True:
            try:
                frame = await track.recv()
            except MediaStreamError:
                await self.flush()
                return
            for f in self.resampler.resample(frame):
                samples = f.to_ndarray().reshape(-1).astype(np.float32) / 32768.0
                await self.feed(samples)

    async def feed(self, samples: np.ndarray):
        """Push 16 kHz mono audio; call from the event loop."""
        self.pending = np.concatenate([self.pending, samples])
        step = int(RATE * FRAME_S)
        while len(self.pending) >= step:
            chunk, self.pending = self.pending[:step], self.pending[step:]
            await self._frame(chunk)

    async def _frame(self, chunk: np.ndarray):
        if not self.listening():
            self._reset()
            return
        level = float(np.sqrt(np.mean(chunk ** 2)))
        threshold = max(MIN_LEVEL, self.noise * 3)
        voiced = level > threshold
        if not self.speech:
            if not voiced:  # adapt to the room only while nobody is talking
                self.noise = 0.95 * self.noise + 0.05 * max(level, MIN_LEVEL / 3)
            self.preroll = (self.preroll + [chunk])[-int(PREROLL_S / FRAME_S):]
            self.voiced_run = self.voiced_run + 1 if voiced else 0
            if self.voiced_run >= START_FRAMES:
                self.speech = list(self.preroll)
                self.silence_s = 0.0
            return
        self.speech.append(chunk)
        self.silence_s = 0.0 if voiced else self.silence_s + FRAME_S
        length = len(self.speech) * FRAME_S
        if self.silence_s >= END_SILENCE_S or length >= MAX_SPEECH_S:
            audio = np.concatenate(self.speech)
            spoken_s = length - self.silence_s
            self._reset()
            if spoken_s >= MIN_SPEECH_S:
                self.inflight += 1
                asyncio.ensure_future(self._transcribe(audio, spoken_s))

    async def flush(self):
        """The audio ended: transcribe what was being said, if anything."""
        if self.speech:
            audio, spoken_s = np.concatenate(self.speech), len(self.speech) * FRAME_S - self.silence_s
            self._reset()
            if spoken_s >= MIN_SPEECH_S:
                self.inflight += 1
                await self._transcribe(audio, spoken_s)
        await self._release_held()

    def _reset(self):
        self.speech, self.preroll, self.voiced_run, self.silence_s = [], [], 0, 0.0

    async def _transcribe(self, audio: np.ndarray, spoken_s: float):
        try:
            await self._transcribe_one(audio, spoken_s)
        finally:
            self.inflight -= 1

    async def _transcribe_one(self, audio: np.ndarray, spoken_s: float):
        async with self._one_at_a_time:
            self.busy = True
            t0 = time.monotonic()
            info = {"audio_s": round(spoken_s, 2)}
            try:
                voiced = await asyncio.to_thread(self.voiced, audio)
                if voiced < MIN_VOICED_S:
                    raise NotSpeech("no voice")
                text = await asyncio.to_thread(self.transcribe, audio)
            except NotSpeech as e:
                self.on_noise({**info, "reason": e.reason, "text": e.text})
                return
            except Exception as e:
                log.warning("transcription failed: %s", e)
                return
            finally:
                self.busy = False
        if not text:
            return
        info["stt_s"] = round(time.monotonic() - t0, 2)
        if self.held is not None:  # the rest of a sentence they paused in
            before, before_info = self.held
            self.held = None
            if self._held_task is not None:
                self._held_task.cancel()
            text = f"{before} {text}"
            info = {**info, "audio_s": round(before_info["audio_s"] + info["audio_s"], 2), "joined": 2}
        if sounds_unfinished(text):
            self.held = (text, info)
            self._held_task = asyncio.ensure_future(self._deliver_held_later())
            return
        await self.on_text(text, info)

    async def _deliver_held_later(self):
        """Hand on a held sentence once the student has clearly stopped: HOLD_S of quiet, and
        nothing new still being said or transcribed (that would be joined to it instead)."""
        deadline, give_up = time.monotonic() + HOLD_S, time.monotonic() + HOLD_S + MAX_SPEECH_S + 30
        while time.monotonic() < give_up and (time.monotonic() < deadline or self.speech or self.inflight):
            await asyncio.sleep(0.05)
        self._held_task = None
        await self._release_held()

    async def _release_held(self):
        if self.held is not None:
            text, info = self.held
            self.held = None
            await self.on_text(text, info)
