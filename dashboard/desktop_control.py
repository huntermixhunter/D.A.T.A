"""
Desktop control — gives Data hands to match his eyes.

Two surfaces:
  1. Plain functions for our own DIY tools (used by claude-api + ollama runners).
  2. execute_computer_action() — maps Anthropic's native computer-tool actions
     (left_click, type, screenshot, scroll, ...) onto the same calls, so the
     native tool works when ACTIVE_PROVIDER is claude-api*.

Safety: a single on/off flag persists to disk. Default ON because the bridge
already runs with full local privileges; the kill switch is there for when the
Captain wants to lock the keyboard down briefly without restarting.
"""
import os
import io
import json
import time
import base64
import logging
from pathlib import Path

log = logging.getLogger("desktop_control")

_pyautogui = None  # lazy

_STATE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "hermes"
_STATE_DIR.mkdir(parents=True, exist_ok=True)
_STATE_FILE = _STATE_DIR / "desktop_control.json"


def _load_state() -> dict:
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"enabled": True}


def _save_state(s: dict) -> None:
    try:
        _STATE_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"Could not persist desktop_control state: {e}")


_state = _load_state()


def is_enabled() -> bool:
    return bool(_state.get("enabled", True))


def set_enabled(value: bool) -> None:
    _state["enabled"] = bool(value)
    _save_state(_state)


def _gate():
    if not is_enabled():
        raise PermissionError(
            "Desktop control is disabled. POST /desktop_control {\"enabled\": true} to re-arm."
        )


def _pg():
    """Lazy pyautogui import so the bridge boots even without it installed."""
    global _pyautogui
    if _pyautogui is None:
        try:
            import pyautogui
            pyautogui.FAILSAFE = True   # mouse to (0,0) aborts any action
            pyautogui.PAUSE = 0.03
            _pyautogui = pyautogui
        except ImportError as e:
            raise RuntimeError(
                "pyautogui not installed — run: pip install pyautogui pillow"
            ) from e
    return _pyautogui


def is_available() -> bool:
    """True if pyautogui can be imported. Used to decide whether to register
    the native computer tool with the API."""
    try:
        _pg()
        return True
    except Exception:
        return False


# ── Primitive ops ────────────────────────────────────────────────

def screen_size() -> dict:
    w, h = _pg().size()
    return {"width": int(w), "height": int(h)}


def cursor_position() -> dict:
    x, y = _pg().position()
    return {"x": int(x), "y": int(y)}


def mouse_click(x=None, y=None, button: str = "left", clicks: int = 1) -> str:
    _gate()
    pg = _pg()
    if x is not None and y is not None:
        pg.click(int(x), int(y), button=button, clicks=int(clicks))
        return f"Clicked {button} {clicks}x at ({x}, {y})"
    pg.click(button=button, clicks=int(clicks))
    cur = cursor_position()
    return f"Clicked {button} {clicks}x at cursor ({cur['x']}, {cur['y']})"


def mouse_move(x: int, y: int) -> str:
    _gate()
    _pg().moveTo(int(x), int(y))
    return f"Moved cursor to ({x}, {y})"


def mouse_drag(start_x, start_y, end_x, end_y, button: str = "left",
               duration: float = 0.3) -> str:
    _gate()
    pg = _pg()
    pg.moveTo(int(start_x), int(start_y))
    pg.dragTo(int(end_x), int(end_y), button=button, duration=duration)
    return f"Dragged from ({start_x}, {start_y}) → ({end_x}, {end_y})"


def type_text(text: str, interval: float = 0.02) -> str:
    _gate()
    _pg().typewrite(text, interval=interval)
    return f"Typed {len(text)} char(s)"


def key_press(keys: str) -> str:
    """'enter', 'ctrl+c', 'win+r', 'alt+tab', ... — comma-separate to chain."""
    _gate()
    pg = _pg()
    for combo in [k.strip() for k in keys.split(",") if k.strip()]:
        parts = [p.strip().lower() for p in combo.split("+")]
        if len(parts) == 1:
            pg.press(parts[0])
        else:
            pg.hotkey(*parts)
    return f"Pressed {keys}"


def scroll(amount: int, x=None, y=None) -> str:
    _gate()
    pg = _pg()
    if x is not None and y is not None:
        pg.moveTo(int(x), int(y))
    pg.scroll(int(amount))
    return f"Scrolled {amount} clicks"


def screenshot_b64() -> str:
    """Full-screen PNG → base64."""
    img = _pg().screenshot()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ── Anthropic computer-tool action dispatcher ────────────────────

def execute_computer_action(action: str, **kwargs) -> dict:
    """
    Maps an Anthropic computer-tool action to underlying calls.
    Returns {"text": "..."} for normal actions and {"image_b64": "..."} for
    screenshots — the bridge translates the latter into a tool_result image
    block so Data can see the screen after acting.

    Action vocabulary (computer_20250124):
      screenshot, cursor_position, left_click, right_click, middle_click,
      double_click, triple_click, left_click_drag, mouse_move,
      left_mouse_down, left_mouse_up, key, type, scroll, wait, hold_key
    """
    coord = kwargs.get("coordinate") or [None, None]
    x = coord[0] if len(coord) > 0 else None
    y = coord[1] if len(coord) > 1 else None
    text = kwargs.get("text")

    try:
        if action == "screenshot":
            return {"image_b64": screenshot_b64()}

        if action == "cursor_position":
            pos = cursor_position()
            return {"text": f"Cursor at ({pos['x']}, {pos['y']})"}

        if action == "mouse_move":
            return {"text": mouse_move(x, y)}

        if action in ("left_click", "right_click", "middle_click"):
            return {"text": mouse_click(x, y, button=action.split("_")[0], clicks=1)}

        if action == "double_click":
            return {"text": mouse_click(x, y, button="left", clicks=2)}

        if action == "triple_click":
            return {"text": mouse_click(x, y, button="left", clicks=3)}

        if action == "left_click_drag":
            start = kwargs.get("start_coordinate") or [None, None]
            sx = start[0] if len(start) > 0 else None
            sy = start[1] if len(start) > 1 else None
            return {"text": mouse_drag(sx, sy, x, y)}

        if action == "left_mouse_down":
            _gate(); _pg().mouseDown(button="left")
            return {"text": "Left mouse down"}

        if action == "left_mouse_up":
            _gate(); _pg().mouseUp(button="left")
            return {"text": "Left mouse up"}

        if action == "key":
            return {"text": key_press(text or "")}

        if action == "type":
            return {"text": type_text(text or "")}

        if action == "scroll":
            direction = kwargs.get("scroll_direction", "down")
            clicks = int(kwargs.get("scroll_amount", 3))
            sign = {"up": 1, "down": -1}.get(direction, -1)
            return {"text": scroll(sign * clicks, x, y)}

        if action == "wait":
            dur = min(float(kwargs.get("duration", 1)), 5.0)
            time.sleep(dur)
            return {"text": f"Waited {dur}s"}

        if action == "hold_key":
            _gate()
            pg = _pg()
            dur = min(float(kwargs.get("duration", 1)), 5.0)
            keys = [k.strip().lower() for k in (text or "").split("+") if k.strip()]
            for k in keys: pg.keyDown(k)
            time.sleep(dur)
            for k in reversed(keys): pg.keyUp(k)
            return {"text": f"Held {text} for {dur}s"}

        return {"text": f"Unknown computer action: {action}"}

    except PermissionError as e:
        return {"text": str(e)}
    except Exception as e:
        log.exception(f"Computer action {action} failed")
        return {"text": f"Action {action} failed: {e}"}
