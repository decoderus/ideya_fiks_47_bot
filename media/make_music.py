"""Фоновая музыка для промо-ролика: синтезирована с нуля (без семплов), свободна от прав.

python make_music.py [длительность_сек] [начало_ударных_сек] -> music.wav (44,1 кГц, стерео)
"""
import sys
import wave
from pathlib import Path

import numpy as np

SR = 44100
BPM = 112
BEAT = 60 / BPM
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 29.5
DRUMS_FROM = float(sys.argv[2]) if len(sys.argv) > 2 else 3.6
OUT = Path(__file__).with_name("music.wav")

# I–V–vi–IV в до мажоре, по 4 доли на аккорд (MIDI-номера)
CHORDS = [[60, 64, 67], [55, 59, 62], [57, 60, 64], [53, 57, 60]]
BASS = [36, 31, 33, 29]


def hz(note: float) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


def env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / SR
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    return a * np.exp(-t / decay)


def tone(freq: float, n: int, harmonics=(1.0, 0.5, 0.25), detune=0.0) -> np.ndarray:
    t = np.arange(n) / SR
    out = np.zeros(n)
    for k, amp in enumerate(harmonics, start=1):
        out += amp * np.sin(2 * np.pi * freq * k * (1 + detune) * t)
    return out


def add(track: np.ndarray, sound: np.ndarray, at: float, gain: float) -> None:
    i = int(at * SR)
    if i >= len(track):
        return
    sound = sound[: len(track) - i]
    track[i : i + len(sound)] += gain * sound


def main() -> None:
    n = int(DURATION * SR)
    left, right = np.zeros(n), np.zeros(n)
    rng = np.random.default_rng(7)
    beats = int(DURATION / BEAT) + 1

    for b in range(beats):
        at = b * BEAT
        chord = CHORDS[(b // 4) % 4]
        bar_beat = b % 4
        # пэд: мягкий аккорд на всю длину (на первой доле аккорда)
        if bar_beat == 0:
            length = int(4 * BEAT * SR)
            for i, note in enumerate(chord):
                pad = tone(hz(note), length, (1.0, 0.3, 0.12), detune=0.002) * env(length, 0.35, 3.0)
                add(left, pad, at, 0.045 if i != 1 else 0.035)
                add(right, tone(hz(note), length, (1.0, 0.3, 0.12), detune=-0.002) * env(length, 0.35, 3.0), at, 0.045)
        # арпеджио восьмыми: колокольчик-«пицц»
        for half in (0, 1):
            note = chord[(bar_beat * 2 + half) % 3] + 12
            length = int(0.45 * SR)
            pluck = tone(hz(note), length, (1.0, 0.4, 0.15, 0.08)) * env(length, 0.004, 0.16)
            pan = 0.65 if half else 0.35
            add(left, pluck, at + half * BEAT / 2, 0.11 * (1 - pan) * 2)
            add(right, pluck, at + half * BEAT / 2, 0.11 * pan * 2)
        if at < DRUMS_FROM:
            continue
        # бас: восьмые с акцентом на долю
        for half in (0, 1):
            length = int(BEAT / 2 * SR)
            bass = tone(hz(BASS[(b // 4) % 4]), length, (1.0, 0.35)) * env(length, 0.005, 0.22)
            g = 0.16 if half == 0 else 0.09
            add(left, bass, at + half * BEAT / 2, g)
            add(right, bass, at + half * BEAT / 2, g)
        # бочка на 1 и 3
        if bar_beat in (0, 2):
            length = int(0.25 * SR)
            t = np.arange(length) / SR
            kick = np.sin(2 * np.pi * (55 + 90 * np.exp(-t * 30)) * t) * np.exp(-t * 14)
            add(left, kick, at, 0.32)
            add(right, kick, at, 0.32)
        # хлопок на 2 и 4
        if bar_beat in (1, 3):
            length = int(0.18 * SR)
            clap = rng.standard_normal(length) * env(length, 0.002, 0.05)
            clap = np.convolve(clap, np.ones(6) / 6, mode="same")
            add(left, clap, at, 0.06)
            add(right, clap, at, 0.06)
        # хай-хэт на слабые восьмые
        length = int(0.05 * SR)
        hat = np.diff(rng.standard_normal(length + 1)) * env(length, 0.001, 0.012)
        add(left, hat, at + BEAT / 2, 0.035)
        add(right, hat, at + BEAT / 2, 0.045)

    # финальный аккорд и затухание
    final = int(2.5 * SR)
    for note in [48, 60, 64, 67, 72]:
        chord_tail = tone(hz(note), final, (1.0, 0.3)) * env(final, 0.02, 1.2)
        add(left, chord_tail, DURATION - 2.2, 0.05)
        add(right, chord_tail, DURATION - 2.2, 0.05)
    fade_in = np.clip(np.arange(n) / (0.4 * SR), 0, 1)
    fade_out = np.clip((n - np.arange(n)) / (1.5 * SR), 0, 1)
    stereo = np.stack([left, right], axis=1) * (fade_in * fade_out)[:, None]
    stereo *= 0.7 / np.max(np.abs(stereo))  # пик −3 дБ: фон, но слышно и с динамика телефона
    data = (stereo * 32767).astype("<i2")
    with wave.open(str(OUT), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
    print("готово:", OUT, f"{DURATION} с")


if __name__ == "__main__":
    main()
