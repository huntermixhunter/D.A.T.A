"""Create the original public arcade sounds offline. Requires NumPy, no services."""
import hashlib
import json
from pathlib import Path
import wave

import numpy as np

RATE = 44100
ROOT = Path(__file__).resolve().parents[1] / "dashboard/arcade/assets/sounds"
RNG = np.random.default_rng(8127)


def noise(length, cutoff, highpass=30):
    bins = np.fft.rfftfreq(length, 1 / RATE)
    spectrum = np.fft.rfft(RNG.normal(size=length))
    shape = 1 / np.sqrt(1 + (bins / cutoff) ** 6)
    shape *= bins / np.sqrt(bins ** 2 + highpass ** 2)
    return np.fft.irfft(spectrum * shape, n=length)


def export(name, signal, target, loop=False):
    signal -= signal.mean()
    if not loop:
        attack, release = round(.002 * RATE), round(.05 * RATE)
        signal[:attack] *= np.linspace(0, 1, attack)
        signal[-release:] *= np.linspace(1, 0, release)
    scale = min(10 ** (target / 20) / np.sqrt(np.mean(signal ** 2)),
                10 ** (-3 / 20) / np.max(np.abs(signal)))
    samples = np.round(signal * scale * 32767).astype("<i2")
    path = ROOT / name
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, RATE, len(samples), "NONE", "not compressed"))
        output.writeframes(samples.tobytes())
    return {"file": name, "source": "Original procedural synthesis, tools/build_arcade_sounds.py",
            "license": "Apache-2.0", "duration_seconds": len(samples) / RATE,
            "loop": loop, "peak_dbfs": round(float(20 * np.log10(np.max(np.abs(signal * scale)))), 2),
            "rms_dbfs": round(float(20 * np.log10(np.sqrt(np.mean((signal * scale) ** 2)))), 2),
            "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    clips = []
    t = np.arange(round(.519 * RATE)) / RATE
    phase = 2 * np.pi * (210 * t + 1150 * .035 * (1 - np.exp(-t / .035)))
    shot = (np.sin(phase) + .25 * np.sin(phase * 2.03)) * np.exp(-t * 13)
    shot += noise(len(t), 8500) * np.exp(-t * 65) * .45
    clips.append(export("laser-shot.wav", shot, -18))

    t = np.arange(round(1.856 * RATE)) / RATE
    blast = (noise(len(t), 4200) * np.exp(-t * 9) * 2.5
             + noise(len(t), 480) * np.exp(-t * 2.6) * 3
             + np.sin(2 * np.pi * (38 * t + 45 * .08 * (1 - np.exp(-t / .08)))) * np.exp(-t * 5) * .7)
    clips.append(export("explosion.wav", blast, -16))

    # Integer-cycle partials and periodic FFT noise have continuous loop boundaries.
    t = np.arange(3 * RATE) / RATE
    engine = noise(len(t), 360) * .9
    engine += .13 * np.sin(2 * np.pi * 63 * t) + .055 * np.sin(2 * np.pi * 126 * t)
    engine += .018 * np.sin(2 * np.pi * 252 * t) * (1 + .2 * np.sin(2 * np.pi * t))
    clips.append(export("engine-loop.wav", engine, -18, loop=True))

    t = np.arange(2 * RATE) / RATE
    boost = noise(len(t), 1800) * 1.5 + noise(len(t), 190) * 1.2
    boost += .1 * np.sin(2 * np.pi * 97 * t) + .035 * np.sin(2 * np.pi * 388 * t)
    clips.append(export("boost-loop.wav", boost, -16, loop=True))

    t = np.arange(round(.48 * RATE)) / RATE
    phase = 2 * np.pi * (80 * t + 1750 * .022 * (1 - np.exp(-t / .022)))
    pulse = (np.sin(phase) + .35 * np.sin(phase * 1.51)) * np.exp(-t * 14)
    pulse += noise(len(t), 5200) * np.exp(-t * 45) * .6
    clips.append(export("pulse.wav", pulse, -16))
    metadata = {"processing": "Original mono 44.1 kHz, 16-bit PCM WAV, deterministic procedural synthesis with a -3 dBFS peak ceiling.",
                "rights_status": "Original project assets, distributed under the repository Apache-2.0 license. No third-party recordings are included.",
                "clips": clips}
    (ROOT / "sources.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({c["file"]: c["sha256"] for c in clips}, indent=2))


if __name__ == "__main__":
    main()
