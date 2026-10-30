"""Explicit, free downloads and local verification for a portable archive."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from maps_setup import download
from offline_rag import MODEL_NAME, MODEL_SIZE, MODEL_SHA256, MODEL_URL, Retriever

SMALL_NAME = 'Qwen3.5-2B-Q4_K_M.gguf'
SMALL_SIZE = 1280835840
SMALL_SHA = 'aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223'
SMALL_URL = 'https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/f6d5376be1edb4d416d56da11e5397a961aca8ae/' + SMALL_NAME


def say(message):
    print(message, flush=True)


def get_model(root, name, size, digest, url):
    dest = root / 'models' / name
    if dest.is_file() and dest.stat().st_size == size:
        with dest.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() == digest:
                say('Already verified: ' + name)
                return
    reserve = 5 * 1024**3
    budget = root / 'maps' / 'budget.json'
    if budget.is_file():
        reserve = max(reserve, int(json.loads(budget.read_text())['reserve_bytes']))
    if shutil.disk_usage(root).free < size + reserve:
        raise RuntimeError('Insufficient space above the media and free-space reserve')
    download(url, dest, name, sha256=digest, expected_size=size)
    receipt = root / 'state' / 'model-provenance.json'
    old = json.loads(receipt.read_text()) if receipt.is_file() else {}
    old[name] = {'url': url, 'bytes': size, 'sha256': digest, 'verified': time.strftime('%Y-%m-%dT%H:%M:%S')}
    receipt.parent.mkdir(exist_ok=True)
    receipt.write_text(json.dumps(old, indent=2), encoding='utf-8')


def runtime(root):
    spec = importlib.util.spec_from_file_location('portable_archive_runtime', root / 'server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def index(root):
    app = runtime(root)
    try:
        result = app.index_documents()
        say('Text index: ' + json.dumps(result))
        if result['errors']:
            raise RuntimeError('Some manuals could not be indexed: ' + '; '.join(result['errors']))
        count = app.RAG.index_documents(app.document_sources(), say)
        say(f'Semantic manual index ready: {count} chunks. Kiwix collections remain keyword-indexed.')
    finally:
        app.cleanup()


def smoke(root):
    app = runtime(root)
    # Test workers use their own loopback ports while the workspace stays open.
    app.AI_PORT = 7793
    app.BOOK_PORT = 7794
    app.RAG.port = 7795
    try:
        sources = [
            {'title': 'Gaskets', 'url': '/test/gaskets', 'kind': 'test', 'text': 'A gasket forms a seal between mating surfaces to prevent fluid from leaking through the joint.'},
            {'title': 'Compost', 'url': '/test/compost', 'kind': 'test', 'text': 'Composting converts organic plant waste into a soil amendment for a garden.'},
        ]
        ranked = app.RAG.rank('What part stops liquid escaping where two pipe surfaces meet?', sources)
        if ranked[0]['title'] != 'Gaskets':
            raise RuntimeError('Semantic ranking smoke test did not select the relevant source')
        app.RAG.stop()
        say('PASS: real CPU embeddings rank a paraphrased question correctly.')
        books = app.book_search('solar', limit=3)
        if not books:
            raise RuntimeError('Kiwix returned no results for the smoke query')
        say('PASS: Kiwix search returns local source links.')
        app.ensure_ai('2B', 'cpu')
        reply = app.local_json(f'http://127.0.0.1:{app.AI_PORT}/v1/chat/completions',
            {'messages':[{'role':'user','content':'Reply with the single word READY.'}],
             'max_tokens':32,'temperature':0,'chat_template_kwargs':{'enable_thinking':False}}, 180)
        answer = reply['choices'][0]['message'].get('content', '')
        if not answer.strip():
            raise RuntimeError('The 2B CPU model returned no answer')
        say('PASS: portable 2B model answered on CPU: ' + answer[:100])
        record = {'tested_on': time.strftime('%Y-%m-%dT%H:%M:%S'), 'cpu_model': '2B',
                  'semantic_ranking': True, 'kiwix_search': True, 'cpu_answer': True,
                  'surface_hardware_tested': False}
        (root / 'state' / 'offline-expansion-verification.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    finally:
        app.cleanup()


def parity(root):
    """5% PAR2 for each ZIM. Keep sets independent and originals untouched."""
    folder = root / 'runtime' / 'par2'
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / 'par2.exe'
    if not exe.is_file():
        package = root / 'packages' / 'par2cmdline-turbo-1.5.0-win-x64.zip'
        download('https://github.com/animetosho/par2cmdline-turbo/releases/download/v1.5.0/' + package.name, package,
                 sha256='873a6f25822415f432224cc6f734407ca2732004184e002960fc68ed1e99c5d7', expected_size=676747)
        with zipfile.ZipFile(package) as archive:
            member = next((m for m in archive.namelist() if Path(m).name.lower() == 'par2.exe'), None)
            if member is None:
                raise RuntimeError('PAR2 release did not include par2.exe')
            exe.write_bytes(archive.read(member))
    recovery = root / 'recovery'; recovery.mkdir(exist_ok=True)
    baseline = json.loads((root / 'state/integrity.json').read_text(encoding='utf-8'))['files']
    for source in sorted((root / 'library').glob('*.zim')):
        target = recovery / (source.name + '.par2')
        complete = recovery / (source.name + '.complete.json')
        if complete.is_file():
            record = json.loads(complete.read_text())
            if record.get('bytes') == source.stat().st_size and record.get('mtime_ns') == source.stat().st_mtime_ns:
                say('Parity already created: ' + source.name)
                continue
        reserve = int(json.loads((root / 'maps' / 'budget.json').read_text())['reserve_bytes'])
        if shutil.disk_usage(root).free < reserve + source.stat().st_size * .07:
            raise RuntimeError('Not enough free space for parity above the media reserve')
        original = baseline.get(source.relative_to(root).as_posix())
        if not original:
            raise RuntimeError('No integrity baseline for ' + source.name + '. Verify its source before making recovery data.')
        say('Checking original checksum before recovery: ' + source.name)
        with source.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if source.stat().st_size != original['bytes'] or digest != original['sha256']:
            raise RuntimeError('Original differs from its integrity baseline: ' + source.name)
        # A stopped run may have finished writing the set but not its receipt.
        # Verify that set without overwriting recovery files or original books.
        if target.exists():
            result = subprocess.run([str(exe), 'verify', '-B' + str(source.parent), str(target), str(source)])
            if result.returncode:
                raise RuntimeError('Incomplete recovery set requires review: ' + str(target))
            complete.write_text(json.dumps({'bytes':source.stat().st_size, 'mtime_ns':source.stat().st_mtime_ns,
                                           'recovery_percent':5, 'verified':True}), encoding='utf-8')
            continue
        say('Creating 5% recovery blocks: ' + source.name)
        subprocess.run([str(exe), 'create', '-r5', '-m512', '-t4', '-B' + str(source.parent), str(target), str(source)], check=True)
        subprocess.run([str(exe), 'verify', '-B' + str(source.parent), str(target), str(source)], check=True)
        complete.write_text(json.dumps({'bytes':source.stat().st_size, 'mtime_ns':source.stat().st_mtime_ns,
                                       'recovery_percent':5, 'verified':True}), encoding='utf-8')


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser=argparse.ArgumentParser()
    parser.add_argument('command', choices=['embedding','small-model','index','smoke','parity'])
    parser.add_argument('--root', required=True)
    args=parser.parse_args(); root=Path(args.root).resolve()
    try:
        if args.command=='embedding':
            get_model(root, MODEL_NAME, MODEL_SIZE, MODEL_SHA256, MODEL_URL)
        elif args.command=='small-model':
            get_model(root, SMALL_NAME, SMALL_SIZE, SMALL_SHA, SMALL_URL)
        elif args.command=='index': index(root)
        elif args.command=='smoke': smoke(root)
        elif args.command=='parity': parity(root)
        return 0
    except Exception as exc:
        say(f'ERROR: {type(exc).__name__}: {exc}')
        return 1


if __name__=='__main__':
    sys.exit(main())
