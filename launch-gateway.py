"""Launch the gateway with real standard handles, including at hidden login."""
import os
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent
state = root / '.runtime'
env = os.environ.copy()
env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
with (state / 'gateway.log').open('ab') as out, (state / 'gateway-error.log').open('ab') as err:
    process = subprocess.Popen(
        [str(root / '.venv/Scripts/python.exe'), '-X', 'utf8', '-u', str(root / 'gateway.py')],
        cwd=root, env=env, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
(state / 'gateway.pid').write_text(str(process.pid))
print('Gateway started, PID', process.pid)
