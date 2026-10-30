"""CPU-only embeddings and resumable local document retrieval. No cloud calls."""
from __future__ import annotations

import array
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import threading
import time
import urllib.request

MODEL_NAME = 'Qwen3-Embedding-0.6B-Q8_0.gguf'
MODEL_SIZE = 639150592
MODEL_SHA256 = '06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439'
MODEL_URL = 'https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/370f27d7550e0def9b39c1f16d3fbaa13aa67728/' + MODEL_NAME
SCHEMA = MODEL_SHA256 + ':prefix-v1:last:1600'
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def normalize(vector):
    values = [float(x) for x in vector]
    if not values or not all(math.isfinite(x) for x in values):
        raise ValueError('Invalid embedding vector')
    norm = math.sqrt(sum(x * x for x in values))
    if norm == 0:
        raise ValueError('Empty embedding vector')
    return [x / norm for x in values]


class Retriever:
    def __init__(self, root: Path, encoder=None):
        self.root = Path(root)
        self.model = self.root / 'models' / MODEL_NAME
        self.process = None
        self.token = secrets.token_urlsafe(24)
        self.port = 7791
        self.lock = threading.RLock()
        self.encoder = encoder
        self.last_error = ''
        self.process_lock = None

    @contextmanager
    def db(self):
        (self.root / 'state').mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.root / 'state' / 'embeddings.db', timeout=30)
        conn.execute('CREATE TABLE IF NOT EXISTS vectors (hash TEXT PRIMARY KEY, vec BLOB NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS document_vectors (id TEXT PRIMARY KEY, hash TEXT NOT NULL)')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def available(self):
        return self.encoder is not None or (self.model.is_file() and self.model.stat().st_size == MODEL_SIZE)

    def status(self):
        with self.db() as conn:
            cached = conn.execute('SELECT count(*) FROM vectors').fetchone()[0]
            docs = conn.execute('SELECT count(*) FROM document_vectors').fetchone()[0]
        return {'installed': self.available(), 'model': MODEL_NAME, 'bytes': MODEL_SIZE,
                'cached_passages': cached, 'indexed_document_chunks': docs,
                'scope': 'Semantic search of indexed manuals; semantic ranking of Kiwix keyword results. ZIMs are not fully vector-indexed.',
                'error': self.last_error}

    def stop(self):
        with self.lock:
            proc, self.process = self.process, None
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait()
            if self.process_lock is not None:
                self.process_lock.close()
                self.process_lock = None

    def request(self, route, data=None, timeout=90):
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}' + route,
            data=json.dumps(data).encode() if data is not None else None,
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token})
        with LOCAL.open(request, timeout=timeout) as response:
            return json.load(response)

    def ensure(self):
        if self.process is not None and self.process.poll() is None:
            return
        if not self.available():
            raise RuntimeError('Embedding model is not installed')
        # Only one embedding worker per archive, even if manual indexing and
        # a browser query arrive through different Python processes.
        lock = (self.root / 'state' / 'embedding-worker.lock').open('a+b')
        lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            raise RuntimeError('Manual indexing is using the embedding worker; keyword search remains available')
        self.process_lock = lock
        exe = self.root / 'runtime' / 'llama' / 'llama-server.exe'
        argv = [str(exe), '-m', str(self.model), '--host', '127.0.0.1', '--port', str(self.port),
                '--api-key', self.token, '--embedding', '--pooling', 'last', '--device', 'none',
                '-ngl', '0', '-t', '4', '-c', '2048', '-b', '2048', '-ub', '2048', '-np', '1', '--no-webui']
        (self.root / 'logs').mkdir(exist_ok=True)
        try:
            with (self.root / 'logs' / 'embeddings.log').open('ab') as log:
                self.process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                                creationflags=0x08000000 if os.name == 'nt' else 0)
        except OSError:
            self.stop()
            raise
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('Local embedding server exited. Check logs/embeddings.log.')
            try:
                if self.request('/health', timeout=1).get('status') == 'ok':
                    return
            except Exception:
                time.sleep(.3)
        self.stop()
        raise RuntimeError('Local embedding server did not become ready')

    @staticmethod
    def prepared(text, query=False):
        text = str(text).strip()[:1600]
        return ('Instruct: Retrieve relevant practical reference passages for the question.\nQuery: ' + text) if query else text

    def key(self, text, query=False):
        return hashlib.sha256((SCHEMA + self.prepared(text, query)).encode()).hexdigest()

    def embed(self, text, query=False):
        key = self.key(text, query)
        with self.lock:
            with self.db() as conn:
                row = conn.execute('SELECT vec FROM vectors WHERE hash=?', (key,)).fetchone()
            if row:
                vector = array.array('f'); vector.frombytes(row[0]); return list(vector)
            if self.encoder:
                vector = self.encoder(self.prepared(text, query))
            else:
                self.ensure()
                response = self.request('/v1/embeddings', {'input': self.prepared(text, query), 'encoding_format': 'float'})
                vector = response['data'][0]['embedding']
            vector = normalize(vector)
            with self.db() as conn:
                conn.execute('INSERT OR REPLACE INTO vectors VALUES (?,?)', (key, array.array('f', vector).tobytes()))
            return vector

    @staticmethod
    def doc_id(source):
        return hashlib.sha256((source['url'] + '\n' + source['text']).encode()).hexdigest()

    def index_documents(self, sources, progress=print):
        """Each vector commits immediately; an interrupted pass resumes from cache."""
        total = len(sources)
        ids = []
        for i, source in enumerate(sources):
            text = source['title'] + '\n' + source['text']
            self.embed(text)
            ident = self.doc_id(source); ids.append(ident)
            with self.db() as conn:
                conn.execute('INSERT OR REPLACE INTO document_vectors VALUES (?,?)', (ident, self.key(text)))
            if i % 20 == 0 or i == total - 1:
                progress(f'Indexed manuals: {i + 1}/{total} chunks')
        # A completed rebuild removes references to deleted/edited documents.
        with self.db() as conn:
            existing = [r[0] for r in conn.execute('SELECT id FROM document_vectors')]
            wanted = set(ids)
            conn.executemany('DELETE FROM document_vectors WHERE id=?', [(i,) for i in existing if i not in wanted])
        return total

    def document_search(self, query, current_sources, limit=6):
        if not current_sources:
            return []
        q = self.embed(query, True)
        current = {self.doc_id(s): s for s in current_sources}
        scored = []
        with self.db() as conn:
            rows = conn.execute('SELECT d.id,v.vec FROM document_vectors d JOIN vectors v ON d.hash=v.hash')
            for ident, blob in rows:
                if ident not in current:
                    continue
                vector = array.array('f'); vector.frombytes(blob)
                if len(vector) != len(q):
                    continue
                score = sum(a*b for a, b in zip(q, vector))
                scored.append((score, current[ident]))
        return [dict(s, semantic_score=round(score, 4)) for score, s in sorted(scored, key=lambda p: p[0], reverse=True)[:limit]]

    def rank(self, query, sources, limit=8):
        if not sources:
            return []
        q = self.embed(query, True)
        scored = []
        for source in sources:
            vector = self.embed(source['title'] + '\n' + source['text'])
            if len(vector) != len(q):
                raise ValueError('Embedding dimension mismatch')
            score = sum(a*b for a, b in zip(q, vector))
            scored.append((score, source))
        self.last_error = ''
        return [dict(s, semantic_score=round(score, 4)) for score, s in sorted(scored, key=lambda p: p[0], reverse=True)[:limit]]
