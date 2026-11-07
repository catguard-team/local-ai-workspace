"""Open the configured chat page only after local services and model are ready."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx

from local_urls import public_url

LOCAL_URL = "http://127.0.0.1:8787"
MODEL = "qwen3.8-27b"
LMS = Path.home() / ".lmstudio" / "bin" / "lms.exe"


def ready(client, address):
    for origin in dict.fromkeys(("http://127.0.0.1:8081", LOCAL_URL, address)):
        response = client.get(origin + "/api/health")
        response.raise_for_status()
        health = response.json()
        if health.get("status") != "ok" or health.get("mode") != "sandbox-files-only" or health.get("model") != MODEL:
            return False
    models = client.get("http://127.0.0.1:1234/v1/models")
    models.raise_for_status()
    if not any(model.get("id") == MODEL for model in models.json().get("data", [])):
        return False
    # /v1/models may list downloaded models: verify actual loading separately.
    result = subprocess.run([str(LMS), "ps", "--json"], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=True)
    loaded = json.loads(result.stdout)
    if not isinstance(loaded, list) or not any(isinstance(model, dict) and model.get("identifier") == MODEL for model in loaded):
        return False
    page = client.get(address + "/")
    page.raise_for_status()
    return "text/html" in page.headers.get("content-type", "") and 'id="login-form"' in page.text


def wait_until_ready(timeout=180):
    address = public_url() or LOCAL_URL
    deadline = time.monotonic() + timeout
    # Certificate checks stay enabled; redirects and environment proxies cannot
    # silently substitute a different destination for the selected chat origin.
    with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
        while True:
            try:
                if ready(client, address):
                    return address + "/"
            except (httpx.HTTPError, OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
                pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("준비 상태를 확인하지 못했습니다. LM Studio와 서버 로그, 접속 주소를 확인하세요: " + address)
            time.sleep(min(2, remaining))


def main():
    print("모델과 웹 화면 준비를 확인한 뒤 기본 브라우저를 엽니다…", flush=True)
    try:
        address = wait_until_ready()
        os.startfile(address)
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print("서버 준비 완료. 브라우저 열기: " + address, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
