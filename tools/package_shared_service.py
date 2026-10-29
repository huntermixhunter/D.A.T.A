"""Package only the shared service source. Never include private dashboard data."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    "dashboard/community_server.py", "dashboard/community_transport.py",
    "dashboard/shared_service.py", "dashboard/marketplace.py", "dashboard/forum.py",
    "dashboard/marketplace_admin.py", "dashboard/forum_admin.py", "dashboard/shared_service_admin.py",
    "dashboard/SHARED_SERVICES.md", "dashboard/MARKETPLACE.md", "dashboard/FORUM.md", "dashboard/ACCOUNT.md",
)


def build(output):
    files = [ROOT / name for name in SOURCE_FILES]
    catalog = ROOT / "dashboard" / "marketplace_catalog"
    files.extend(path for path in catalog.rglob("*") if path.is_file() and path.suffix in (".json", ".md", ".txt"))
    manifest = {}
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(files):
            if source.is_symlink() or not source.resolve().is_relative_to(ROOT):
                raise ValueError("A service source file resolves outside the repository.")
            relative = source.relative_to(ROOT).as_posix()
            payload = source.read_bytes()
            manifest[relative] = hashlib.sha256(payload).hexdigest()
            archive.writestr(relative, payload)
        archive.writestr("SHA256SUMS.json", json.dumps(manifest, indent=2) + "\n")
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("Archive integrity check failed.")
        for name, digest in manifest.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError("Archive digest mismatch.")
    return {"path": str(output), "files": len(manifest), "bytes": output.stat().st_size,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.output), indent=2))


if __name__ == "__main__":
    main()
