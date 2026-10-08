"""Download checksum-verified external tools and build without sudo."""
import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile

from environment import DEPENDENCIES, dependency_dir, tools_dir


def fetch(name, desktop=False):
    dep = DEPENDENCIES[name]
    cache = tools_dir()
    archive = cache / f"{name}-{dep['revision']}.tar.gz"
    if not archive.exists():
        temporary = archive.with_suffix(".download")
        subprocess.run(["curl", "--fail", "--location", "--retry", "3",
                        "--connect-timeout", "15", "--max-time", "180",
                        dep["url"], "--output", str(temporary)], check=True)
        temporary.replace(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != dep["sha256"]:
        raise ValueError(f"checksum mismatch: {archive}; remove the corrupt archive and retry")
    destination = dependency_dir(name)
    if desktop:
        destination = destination.with_name(destination.name + "-desktop")
    stamp = destination / ".verified-archive-sha256"
    if destination.exists():
        if not stamp.is_file() or stamp.read_text().strip() != dep["sha256"]:
            raise ValueError(f"unverified existing source directory: {destination}")
        return destination
    staging = destination.with_name(destination.name + ".extracting")
    if staging.exists():
        # This is exclusively our staging area; final source directories are preserved.
        shutil.rmtree(staging)
    staging.mkdir()
    with tarfile.open(archive) as tar:
        # Python 3.10-compatible validation. Upstream agent metadata has symlinks;
        # omit links entirely (none are used by the pinned build targets).
        members = []
        for member in tar.getmembers():
            if member.issym() or member.islnk():
                continue
            parts = Path(member.name).parts
            if Path(member.name).is_absolute() or ".." in parts:
                raise ValueError(f"unsafe archive path: {member.name}")
            if not member.isdir() and not member.isfile():
                raise ValueError(f"unsupported archive member: {member.name}")
            members.append(member)
        tar.extractall(staging, members=members)
    if name == "actionlint":
        staging.rename(destination)
    else:
        entries = list(staging.iterdir())
        if len(entries) != 1 or not entries[0].is_dir():
            raise ValueError("source archive must have exactly one root")
        entries[0].rename(destination)
        staging.rmdir()
    stamp.write_text(dep["sha256"] + "\n")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=["all", "cc65", "geargrafx", "actionlint"])
    parser.add_argument("--desktop", action="store_true")
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("supported bootstrap host: Linux x86_64")
    required = ["curl", "make", "gcc", "g++", "ar"]
    missing = [name for name in required if not shutil.which(name)]
    if missing:
        raise ValueError(f"missing host tools: {', '.join(missing)}; see docs/building.md")
    if args.desktop:
        if args.target != "geargrafx":
            raise ValueError("--desktop is only valid with geargrafx")
        if not shutil.which("pkg-config") or subprocess.run(
                ["pkg-config", "--exists", "sdl3"]).returncode:
            raise ValueError("desktop/MCP requires SDL3 development files; see docs/emulator-geargrafx.md")
    jobs = int(os.environ.get("JOBS", "2"))
    if not 1 <= jobs <= 64:
        raise ValueError("JOBS must be between 1 and 64")
    cache = tools_dir()
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / ".setup.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        names = ["cc65", "geargrafx", "actionlint"] if args.target == "all" else [args.target]
        for name in names:
            source = fetch(name, args.desktop)
            if name == "cc65":
                subprocess.run(["make", "-C", str(source), f"-j{jobs}",
                                f"BUILD_ID=Git {DEPENDENCIES[name]['revision'][:9]}",
                                "ca65", "ld65", "cc65"], check=True)
            elif name == "geargrafx":
                platform_dir = "platforms/linux" if args.desktop else "platforms/libretro"
                subprocess.run(["make", "-C", str(source / platform_dir),
                                f"-j{jobs}", f"GIT_VERSION={DEPENDENCIES[name]['revision']}"], check=True)
            else:
                (source / "actionlint").chmod(0o755)
            print(f"READY {name}: {DEPENDENCIES[name]['revision']}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"setup: {error}") from error
