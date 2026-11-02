"""Build a source-only ZIP from release-files.txt, excluding all local state."""
import hashlib
from pathlib import Path
import zipfile

from check_release import ROOT, source_files, check_tracked


def main():
    files = source_files()
    check_tracked(files)
    output = ROOT / 'dist'
    output.mkdir(exist_ok=True)
    target = output / 'local-ai-server-source.zip'
    temporary = output / 'local-ai-server-source.zip.tmp'
    if output.is_symlink() or (hasattr(output, 'is_junction') and output.is_junction()):
        raise RuntimeError('Release output must be an ordinary local directory')
    for path in (target, temporary):
        if path.is_symlink() or (path.exists() and path.stat().st_nlink != 1):
            raise RuntimeError('Release output cannot be a filesystem link')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, path in files:
            archive.write(path, 'local-ai-server/' + name)
    temporary.replace(target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    checksum = target.with_suffix('.zip.sha256')
    if checksum.is_symlink() or (checksum.exists() and checksum.stat().st_nlink != 1):
        raise RuntimeError('Checksum output cannot be a filesystem link')
    checksum.write_text(f'{digest}  {target.name}\n', encoding='ascii')
    print(f'Built {target.name}: {len(files)} source files, {target.stat().st_size} bytes')
    print('SHA256:', digest)


if __name__ == '__main__':
    main()

