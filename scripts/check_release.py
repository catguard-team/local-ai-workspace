"""Check the explicit source manifest; never print a matched secret value."""
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIRS = {'.runtime', '.venv', '.computer-venv', 'bin', 'agent', 'workspaces', '__pycache__', '.git', 'dist'}
PRIVATE_SUFFIXES = {'.pem', '.key', '.pfx', '.p12', '.dpapi', '.db', '.sqlite', '.sqlite3', '.log', '.gguf', '.safetensors', '.pyc'}
SECRET_PATTERNS = (
    re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    re.compile(rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    re.compile(rb'\bgh[pousr]_[A-Za-z0-9]{30,}\b'),
    re.compile(rb'\bgithub_pat_[A-Za-z0-9_]{40,}\b'),
)


def source_files(root=ROOT):
    root = Path(root).resolve()
    manifest = root / 'release-files.txt'
    names = [line.strip() for line in manifest.read_text(encoding='utf-8').splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    if not names or len(names) != len(set(names)):
        raise ValueError('Manifest is empty or contains duplicates')
    files = []
    for name in names:
        rel = PurePosixPath(name)
        if ('\\' in name or ':' in name or rel.is_absolute() or '..' in rel.parts
                or any(part in PRIVATE_DIRS for part in rel.parts)
                or rel.suffix.lower() in PRIVATE_SUFFIXES or rel.name.startswith('.env')):
            raise ValueError('Private or unsafe path in manifest: ' + name)
        path = root.joinpath(*rel.parts)
        for part in (path, *path.parents):
            if part == root:
                break
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Filesystem link in source manifest: ' + name)
        info = path.stat()
        if not path.is_file() or info.st_nlink != 1 or info.st_size > 2 * 1024 * 1024:
            raise ValueError('Unexpected source file type, hard link or size: ' + name)
        data = path.read_bytes()
        if b'\0' in data:
            raise ValueError('Binary file in source manifest: ' + name)
        if any(pattern.search(data) for pattern in SECRET_PATTERNS):
            raise ValueError('Potential credential found; inspect locally: ' + name)
        files.append((name, path))
    return files


def check_tracked(files, root=ROOT):
    if not (root / '.git').exists():
        return
    result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z'], check=True, capture_output=True)
    tracked = {name for name in result.stdout.decode('utf-8').split('\0') if name}
    extra = tracked - {name for name, _ in files}
    if extra:
        raise ValueError('Tracked files outside release manifest: ' + ', '.join(sorted(extra)))


def main():
    try:
        files = source_files()
        check_tracked(files)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f'PASS: {len(files)} allowlisted text files; no private paths or recognized credential patterns.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

