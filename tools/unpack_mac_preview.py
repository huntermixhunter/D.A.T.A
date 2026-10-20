"""Extract a Mac runtime for Windows inspection, materializing links as copies.

This is a file preview only. Native Mac builds use tar and preserve real links.
"""
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import sys


def unpack(archive: Path, destination: Path) -> None:
    base = destination.resolve()
    base.mkdir(parents=True, exist_ok=True)
    if (base / "python").exists():
        raise ValueError("Preview output already contains python; use a fresh directory")

    def target(name: str) -> Path:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
            raise ValueError(f"Unsafe archive path: {name}")
        if not path.parts or path.parts[0] != "python":
            raise ValueError(f"Unexpected archive root: {name}")
        result = base.joinpath(*path.parts).resolve()
        if not result.is_relative_to(base):
            raise ValueError(f"Archive path escapes output: {name}")
        return result

    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        links = []
        # Validate the entire inventory before writing files.
        for member in members:
            output = target(member.name)
            if member.issym() or member.islnk():
                link = PurePosixPath(member.linkname)
                if link.is_absolute() or "\\" in member.linkname or ":" in member.linkname:
                    raise ValueError(f"Unsafe archive link: {member.name}")
                linked = (output.parent.joinpath(*link.parts) if member.issym()
                          else base.joinpath(*link.parts)).resolve()
                if not linked.is_relative_to(base / "python"):
                    raise ValueError(f"Archive link escapes runtime: {member.name}")
                links.append((output, linked))
            elif not (member.isfile() or member.isdir()):
                raise ValueError(f"Unsupported archive entry: {member.name}")

        for member in members:
            output = target(member.name)
            if member.isdir():
                output.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                output.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as stream, output.open("xb") as dest:
                    shutil.copyfileobj(stream, dest)

        # Tar archives may put links before their targets, or link to other links.
        while links:
            pending = []
            for output, linked in links:
                if not linked.exists():
                    pending.append((output, linked))
                    continue
                output.parent.mkdir(parents=True, exist_ok=True)
                if linked.is_dir():
                    shutil.copytree(linked, output)
                else:
                    shutil.copy2(linked, output)
            if len(pending) == len(links):
                raise ValueError("Unresolved or cyclic archive links")
            links = pending


if __name__ == "__main__":
    unpack(Path(sys.argv[1]), Path(sys.argv[2]))
    print("Windows preview extracted; archive links are represented by file copies.")
