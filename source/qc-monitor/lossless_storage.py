"""Byte-preserving ZIP storage. No parsing, rounding, deduplication or truth join."""
import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path, PurePosixPath

from records import ROOT, sha256


def within_work(path):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise ValueError("New outputs must remain under 19-Completion-Work")
    return path


def inventory_files(folder):
    folder = Path(folder)
    files = {}
    for p in sorted(folder.rglob('*')):
        if p.is_symlink():
            raise ValueError("Links are not permitted in evidence bundles")
        if p.is_file():
            files[p.relative_to(folder).as_posix()] = {"sha256": sha256(p), "bytes": p.stat().st_size}
    if not files:
        raise ValueError("Empty evidence bundle")
    return files


def verify_zip(path, expected, extract_to=None):
    """Stream every member through SHA256; optionally extract to a NEW directory."""
    target = within_work(extract_to) if extract_to is not None else None
    if target is not None:
        target.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise ValueError("Archive member roster differs")
        for name in names:
            rel = PurePosixPath(name)
            if rel.is_absolute() or '..' in rel.parts or '\\' in name or ':' in name:
                raise ValueError("Unsafe archive member")
            info = archive.getinfo(name)
            if info.file_size != expected[name]['bytes']:
                raise ValueError("Archive member length differs: " + name)
            h = hashlib.sha256()
            count = 0
            destination = None
            if target is not None:
                p = target.joinpath(*rel.parts)
                p.parent.mkdir(parents=True, exist_ok=True)
                destination = p.open('xb')
            try:
                with archive.open(name) as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        count += len(chunk)
                        h.update(chunk)
                        if destination is not None:
                            destination.write(chunk)
            finally:
                if destination is not None:
                    destination.close()
            if count != expected[name]['bytes'] or h.hexdigest() != expected[name]['sha256']:
                raise ValueError("Archive member hash differs: " + name)
    if target is not None and inventory_files(target) != expected:
        raise ValueError("Extracted files differ")
    return {"members": len(expected), "bytes": sum(x['bytes'] for x in expected.values()),
            "all_member_hashes_match": True, "extracted": target is not None}


def pack(folder, destination):
    """Save a fresh archive, verify decompressed bytes, then publish its final name."""
    folder, destination = Path(folder), within_work(destination)
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected = inventory_files(folder)
    partial = destination.with_name(destination.name + '.partial')
    with zipfile.ZipFile(partial, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in expected:
            archive.write(folder / name, name)
    verify_zip(partial, expected)
    if inventory_files(folder) != expected:
        raise ValueError("Source changed during compression")
    with partial.open('r+b') as f:
        os.fsync(f.fileno())
    partial.rename(destination)
    return {"sha256": sha256(destination), "bytes": destination.stat().st_size,
            "uncompressed_bytes": sum(x['bytes'] for x in expected.values()), "files": expected,
            "codec": "ZIP DEFLATE level 6", "verified_readback": True}


def check_bundle(path, manifest):
    if sha256(path) != manifest['sha256'] or Path(path).stat().st_size != manifest['bytes']:
        raise ValueError("Compressed archive changed or truncated")
    return verify_zip(path, manifest['files'])


def remove_verified_scratch(scratch, attempt, manifest):
    """Only remove this controller's new successful scratch after archive verification."""
    scratch, attempt = within_work(scratch), within_work(attempt)
    if scratch.parent != attempt or scratch.name not in ('public-scratch', 'private-scratch'):
        raise ValueError("Refusing cleanup outside exact attempt scratch")
    if inventory_files(scratch) != manifest['files']:
        raise ValueError("Scratch differs from archive manifest")
    shutil.rmtree(scratch)
