"""Retail DATA voice engine — Kokoro 82M TTS (ONNX, CPU) + faster-whisper STT.

This is the RETAIL voice stack. It is deliberately lightweight: fully local,
fully offline after a one-time model download, and CPU-only — no PyTorch, no
CUDA. That makes Conversation Mode work on an ordinary laptop with no GPU,
which the personal F5-TTS clone stack cannot do.

It is a drop-in replacement for the personal `local_voice.py`: the bridge
(`bridge_server.py`) imports this module by name and calls the same public
surface, so no caller has to change:

    transcribe(audio_bytes, suffix=".webm") -> str          # STT  (mic in)
    synthesize(text, voice=DEFAULT_VOICE)   -> (bytes, mime) # TTS  (speaker out)
    synthesize_long(text, voice=DEFAULT_VOICE) -> (bytes, mime)
    warmup()                                                # preload both models
    list_voices() -> list[str]
    set_engine(name)                                        # only "kokoro"
    start_idle_watcher(idle_seconds, check_interval)
    DEFAULT_VOICE, VOICES, ENGINE, VALID_ENGINES, DEVICE
    module attrs read by /voice/status: _whisper_model, _kokoro_model

Why Kokoro: Kokoro-82M is an Apache-2.0 open-weights model that synthesizes
natural speech from ~54 baked-in voicepacks. It does NOT clone a voice — it is
a fixed-voice engine — but it runs fast on plain CPU with no cold-start GPU
penalty, which is exactly what a distributable product needs. Each crew
callsign is mapped to the preset that best fits its persona.

Model assets (downloaded once into ./voice_models/ by ensure_assets()):
    kokoro-v1.0.onnx   (~310 MB)   the synthesis model
    voices-v1.0.bin    (~26 MB)    the voicepack styles
"""

from __future__ import annotations

import io
import logging
import os
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf

log = logging.getLogger("local_voice")
if not log.handlers:
    logging.basicConfig(level=logging.INFO)

# ════════════════════════════════════════════════════════════════
# Paths / assets
# ════════════════════════════════════════════════════════════════
HERE = Path(__file__).resolve().parent
MODELS_DIR = Path(os.environ.get("DATA_VOICE_MODELS", str(HERE / "voice_models")))
MODELS_DIR.mkdir(parents=True, exist_ok=True)

KOKORO_MODEL_PATH  = MODELS_DIR / "kokoro-v1.0.onnx"
KOKORO_VOICES_PATH = MODELS_DIR / "voices-v1.0.bin"

# GitHub release the assets are pulled from when missing.
_ASSET_BASE = ("https://github.com/thewh1teagle/kokoro-onnx/releases/"
               "download/model-files-v1.0")
_ASSETS = {
    KOKORO_MODEL_PATH:  f"{_ASSET_BASE}/kokoro-v1.0.onnx",
    KOKORO_VOICES_PATH: f"{_ASSET_BASE}/voices-v1.0.bin",
}

# Sample rate Kokoro emits at.
KOKORO_SR = 24000

# ════════════════════════════════════════════════════════════════
# Engine metadata (read by the bridge)
# ════════════════════════════════════════════════════════════════
ENGINE = "kokoro"
VALID_ENGINES = ("kokoro",)
DEVICE = "cpu"
DEFAULT_VOICE = "data"

# ════════════════════════════════════════════════════════════════
# STT (Whisper) runtime config — live + persisted
# ════════════════════════════════════════════════════════════════
# These used to be import-time constants. They are now a live, persisted config
# so the Settings UI can retune STT (which model, beam size, compute precision)
# WITHOUT editing code or restarting the daemon — the next spoken turn picks up
# the change, and a model swap reloads lazily on the following utterance.
# Persisted to voice_config.json next to the model assets.
VOICE_CONFIG_PATH = MODELS_DIR / "voice_config.json"

# Catalog of Whisper models the Settings UI offers, fastest → most accurate.
# size_mb is approximate on-disk (int8). The ".en" variants are smaller and
# faster and are the right pick for an English-only, GPU-less machine (e.g. a
# Surface Pro). faster-whisper downloads the chosen model from HuggingFace on
# first use; "installing" simply pre-fetches it so the first turn is not slow.
STT_MODEL_CATALOG = [
    {"id": "tiny.en",          "label": "Tiny (English)",      "size_mb": 75,   "note": "fastest, lowest accuracy"},
    {"id": "tiny",             "label": "Tiny (multilingual)", "size_mb": 75,   "note": "fastest, multilingual"},
    {"id": "base.en",          "label": "Base (English)",      "size_mb": 145,  "note": "balanced, default"},
    {"id": "base",             "label": "Base (multilingual)", "size_mb": 145,  "note": "balanced, multilingual"},
    {"id": "small.en",         "label": "Small (English)",     "size_mb": 480,  "note": "more accurate, slower"},
    {"id": "small",            "label": "Small (multilingual)","size_mb": 480,  "note": "more accurate, multilingual"},
    {"id": "distil-small.en",  "label": "Distil-Small (Eng)",  "size_mb": 330,  "note": "fast, near-small accuracy"},
    {"id": "medium.en",        "label": "Medium (English)",    "size_mb": 1500, "note": "high accuracy, much slower"},
    {"id": "distil-medium.en", "label": "Distil-Medium (Eng)", "size_mb": 790,  "note": "fast, near-medium accuracy"},
    {"id": "large-v3",         "label": "Large v3",            "size_mb": 3100, "note": "best accuracy, GPU recommended"},
    {"id": "distil-large-v3",  "label": "Distil-Large v3",     "size_mb": 1500, "note": "near-large accuracy, faster"},
]
STT_MODEL_IDS = [m["id"] for m in STT_MODEL_CATALOG]

# faster-whisper compute types. int8 is the fastest on a plain CPU and is the
# retail default; the float types only help on a GPU.
COMPUTE_TYPE_CHOICES = ("int8", "int8_float16", "float16", "float32")

_STT_DEFAULTS = {
    "model":        (os.environ.get("DATA_STT_MODEL", "base.en").strip() or "base.en"),
    "beam_size":    1,        # 1 = greedy = fastest (best for CPU/no-GPU)
    "best_of":      1,
    "compute_type": "int8",
    "vad_filter":   True,
}

_CONFIG_LOCK = threading.Lock()


def _load_config() -> dict:
    """Read voice_config.json over the defaults. Missing/garbled file → defaults."""
    cfg = dict(_STT_DEFAULTS)
    try:
        if VOICE_CONFIG_PATH.exists():
            import json
            saved = json.loads(VOICE_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                for k in _STT_DEFAULTS:
                    if k in saved and saved[k] is not None:
                        cfg[k] = saved[k]
    except Exception as e:
        log.warning(f"[STT] could not read {VOICE_CONFIG_PATH.name}: {e}")
    # Defensive clamps in case the file was hand-edited.
    if cfg.get("model") not in STT_MODEL_IDS:
        cfg["model"] = _STT_DEFAULTS["model"]
    if cfg.get("compute_type") not in COMPUTE_TYPE_CHOICES:
        cfg["compute_type"] = _STT_DEFAULTS["compute_type"]
    try:
        cfg["beam_size"] = max(1, min(10, int(cfg["beam_size"])))
        cfg["best_of"]   = max(1, min(10, int(cfg["best_of"])))
    except Exception:
        cfg["beam_size"], cfg["best_of"] = 1, 1
    cfg["vad_filter"] = bool(cfg.get("vad_filter", True))
    return cfg


_stt_cfg = _load_config()

# Back-compat module attrs (older tooling / logs read these). Kept in sync with
# the live config by set_stt_config().
_STT_MODEL   = _stt_cfg["model"]
COMPUTE_TYPE = _stt_cfg["compute_type"]


def get_stt_config() -> dict:
    """Snapshot of the current STT config (model / beam_size / best_of /
    compute_type / vad_filter)."""
    with _CONFIG_LOCK:
        return dict(_stt_cfg)


def _persist_config() -> None:
    import json
    try:
        VOICE_CONFIG_PATH.write_text(json.dumps(_stt_cfg, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"[STT] could not write {VOICE_CONFIG_PATH.name}: {e}")


def set_stt_config(**kw) -> dict:
    """Update STT config, validate, persist, and drop the cached Whisper model
    if the model or compute type changed so the next transcribe() reloads with
    the new settings. Returns the new config snapshot."""
    global _STT_MODEL, COMPUTE_TYPE, _whisper_model
    with _CONFIG_LOCK:
        old_model, old_compute = _stt_cfg["model"], _stt_cfg["compute_type"]
        if kw.get("model"):
            m = str(kw["model"]).strip()
            if m not in STT_MODEL_IDS:
                raise ValueError(f"unknown STT model {m!r}")
            _stt_cfg["model"] = m
        if kw.get("beam_size") is not None:
            _stt_cfg["beam_size"] = max(1, min(10, int(kw["beam_size"])))
        if kw.get("best_of") is not None:
            _stt_cfg["best_of"] = max(1, min(10, int(kw["best_of"])))
        if kw.get("compute_type"):
            c = str(kw["compute_type"]).strip()
            if c not in COMPUTE_TYPE_CHOICES:
                raise ValueError(f"unknown compute type {c!r}")
            _stt_cfg["compute_type"] = c
        if kw.get("vad_filter") is not None:
            _stt_cfg["vad_filter"] = bool(kw["vad_filter"])
        _STT_MODEL   = _stt_cfg["model"]
        COMPUTE_TYPE = _stt_cfg["compute_type"]
        _persist_config()
        changed = (_stt_cfg["model"] != old_model) or (_stt_cfg["compute_type"] != old_compute)
        snapshot = dict(_stt_cfg)
    if changed:
        with _LOAD_LOCK:
            _whisper_model = None
        log.info(f"[STT] config changed → next turn loads {snapshot['model']} ({snapshot['compute_type']})")
    return snapshot

# ── Crew voice → Kokoro preset map ──────────────────────────────
# Kokoro ships fixed voicepacks (am_=American male, af_=American female,
# bm_/bf_=British). Each retail crew callsign gets the preset that best fits
# its persona. "preset" is the Kokoro voice name; "speed" tunes cadence.
# These ids mirror CREW_VOICES in bridge_server.py so the selector lines up.
# Default voice "data" speaks as Daniel (bm_daniel) — Captain's pick, 2026-06-29.
VOICES = {
    "data":     {"name": "DATA",     "preset": "bm_daniel",  "speed": 0.95},
    "atlas":    {"name": "Atlas",    "preset": "am_michael", "speed": 1.0},
    "forge":    {"name": "Forge",    "preset": "am_fenrir",  "speed": 1.0},
    "vector":   {"name": "Vector",   "preset": "am_eric",    "speed": 1.0},
    "sentinel": {"name": "Sentinel", "preset": "am_adam",    "speed": 0.95},
    "probe":    {"name": "Probe",    "preset": "am_liam",    "speed": 1.0},
    "relay":    {"name": "Relay",    "preset": "am_echo",    "speed": 1.0},
    "sage":     {"name": "Sage",     "preset": "bm_daniel",  "speed": 0.95},
    "echo":     {"name": "Echo",     "preset": "af_heart",   "speed": 1.0},
    "pulse":    {"name": "Pulse",    "preset": "af_bella",   "speed": 1.05},
    "scout":    {"name": "Scout",    "preset": "af_nicole",  "speed": 1.0},
}

# Preset to fall back to when a mapped voicepack is not present in the model.
_FALLBACK_PRESET = "am_onyx"


def list_voices() -> list[str]:
    """Crew voice ids this engine can speak. Used by /voice/voices."""
    return list(VOICES.keys())


def _resolve_preset(voice: str) -> tuple[str, float]:
    """Map a crew voice id to (kokoro_preset, speed)."""
    spec = VOICES.get((voice or DEFAULT_VOICE).lower(), VOICES[DEFAULT_VOICE])
    return spec["preset"], float(spec.get("speed", 1.0))


# ════════════════════════════════════════════════════════════════
# Asset download
# ════════════════════════════════════════════════════════════════
_ASSET_LOCK = threading.Lock()


def ensure_assets() -> None:
    """Download the Kokoro model + voicepack if they are not already present.

    Idempotent. Raises on a failed download so callers surface a clear error
    rather than loading a truncated model.
    """
    with _ASSET_LOCK:
        for dest, url in _ASSETS.items():
            if dest.exists() and dest.stat().st_size > 1024:
                continue
            log.info(f"[kokoro] downloading {dest.name} from {url}")
            tmp = dest.with_suffix(dest.suffix + ".part")
            try:
                urllib.request.urlretrieve(url, tmp)
                tmp.replace(dest)
                log.info(f"[kokoro] {dest.name} ready ({dest.stat().st_size/1e6:.1f} MB)")
            except Exception as e:
                try: tmp.unlink()
                except OSError: pass
                raise RuntimeError(f"failed to download {dest.name}: {e}") from e


# ════════════════════════════════════════════════════════════════
# Activity / idle unload
# ════════════════════════════════════════════════════════════════
_last_activity = time.time()
_idle_watcher_started = False


def _touch_activity() -> None:
    global _last_activity
    _last_activity = time.time()


def unload_models() -> None:
    """Free both models from RAM (idle reclaim)."""
    global _kokoro_model, _whisper_model
    with _LOAD_LOCK:
        _kokoro_model = None
        _whisper_model = None
    log.info("[voice] models unloaded (idle)")


def start_idle_watcher(idle_seconds: int = 600, check_interval: int = 30) -> None:
    """Background thread that unloads the models after `idle_seconds` of no use.
    Keeps idle RAM low on a customer machine; first call after idle re-pays the
    (modest, CPU) load cost."""
    global _idle_watcher_started
    if _idle_watcher_started:
        return
    _idle_watcher_started = True

    def _loop():
        while True:
            time.sleep(check_interval)
            if (_kokoro_model is not None or _whisper_model is not None) and \
               (time.time() - _last_activity) > idle_seconds:
                unload_models()

    threading.Thread(target=_loop, daemon=True, name="voice-idle-watcher").start()


# ════════════════════════════════════════════════════════════════
# Model handles + lazy loaders
# ════════════════════════════════════════════════════════════════
_LOAD_LOCK = threading.Lock()
_kokoro_model = None
_whisper_model = None
# (model_id, compute_type) the cached Whisper handle was built with — lets
# set_stt_config() force a reload only when one of those actually changed.
_whisper_loaded_key = None

# Back-compat aliases: the personal bridge's /voice/status reads `_f5_model`
# and `_xtts_model`. Retail has neither engine, so they stay None; the status
# handler is updated to read `_kokoro_model`, but keeping these avoids an
# AttributeError on any older code path that still references them.
_f5_model = None
_xtts_model = None


def _load_kokoro():
    """Lazy-load the Kokoro ONNX model. First call ensures assets exist."""
    global _kokoro_model
    if _kokoro_model is not None:
        return _kokoro_model
    with _LOAD_LOCK:
        if _kokoro_model is not None:
            return _kokoro_model
        ensure_assets()
        from kokoro_onnx import Kokoro
        log.info("[TTS] loading Kokoro-82M (onnx, cpu)")
        _kokoro_model = Kokoro(str(KOKORO_MODEL_PATH), str(KOKORO_VOICES_PATH))
        try:
            avail = set(_kokoro_model.get_voices())
            missing = sorted({s["preset"] for s in VOICES.values()} - avail)
            if missing:
                log.warning(f"[TTS] presets not in model, will fall back: {missing}")
        except Exception:
            pass
        log.info("[TTS] Kokoro ready")
        return _kokoro_model


# Vocabulary hint for Whisper — biases recognition toward product/crew names so
# "DATA" is not heard as "Dave", crew callsigns survive, etc.
_INITIAL_PROMPT = (
    "Captain speaking to DATA, the ship's computer. "
    "Hello DATA. DATA, run diagnostics. "
    "Atlas, Forge, Vector, Sentinel, Probe, Relay, Sage, Echo, Pulse, Scout. "
    "Claude, Codex, Gemini, Ollama."
)


def _load_whisper():
    """Lazy-load faster-whisper using the live config. First call for a given
    model downloads it (~75MB for tiny/base, more for larger models). Reloads
    automatically if the configured model or compute type changed."""
    global _whisper_model, _whisper_loaded_key
    cfg = get_stt_config()
    want = (cfg["model"], cfg["compute_type"])
    if _whisper_model is not None and _whisper_loaded_key == want:
        return _whisper_model
    with _LOAD_LOCK:
        if _whisper_model is not None and _whisper_loaded_key == want:
            return _whisper_model
        from faster_whisper import WhisperModel
        log.info(f"[STT] loading faster-whisper {want[0]} ({DEVICE}, {want[1]})")
        _whisper_model = WhisperModel(want[0], device=DEVICE, compute_type=want[1])
        _whisper_loaded_key = want
        log.info("[STT] model ready")
        return _whisper_model


# ════════════════════════════════════════════════════════════════
# STT model install (background download) + installed detection
# ════════════════════════════════════════════════════════════════
def _stt_repo_for(model_id: str) -> str:
    """HuggingFace repo faster-whisper pulls a given model id from."""
    if model_id.startswith("distil-"):
        return f"Systran/faster-distil-whisper-{model_id[len('distil-'):]}"
    return f"Systran/faster-whisper-{model_id}"


def _hf_hub_cache() -> Path:
    """Where huggingface_hub caches downloaded models."""
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def is_model_installed(model_id: str) -> bool:
    """True if the model's weights are already on disk (no download needed)."""
    folder = "models--" + _stt_repo_for(model_id).replace("/", "--")
    snaps = _hf_hub_cache() / folder / "snapshots"
    if not snaps.is_dir():
        return False
    try:
        for snap in snaps.iterdir():
            if (snap / "model.bin").exists():
                return True
    except OSError:
        pass
    return False


_install_state: dict = {}      # model_id -> {"status": "downloading"|"ready"|"error", "error"?: str}
_install_lock = threading.Lock()


def stt_install_state() -> dict:
    with _install_lock:
        return {k: dict(v) for k, v in _install_state.items()}


def _do_install(model_id: str) -> None:
    try:
        from faster_whisper import WhisperModel
        log.info(f"[STT] installing model {model_id} (downloading if needed)")
        # Constructing the model triggers the HuggingFace download into cache.
        WhisperModel(model_id, device=DEVICE, compute_type=get_stt_config()["compute_type"])
        with _install_lock:
            _install_state[model_id] = {"status": "ready"}
        log.info(f"[STT] model {model_id} installed")
    except Exception as e:
        with _install_lock:
            _install_state[model_id] = {"status": "error", "error": str(e)}
        log.warning(f"[STT] install of {model_id} failed: {e}")


def start_install(model_id: str) -> dict:
    """Kick off a background download of a model. Idempotent: returns the current
    status if it is already installed or in flight."""
    if model_id not in STT_MODEL_IDS:
        raise ValueError(f"unknown STT model {model_id!r}")
    with _install_lock:
        st = _install_state.get(model_id)
        if st and st.get("status") == "downloading":
            return {"status": "downloading"}
        if is_model_installed(model_id):
            _install_state[model_id] = {"status": "ready"}
            return {"status": "ready"}
        _install_state[model_id] = {"status": "downloading"}
    threading.Thread(target=_do_install, args=(model_id,), daemon=True,
                     name=f"stt-install-{model_id}").start()
    return {"status": "downloading"}


def stt_catalog() -> list:
    """The model catalog annotated with per-model installed/download status —
    what the Settings UI renders."""
    out = []
    for m in STT_MODEL_CATALOG:
        with _install_lock:
            st = _install_state.get(m["id"], {})
        installed = is_model_installed(m["id"])
        status = st.get("status") or ("ready" if installed else "not_installed")
        out.append({**m, "installed": installed, "status": status,
                    "error": st.get("error")})
    return out


# ════════════════════════════════════════════════════════════════
# STT
# ════════════════════════════════════════════════════════════════
def transcribe(audio_bytes: bytes, suffix: str = ".webm") -> str:
    """Speech-to-text. Accepts any audio format ffmpeg/soundfile can read
    (webm/ogg/wav/mp3); returns the transcript text."""
    _touch_activity()
    model = _load_whisper()
    cfg = get_stt_config()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio_bytes)
        tmp = f.name
    try:
        segments, _info = model.transcribe(
            tmp,
            language="en",
            beam_size=int(cfg["beam_size"]),
            best_of=int(cfg["best_of"]),
            temperature=0.0,
            initial_prompt=_INITIAL_PROMPT,
            condition_on_previous_text=False,
            vad_filter=bool(cfg["vad_filter"]),
            vad_parameters={"min_silence_duration_ms": 500},
        )
        return " ".join(s.text.strip() for s in segments).strip()
    finally:
        try: os.unlink(tmp)
        except OSError: pass


# ════════════════════════════════════════════════════════════════
# TTS
# ════════════════════════════════════════════════════════════════
def set_engine(name: str) -> None:
    """Retail ships a single engine (Kokoro). Anything else is rejected."""
    if name not in VALID_ENGINES:
        raise ValueError(f"engine must be one of {VALID_ENGINES}")


def _wav_bytes(samples: np.ndarray, sr: int = KOKORO_SR) -> tuple[bytes, str]:
    """Encode a float32 mono waveform to 16-bit PCM WAV bytes."""
    buf = io.BytesIO()
    sf.write(buf, samples, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue(), "audio/wav"


def _synth_one(text: str, voice: str) -> np.ndarray:
    """Synthesize a single chunk, returning the raw float32 waveform.
    Falls back to the default preset if the mapped voicepack is unavailable."""
    model = _load_kokoro()
    preset, speed = _resolve_preset(voice)
    try:
        samples, _sr = model.create(text, voice=preset, speed=speed, lang="en-us")
    except Exception as e:
        log.warning(f"[TTS] preset {preset!r} failed ({e}); falling back to {_FALLBACK_PRESET}")
        samples, _sr = model.create(text, voice=_FALLBACK_PRESET, speed=speed, lang="en-us")
    return np.asarray(samples, dtype=np.float32)


def synthesize(text: str, voice: str = DEFAULT_VOICE) -> tuple[bytes, str]:
    """Synthesize a short utterance to WAV bytes."""
    _touch_activity()
    text = (text or "").strip()
    if not text:
        return _wav_bytes(np.zeros(1, dtype=np.float32))
    return _wav_bytes(_synth_one(text, voice))


# ── chunking for long text ──────────────────────────────────────
_TTS_MAX_CHARS = 380


def _chunk_for_tts(text: str, max_chars: int = _TTS_MAX_CHARS) -> list[str]:
    """Split text on sentence boundaries into <= max_chars chunks so Kokoro
    keeps prosody natural and never chokes on a giant string."""
    import re
    sentences = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    chunks: list[str] = []
    cur = ""
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(s) > max_chars:
            # hard-wrap an over-long sentence on commas / spaces
            for piece in re.split(r"(?<=,)\s+", s):
                while len(piece) > max_chars:
                    chunks.append(piece[:max_chars])
                    piece = piece[max_chars:]
                if piece:
                    if len(cur) + len(piece) + 1 <= max_chars:
                        cur = f"{cur} {piece}".strip()
                    else:
                        if cur: chunks.append(cur)
                        cur = piece
            continue
        if len(cur) + len(s) + 1 <= max_chars:
            cur = f"{cur} {s}".strip()
        else:
            if cur: chunks.append(cur)
            cur = s
    if cur:
        chunks.append(cur)
    return chunks or [text.strip()]


def synthesize_long(text: str, voice: str = DEFAULT_VOICE) -> tuple[bytes, str]:
    """Synthesize arbitrarily long text by chunking on sentence boundaries and
    concatenating the waveforms (with a short pause between chunks)."""
    _touch_activity()
    text = (text or "").strip()
    if not text:
        return _wav_bytes(np.zeros(1, dtype=np.float32))
    chunks = _chunk_for_tts(text)
    if len(chunks) == 1:
        return _wav_bytes(_synth_one(chunks[0], voice))
    gap = np.zeros(int(KOKORO_SR * 0.18), dtype=np.float32)   # 180ms pause
    pieces: list[np.ndarray] = []
    for i, c in enumerate(chunks):
        pieces.append(_synth_one(c, voice))
        if i < len(chunks) - 1:
            pieces.append(gap)
    return _wav_bytes(np.concatenate(pieces))


# ════════════════════════════════════════════════════════════════
# Warmup
# ════════════════════════════════════════════════════════════════
def warmup() -> None:
    """Preload both models so the first Conversation Mode turn is responsive.
    Called in a background thread by the bridge on boot.

    Retail courtesy: do NOT force the ~340MB Kokoro download on a customer who
    may never open Conversation Mode. Only preload when the model assets are
    already on disk (a returning voice user). On a fresh install the assets are
    lazy-downloaded the first time Conversation Mode is actually entered. Set
    DATA_VOICE_PRELOAD=1 to force the download+preload on boot regardless."""
    force = os.environ.get("DATA_VOICE_PRELOAD", "").strip().lower() in ("1", "true", "yes")
    if not (KOKORO_MODEL_PATH.exists() and KOKORO_VOICES_PATH.exists()) and not force:
        log.info("[voice] model assets not present — deferring download to first use")
        return
    ensure_assets()
    _load_kokoro()
    # A tiny synth primes the onnx graph so the first real turn is not slow.
    try:
        _synth_one("Online.", DEFAULT_VOICE)
    except Exception as e:
        log.warning(f"[TTS] warmup synth failed: {e}")
    _load_whisper()


# ════════════════════════════════════════════════════════════════
# CLI smoke test:  python local_voice.py "text" [voice]
# ════════════════════════════════════════════════════════════════
def _main(argv: list[str]) -> int:
    text = argv[1] if len(argv) > 1 else "Online. Conversation mode is active."
    voice = argv[2] if len(argv) > 2 else DEFAULT_VOICE
    wav, mime = synthesize_long(text, voice)
    out = HERE / f"test_output_{voice}.wav"
    out.write_bytes(wav)
    print(f"wrote {out} ({len(wav)} bytes, {mime}) using preset {_resolve_preset(voice)[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
