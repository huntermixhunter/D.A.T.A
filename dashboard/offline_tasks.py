"""Small background task runner owned by the dashboard server, never the browser."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

_lock = threading.Lock()
_tasks = {}


def file_locked(path: Path) -> bool:
    """Probe a setup lock without relying on a stale PID from another computer."""
    if not path.is_file():
        return False
    try:
        with path.open('r+b') as stream:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except OSError:
        return True


def start(root: Path, key: str, argv: list[str], label: str) -> dict:
    """Only callers construct argv. No shell or user-provided commands."""
    root = root.resolve()
    with _lock:
        for task in _tasks.values():
            if task['root'] == str(root) and task['key'] == key and task['status'] == 'running':
                return dict(task)
        folder = root / 'jobs'
        folder.mkdir(parents=True, exist_ok=True)
        ident = uuid.uuid4().hex
        log = folder / (ident + '.log')
        task = dict(id=ident, key=key, label=label, root=str(root), status='running',
                    started=time.time(), log=str(log))
        with log.open('ab') as output:
            proc = subprocess.Popen([str(x) for x in argv], stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=subprocess.STDOUT,
                                    creationflags=0x08000000 if os.name == 'nt' else 0)
        task['pid'] = proc.pid
        _tasks[ident] = task
        receipt = folder / (ident + '.json')
        receipt.write_text(json.dumps(task), encoding='utf-8')

        def finish():
            rc = proc.wait()
            with _lock:
                task.update(status='complete' if rc == 0 else 'error', exit_code=rc, finished=time.time())
                receipt.write_text(json.dumps(task), encoding='utf-8')
        threading.Thread(target=finish, daemon=True).start()
        return dict(task)


def recent(root: Path) -> list[dict]:
    """Receipts survive restarts; never report a vanished process as completed."""
    out = []
    for file in sorted((root / 'jobs').glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:12]:
        try:
            task = json.loads(file.read_text(encoding='utf-8'))
            with _lock:
                live = _tasks.get(task['id'])
                if live:
                    task = dict(live)
            if task['status'] == 'running' and not live:
                task['status'] = 'unknown after server restart; check files and log'
            log = root / 'jobs' / (task['id'] + '.log')
            if log.is_file():
                with log.open('rb') as stream:
                    stream.seek(max(0, log.stat().st_size - 1800))
                    task['output'] = stream.read().decode('utf-8', 'replace')
            out.append({k: v for k, v in task.items() if k not in ('root', 'log')})
        except (OSError, ValueError, KeyError):
            continue
    return out


def python_argv(script: Path, *args) -> list[str]:
    # The Windows embedded runtime needs its site directory for document tools.
    return [sys.executable, str(script), *map(str, args)]
