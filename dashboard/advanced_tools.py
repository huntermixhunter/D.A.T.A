"""Device-local setup records for a fixed catalogue of optional applications.

No installers, model downloads, application execution or cloud calls. Connection
checks are opt-in GET requests to a literal loopback address and fixed health path.
"""
from __future__ import annotations

import copy
import json
import os
import platform
import re
import shutil
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from remote_access import SetupError, setup_allowed


CATALOG = (
    {
        "id": "framepack", "listing_id": "library-framepack-video-studio",
        "name": "FramePack Video Studio", "engine": "FramePack", "category": "Local video",
        "description": "Animate a still image with a local image-to-video application.",
        "source": "https://github.com/lllyasviel/FramePack",
        "requirements": "Windows or Linux. Supported NVIDIA RTX 30, 40 or 50 series GPU with at least 6 GB VRAM.",
        "download": "More than 30 GB of model downloads, plus the application and room for outputs.",
        "costs": "Local processing has no per-video API fee. Electricity, storage and any metered internet still apply.",
        "default_url": "http://127.0.0.1:7860", "health_path": "/config",
        "steps": [
            "Check the GPU and available disk space before choosing the official Windows package or Linux setup.",
            "Review the application and model terms. Choose the install and model-cache locations before the first run.",
            "Use the official repository instructions. The first launch automatically downloads models, so review the download size first.",
            "Start the app bound to 127.0.0.1, with public sharing off. Save its local address here and check the connection.",
            "Try one short clip with an image you have permission to use. Record the prompt, settings and output location.",
        ],
    },
    {
        "id": "comfyui", "listing_id": "library-image-studio",
        "name": "Image Studio", "engine": "ComfyUI", "category": "Local images",
        "description": "Build reusable workflows for images, product scenes and upscaling.",
        "source": "https://github.com/Comfy-Org/ComfyUI",
        "requirements": "Windows, macOS or Linux with a compatible backend. GPU and memory needs depend on the workflow; CPU mode is slower.",
        "download": "Application plus selected models. Model downloads vary, commonly several GB each.",
        "costs": "Local workflows have no per-image API fee. Optional API nodes can charge separately; starter guidance uses local nodes.",
        "default_url": "http://127.0.0.1:8188", "health_path": "/system_stats",
        "steps": [
            "Choose an official installation option for this OS and GPU. Review the selected models and their licenses.",
            "Reserve space for models and outputs. Install the application from its official source after reviewing the download.",
            "Begin with a built-in workflow and local nodes. Review custom nodes before installing their code; exclude paid API nodes unless separately approved.",
            "Start the local app, save its loopback address here, and check the connection.",
            "Run a small image first. Save the workflow, seed and model names with the finished image so it can be reproduced.",
        ],
    },
    {
        "id": "whispercpp", "listing_id": "library-local-transcription",
        "name": "Local Transcription", "engine": "whisper.cpp", "category": "Local audio",
        "description": "Transcribe recordings on this computer, with CPU support.",
        "source": "https://github.com/ggml-org/whisper.cpp",
        "requirements": "Windows, macOS or Linux. CPU operation is supported; memory and speed depend on the model.",
        "download": "Model examples: tiny 75 MiB, base 142 MiB, small 466 MiB. Application files are additional.",
        "costs": "Local transcription has no per-minute API fee. Summarizing a transcript with your selected AI provider may use its allowance or incur charges.",
        "default_url": "", "health_path": "",
        "steps": [
            "Choose a language and model size. Start with tiny or base for a short test, then assess accuracy.",
            "Obtain or build whisper.cpp from the official repository and download the chosen model after reviewing its size.",
            "Save the application folder and model file below. File checks confirm their presence only; they do not execute the binary.",
            "Prepare a short 16-bit WAV recording, using a local converter if needed. Keep the original recording.",
            "Run a test through your normal local workflow. Review names, timestamps and uncertain passages before creating a summary.",
        ],
    },
)
TOOLS = {tool["id"]: tool for tool in CATALOG}


def local_url(value, blocked_ports=()):
    """Normalize localhost to a literal address, without DNS or redirects."""
    if not isinstance(value, str) or len(value) > 200 or re.search(r"[\s\\]", value):
        raise SetupError("Use a local HTTP address, such as http://127.0.0.1:8188.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        valid = (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")
                 and not parsed.username and not parsed.password and parsed.path in ("", "/")
                 and not parsed.query and not parsed.fragment and port and 1024 <= port <= 65535
                 and port not in {7777, *blocked_ports})
    except ValueError:
        valid = False
    if not valid:
        raise SetupError("Use the application's loopback HTTP address with its port, without a path, credentials or query.")
    host = "[::1]" if parsed.hostname == "::1" else "127.0.0.1"
    return f"http://{host}:{port}"


def local_path(value):
    if not isinstance(value, str) or len(value) > 1024 or any(ord(c) < 32 for c in value):
        raise SetupError("Use an absolute local file or folder path.")
    value = value.strip()
    if not value:
        return ""
    # UNC/device paths can contact file servers merely by checking existence.
    if value.startswith(("\\\\", "//")) or not Path(value).is_absolute():
        raise SetupError("Use an absolute path on this computer, not a network share.")
    if os.name == "nt" and ":" in value[2:]:
        raise SetupError("Alternate file streams are not supported.")
    return str(Path(value))


def compatibility(tool_id, hw):
    system = hw.get("os", platform.system())
    if tool_id == "framepack":
        if system not in ("Windows", "Linux"):
            return {"state": "unsupported", "label": "Unsupported OS", "detail": "The official setup targets Windows and Linux."}
        gpu = hw.get("gpu_name", "")
        vram = hw.get("vram_total_gb")
        if not gpu or not vram:
            return {"state": "unknown", "label": "GPU check needed", "detail": "A supported NVIDIA GPU and at least 6 GB VRAM have not been confirmed."}
        if not re.search(r"\bRTX\s*(?:30|40|50)\d{2}\b", gpu, re.I) or vram < 6:
            return {"state": "unsupported", "label": "GPU requirements unmet", "detail": "Check the official supported GPU series and 6 GB VRAM minimum."}
        return {"state": "compatible", "label": "Basic hardware match", "detail": "OS, GPU series and VRAM match. Drivers, disk space and model compatibility still need checking."}
    if system not in ("Windows", "Darwin", "Linux"):
        return {"state": "unknown", "label": "Check platform support", "detail": "Review the official setup instructions for this operating system."}
    if tool_id == "whispercpp":
        return {"state": "compatible", "label": "CPU supported", "detail": "Choose a model that fits available memory. Speed and accuracy vary."}
    return {"state": "unknown", "label": "Workflow dependent", "detail": "Match the installation and model to this machine's GPU or CPU backend."}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def probe(tool, address):
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        request = Request(address + tool["health_path"], headers={"Accept": "application/json"})
        with opener.open(request, timeout=3) as response:
            if response.status != 200:
                raise ValueError("Unexpected status")
            raw = response.read(256 * 1024 + 1)
            if len(raw) > 256 * 1024:
                raise ValueError("Response too large")
            data = json.loads(raw)
        if tool["id"] == "comfyui":
            identified = isinstance(data, dict) and isinstance(data.get("system"), dict) and isinstance(data.get("devices"), list)
        else:
            # Gradio alone is insufficient to identify FramePack.
            identified = (isinstance(data, dict) and isinstance(data.get("components"), list)
                          and "framepack" in json.dumps(data).lower())
        if not identified:
            return {"state": "unconfirmed", "message": "A server responded, but its response did not identify the expected app."}
        return {"state": "connected", "message": "The local app responded. Models and generation have not been tested.", "url": address}
    except HTTPError as exc:
        exc.close()
        return {"state": "offline", "message": "The local app returned an unexpected HTTP response. Check its address and try again."}
    except (OSError, URLError, ValueError, TypeError):
        return {"state": "offline", "message": "The expected local app could not be reached. Start it, check the port, then try again."}


class AdvancedTools:
    def __init__(self, directory, hardware=None):
        self.directory = Path(directory)
        self.path = self.directory / "advanced_tools.json"
        self.hardware = hardware or (lambda: {"os": platform.system()})
        self.lock = threading.RLock()

    def _read(self):
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("tools"), dict):
                raise ValueError("Invalid settings")
            result = {}
            for key, settings in data["tools"].items():
                if key not in TOOLS or not isinstance(settings, dict):
                    raise ValueError("Invalid tool")
                result[key] = self._validate(TOOLS[key], settings)
            return result
        except (ValueError, OSError, SetupError) as exc:
            raise SetupError("Advanced tool settings could not be read. Restore the local advanced_tools.json file before saving changes.", 503) from exc

    @staticmethod
    def _validate(tool, data, blocked_ports=()):
        if set(data) - {"folder", "model", "url"}:
            raise SetupError("Unsupported tool settings.")
        settings = {"folder": local_path(data.get("folder", ""))}
        if tool["default_url"]:
            settings["url"] = local_url(data.get("url") or tool["default_url"], blocked_ports)
            if data.get("model"):
                raise SetupError("This tool manages models in its own application.")
        else:
            settings["model"] = local_path(data.get("model", ""))
            if data.get("url"):
                raise SetupError("Local Transcription uses files, not a web connection.")
        return settings

    def save(self, tool_id, data, blocked_ports=()):
        tool = TOOLS.get(tool_id)
        if not tool:
            raise SetupError("Unknown advanced tool.", 404)
        settings = self._validate(tool, data, blocked_ports)
        with self.lock:
            records = self._read()
            records[tool_id] = settings
            self._write(records)
        return {"message": "Locations saved on this device. No software was installed or started."}

    def _write(self, records):
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"version": 1, "tools": records}, stream, indent=2)
        os.replace(temporary, self.path)

    def forget(self, tool_id):
        if tool_id not in TOOLS:
            raise SetupError("Unknown advanced tool.", 404)
        with self.lock:
            records = self._read()
            records.pop(tool_id, None)
            self._write(records)
        return {"message": "Saved locations removed. Application and model files were kept."}

    @staticmethod
    def files(tool_id, settings):
        folder = Path(settings["folder"]) if settings.get("folder") else None
        found = False
        free_gb = None
        model = bool(settings.get("model") and Path(settings["model"]).is_file())
        if folder and folder.is_dir():
            free_gb = round(shutil.disk_usage(folder).free / 1024 ** 3, 1)
            markers = {
                "framepack": ("demo_gradio.py", "webui/demo_gradio.py", "FramePack/demo_gradio.py"),
                "comfyui": ("main.py", "ComfyUI/main.py", "resources/ComfyUI/main.py"),
                "whispercpp": ("whisper-cli.exe", "whisper-cli", "build/bin/whisper-cli", "build/bin/whisper-cli.exe", "build/bin/Release/whisper-cli.exe"),
            }
            found = any((folder / name).is_file() for name in markers[tool_id])
        return {"folder_exists": bool(folder and folder.is_dir()), "app_files_found": found,
                "model_found": model, "free_gb": free_gb,
                "label": "App files found" if found else "App files not confirmed"}

    def catalog(self):
        with self.lock:
            records = self._read()
        try:
            hw = self.hardware()
        except (OSError, ValueError):
            hw = {"os": platform.system()}
        items = []
        for tool in CATALOG:
            settings = records.get(tool["id"], {"folder": "", "url": tool["default_url"]} if tool["default_url"] else {"folder": "", "model": ""})
            try:
                files = self.files(tool["id"], settings)
            except OSError:
                files = {"label": "File check unavailable", "app_files_found": False, "model_found": False, "free_gb": None}
            item = copy.deepcopy(tool)
            item.pop("health_path")
            items.append({**item, "settings": settings, "saved": tool["id"] in records,
                          "compatibility": compatibility(tool["id"], hw), "files": files})
        return {"tools": items, "os": hw.get("os", platform.system()), "checked_at": int(time.time())}

    def check(self, tool_id, blocked_ports=()):
        tool = TOOLS.get(tool_id)
        if not tool:
            raise SetupError("Unknown advanced tool.", 404)
        with self.lock:
            settings = self._read().get(tool_id, {})
        if not tool["default_url"]:
            files = self.files(tool_id, settings)
            return {"state": "files-found" if files["app_files_found"] and files["model_found"] else "incomplete",
                    "message": "Application and model files found. Execution has not been tested." if files["app_files_found"] and files["model_found"] else "Save a folder containing whisper-cli and an existing model file, then check again."}
        address = local_url(settings.get("url") or tool["default_url"], blocked_ports)
        return probe(tool, address)


def handle_http(handler, directory, hardware=None):
    path = urlsplit(handler.path).path
    if not path.startswith("/advanced-tools/"):
        return False
    try:
        if not setup_allowed(handler):
            raise SetupError("Open Settings on the computer running DATA to manage its advanced tools.", 403)
        if hasattr(handler, "_check_auth") and not handler._check_auth():
            return True
        manager = manager_for(directory, hardware)
        parts = path[len("/advanced-tools/"):].split("/")
        if handler.command == "GET" and parts == ["catalog"]:
            payload = manager.catalog()
        elif handler.command == "POST" and len(parts) == 2:
            if handler.headers.get_content_type() != "application/json":
                raise SetupError("Use a JSON request.", 415)
            if handler.headers.get("Transfer-Encoding") or len(handler.headers.get_all("Content-Length", [])) != 1:
                raise SetupError("A single Content-Length is required.", 411)
            try:
                length = int(handler.headers.get("Content-Length", "0"))
            except ValueError:
                raise SetupError("Invalid request size.") from None
            if not 0 < length <= 8192:
                raise SetupError("Request is too large or empty.", 413)
            handler.connection.settimeout(5)
            try:
                data = json.loads(handler.rfile.read(length))
            except ValueError:
                raise SetupError("Invalid JSON.") from None
            if not isinstance(data, dict):
                raise SetupError("Use a JSON object.")
            tool_id, action = parts
            blocked = (handler.server.server_address[1],)
            if action == "save":
                payload = manager.save(tool_id, data, blocked)
            elif action in ("check", "forget") and not data:
                payload = manager.check(tool_id, blocked) if action == "check" else manager.forget(tool_id)
            else:
                raise SetupError("Unknown advanced tool action.", 404)
        else:
            raise SetupError("Unknown advanced tool endpoint.", 404)
        status = 200
    except SetupError as exc:
        status, payload = exc.status, {"error": str(exc)}
    except OSError:
        status, payload = 503, {"error": "Local tool settings or files could not be accessed. Check file permissions and disk space."}
    body = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(body)
    return True


_managers = {}
_managers_lock = threading.Lock()


def manager_for(directory, hardware=None):
    key = str(Path(directory).resolve())
    with _managers_lock:
        if key not in _managers:
            _managers[key] = AdvancedTools(directory, hardware)
        return _managers[key]
