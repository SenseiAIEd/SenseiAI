"""Ears: voice activity detection + real Whisper on a synthetic student voice. Run: pytest gateway/"""
import asyncio
import shutil
import subprocess

import numpy as np
import pytest

from ears import RATE, Ears, Transcriber, is_phantom

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


def listen(audio: np.ndarray, listening=lambda: True, noise=None):
    heard = []

    async def on_text(text, info):
        heard.append((text, info))

    async def run():
        transcriber = Transcriber()
        transcriber.load()  # the gateway loads the model at startup; don't time the load
        ears = Ears(transcriber, on_text, listening=listening, on_noise=(noise.append if noise is not None else None))
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


def test_loud_noise_is_not_speech_and_is_never_transcribed(tmp_path):
    # Loud enough for the energy detector to start an utterance, but nobody is talking: Whisper
    # would have written "Thank you." for this. Silero finds no voice, so it isn't transcribed.
    rng = np.random.default_rng(0)
    rustle = (rng.standard_normal(RATE) * 0.08).astype(np.float32)
    noise = []
    assert listen(rustle, noise=noise) == []
    assert [n["reason"] for n in noise] == ["no voice"]


@pytest.mark.parametrize("text,no_speech,phantom", [
    ("Thank you.", 0.63, True),        # the demo's phantom, with Whisper unsure anyone spoke
    ("Silence.", 0.47, True),
    ("Thanks for watching!", 0.5, True),
    ("", 0.1, True),
    ("Thank you.", 0.08, False),       # clearly said: a real thank you
    ("No, I don't get it.", 0.6, False),  # not a stock phrase
    ("What do you see now?", 0.51, False),
])
def test_stock_phrases_only_count_when_whisper_doubts_there_was_speech(text, no_speech, phantom):
    assert is_phantom(text, no_speech) is phantom


def bursts(*gaps_s):
    """Tone bursts (0.6 s each) separated by the given silences: a voice pausing mid-sentence."""
    tone = (0.3 * np.sin(2 * np.pi * 220 * np.arange(int(RATE * 0.6)) / RATE)).astype(np.float32)
    parts = [np.zeros(RATE, np.float32), tone]
    for gap in gaps_s:
        parts += [np.zeros(int(RATE * gap), np.float32), tone]
    return np.concatenate(parts + [np.zeros(RATE * 3, np.float32)])


def turns(audio, *transcripts):
    """What the tutor is handed, with a fake transcriber saying `transcripts` in order."""
    said, heard = list(transcripts), []

    async def on_text(text, info):
        heard.append(text)

    async def run():
        ears = Ears(lambda a: said.pop(0), on_text, voiced=lambda a: 1.0)
        for i in range(0, len(audio), RATE // 10):  # in real time, so the hold can run out
            await ears.feed(audio[i:i + RATE // 10])
            await asyncio.sleep(0.02)
        await asyncio.sleep(2.5)

    asyncio.run(run())
    return heard


@pytest.mark.parametrize("unfinished", ["Yeah, this is the.", "So the next step is because...", "x equals"])
def test_a_pause_mid_sentence_is_one_turn(unfinished):
    assert turns(bursts(1.2), unfinished, "I want to do.") == [f"{unfinished} I want to do."]


def test_finished_sentences_are_separate_turns():
    assert turns(bursts(1.2), "Is my second line right?", "I think so.") == [
        "Is my second line right?", "I think so."]


def test_a_dangling_sentence_is_still_heard_if_nothing_follows():
    assert turns(bursts(), "Can you check the") == ["Can you check the"]


def test_numbers_finish_a_sentence():
    from ears import sounds_unfinished
    assert not sounds_unfinished("x equals 5")
    assert not sounds_unfinished("the answer is 12.5")
    assert sounds_unfinished("x equals")
