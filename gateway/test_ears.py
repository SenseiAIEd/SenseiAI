"""Ears: voice activity detection + real Whisper on a synthetic student voice. Run: pytest gateway/"""
import asyncio
import shutil
import subprocess

import numpy as np
import pytest

from ears import RATE, Ears, Transcriber

pytestmark = pytest.mark.skipif(shutil.which("espeak-ng") is None, reason="needs espeak-ng for a test voice")


def spoken(text: str, tmp_path) -> np.ndarray:
    """Synthesize `text` and return 16 kHz mono float32 samples."""
    wav = tmp_path / "say.wav"
    subprocess.run(["espeak-ng", "-v", "en-us", "-s", "150", "-w", str(wav), text], check=True)
    import av
    out = []
    with av.open(str(wav)) as f:
        rs = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        for frame in f.decode(audio=0):
            for r in rs.resample(frame):
                out.append(r.to_ndarray().reshape(-1))
    return np.concatenate(out).astype(np.float32) / 32768.0


def listen(audio: np.ndarray, listening=lambda: True):
    heard = []

    async def on_text(text, info):
        heard.append((text, info))

    async def run():
        ears = Ears(Transcriber(), on_text, listening=listening)
        silence = np.zeros(int(RATE * 1.5), np.float32)
        await ears.feed(np.concatenate([silence, audio, silence]))
        for _ in range(100):  # transcription runs in a thread
            if heard or not ears.busy:
                await asyncio.sleep(0.05)
            if heard:
                break
        while ears.busy:
            await asyncio.sleep(0.05)

    asyncio.run(run())
    return heard


def test_hears_and_understands_a_spoken_answer(tmp_path):
    heard = listen(spoken("x equals five", tmp_path) * 0.5)
    assert len(heard) == 1
    text, info = heard[0]
    assert "5" in text or "five" in text.lower()
    assert info["audio_s"] > 0.3 and info["stt_s"] < 10


def test_silence_muted_mic_and_clicks_are_ignored(tmp_path):
    assert listen(np.zeros(RATE * 2, np.float32)) == []                       # muted mic sends zeros
    click = np.zeros(RATE, np.float32)
    click[1000:1100] = 0.8                                                   # 6 ms tap on the desk
    assert listen(click) == []


def test_nothing_is_heard_outside_voice_mode(tmp_path):
    assert listen(spoken("x equals five", tmp_path) * 0.5, listening=lambda: False) == []


@pytest.mark.parametrize("said,expected", [
    ("x equals five", "x equals 5"),
    ("x equals negative one", "x equals negative 1"),
    ("I forgot to flip the sign", "i forgot to flip the sign"),
    ("the answer is twelve point five", "the answer is 12.5"),
    ("what do you see", "what do you see"),
    ("is my second line right", "is my second line right"),
])
def test_short_math_answers_are_transcribed_right(said, expected, tmp_path):
    [(text, _)] = listen(spoken(said, tmp_path) * 0.5)
    assert text.lower().strip(" .?!") == expected
