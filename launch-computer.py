import os
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent
state = root / '.runtime'
env = os.environ.copy()
env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
with (state / 'computer.log').open('ab') as out, (state / 'computer-error.log').open('ab') as err:
    process = subprocess.Popen([str(root / '.venv/Scripts/python.exe'), '-u', str(root / 'sandbox_agent.py')], cwd=root, env=env, stdout=out, stderr=err, stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
(state / 'computer.pid').write_text(str(process.pid))
print('File-only sandbox agent started, PID', process.pid)
