# Local AI Server

Windows PC의 LM Studio 모델을 브라우저에서 사용하는 한국어 채팅·파일 에이전트입니다. 사용자 로그인, 대화 기록, 문서·이미지 첨부, 순차 요청 처리, 모바일 화면을 제공합니다.

**Windows 전용이며, 모델의 파일 작업은 저장소의 `agent/` 폴더로 제한됩니다. 터미널과 코드 실행 기능은 없습니다.** 이 프로젝트는 OpenAI Codex의 복제나 동일한 모델·실행 환경을 제공하지 않습니다.

## 제공 기능

| 항목 | 동작 |
| --- | --- |
| 로그인 | 이메일이 아닌 사용자 이름과 비밀번호. 공개 회원가입 없음 |
| 채팅·에이전트 | 기본은 채팅. 같은 대화에서 모드 전환 |
| 기록·첨부 | 계정별 비공개 저장. SQLite와 로컬 파일 사용 |
| 이미지·문서 | 비전 모델의 이미지 입력, 텍스트·PDF·DOCX 내용 추출 |
| 에이전트 | 목록, 읽기, 쓰기, 부분 수정, 폴더 생성, 검색의 6가지 파일 도구 |
| 대기열 | 한 번에 한 답변 또는 에이전트 작업. 대기 순서·취소 표시 |
| 화면 | 모바일 대응, Markdown 렌더링, 서식 기호 없는 텍스트 복사 |
| 키보드 | Enter 전송, Shift+Enter 줄바꿈, 한글 조합 중 전송 방지 |
| 외부 접속 | 선택 사항. HTTPS 프록시와 WireGuard를 별도로 구성 |

대화와 첨부는 계정별로 분리되지만 **`agent/` 파일 작업 공간은 모든 계정이 공유**합니다. 계정별 파일 격리가 필요한 환경에는 현재 구성을 그대로 사용하지 마세요. 파일 경계는 Windows 파일 핸들로 검증하는 애플리케이션 제한이며 OS 컨테이너가 아닙니다.

## 준비 사항

- Windows x64. Windows 11과 Python 3.12에서 검증했습니다.
- `venv`와 `ensurepip`가 있는 완전한 Python 3.12 x64 설치.
- [LM Studio](https://lmstudio.ai/)와 `lms` CLI. LM Studio를 한 번 실행한 뒤 [CLI 설치 확인](https://lmstudio.ai/docs/cli)을 진행하세요.
- 사용할 GGUF 모델. 모델 가중치·비전 projector·LM Studio·Python 바이너리는 이 저장소에 포함하지 않습니다.
- 개발 테스트를 실행할 때만 Node.js 22 이상이 추가로 필요합니다.

기본 모델은 [Qwen3.8 27B Uncensored HauhauCS Aggressive MTP GGUF](https://huggingface.co/HauhauCS/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF)입니다. 원래 설치에서 IQ4_XS와 비전 projector를 사용했습니다. 모델 파일명, 다운로드 방법, 라이선스는 모델 카드를 확인하세요. 이미지를 사용하려면 LM Studio에서 비전 지원 모델과 필요한 projector가 함께 준비되어야 합니다.

참고 환경은 RAM 32GB와 VRAM 8GB입니다. 기본 시작값은 GPU offload 25%, 컨텍스트 32,768, 동시 추론 1개이며 MTP 초안 기능은 끕니다. 27B 모델은 CPU와 RAM도 사용하므로 응답이 느릴 수 있습니다. 이 수치는 모든 PC의 최소 사양이나 성능 보장이 아닙니다.

## 빠른 시작

저장소를 `C:\Projects\local-ai-server` 같은 일반 폴더에 두고 그 폴더에서 PowerShell을 엽니다. 운영 중인 다른 복사본이 같은 포트를 사용한다면 동시에 시작하지 마세요.

### 1. Python 환경과 계정 준비

Python Launcher에서 3.12를 찾을 수 있다면:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1
```

Python 실행 파일을 직접 지정할 수도 있습니다. 아래 경로는 실제 설치 경로로 바꾸세요.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1 -Python "C:\Path\To\Python312\python.exe"
```

설치 프로그램은 완전한 Python 런타임을 `bin/python312`에 준비하고, 이를 사용하는 `.venv`를 새로 생성한 뒤 `requirements.txt`와 `requirements-lock.txt`에 고정한 의존성을 설치합니다. Codex 같은 패키지 앱의 가상 AppData 경로에 의존하지 않도록 만든 절차입니다. 기존 가상환경을 다른 PC로 복사하지 말고 [Python venv 문서](https://docs.python.org/3.12/library/venv.html)처럼 새 환경을 만드세요.

마지막에 사용자 이름과 비밀번호를 입력합니다. 기본 계정이나 기본 비밀번호는 없습니다. 사용자 이름은 영문 소문자·숫자 등 3–64자이며, 비밀번호는 12자 이상·UTF-8 72바이트 이하입니다. 비밀번호를 명령 인수나 소스 파일에 적지 않습니다.

계정 생성 없이 환경만 준비하려면 `-SkipAccountSetup`을 붙입니다. 나중에 계정을 추가하거나 비밀번호를 재설정할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe setup_accounts.py create
.\.venv\Scripts\python.exe setup_accounts.py list
.\.venv\Scripts\python.exe setup_accounts.py reset-password your_username
```

비밀번호 재설정은 해당 계정의 기존 로그인 세션도 만료시킵니다. 다른 계정의 기록이나 대기 중인 요청을 초기화하지 않습니다.

### 2. LM Studio 모델 준비

LM Studio에서 모델을 내려받거나 가져옵니다. `lms ls`로 로컬 모델 키를 확인합니다.

```powershell
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" ls
```

기본 모델 키가 맞으면 다음 명령으로 시작합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

모델 키가 다르거나 메모리 설정을 조절해야 하면 다음 인수를 사용합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -ModelKey "your-local-model-key" -GpuOffload 0.25 -ContextLength 32768
```

`ModelKey`는 모델을 자동 다운로드하는 주소가 아니라 **이미 설치한 LM Studio 모델의 키**입니다. 시작 스크립트는 API 식별자를 `qwen3.8-27b`로 고정합니다. 모델을 바꾸더라도 비전·도구 호출 지원 여부는 해당 모델에 따라 다릅니다.

### 3. 브라우저 접속

[http://127.0.0.1:8787](http://127.0.0.1:8787)에서 만든 계정으로 로그인합니다. 시작 시 `접속주소.txt`도 생성됩니다.

| 포트 | 용도 | 기본 수신 주소 |
| --- | --- | --- |
| 1234 | LM Studio API | `127.0.0.1` |
| 8081 | 인증·대화·파일 에이전트 | `127.0.0.1` |
| 8787 | 브라우저 게이트웨이 | `127.0.0.1` 및 명시적으로 설정한 VPN 주소 |

기본 시작은 공개 터널을 생성하지 않습니다. 다른 인터넷에서 접속하려면 [외부 접속 안내](docs/REMOTE_ACCESS.md)의 DNS·TLS·WireGuard·프록시 설정을 먼저 준비하세요. 설정 예시는 [config/](config)에 있고 실제 비밀값은 `.runtime`에만 저장합니다.

## 재부팅 후 자동 시작

먼저 수동 실행과 로그인이 정상인지 확인한 뒤 등록합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-startup.ps1
```

모델 인수를 변경했다면 등록 명령에도 동일하게 전달하세요. 제거는 `install-startup.ps1 -Remove`입니다. 해당 저장소의 로그인 항목만 관리하며 Windows 자동 로그인이나 PC 재부팅을 실행하지 않습니다.

**Windows 로그인 후** 모델과 웹 서버가 시작됩니다. Windows의 시작 프로그램 지연과 모델 로딩 때문에 몇 분이 걸릴 수 있습니다. 원래 운영 환경에서 실제 재부팅 후 자동 복구·두 계정 로그인·기록 보존을 확인했지만, 이 공유본의 새 PC 설치는 각 환경에서 재검증해야 합니다. [운영 및 복구](docs/OPERATIONS.md)에 확인 순서가 있습니다.

## 요청 처리와 데이터

모든 계정과 두 모드는 하나의 FIFO 대기열을 사용합니다. 한 계정은 실행·대기를 합쳐 최대 2개 요청, 서버는 최대 8개 대기 요청을 받습니다. 실행 제한은 15분, 대기 제한은 30분입니다. 서버 재시작으로 중단된 요청은 자동 재실행하지 않으며 부분 응답을 보존합니다.

`.runtime/`에는 계정 해시, 세션, 대화, 첨부, 로그, VPN 설정이 있습니다. `agent/`는 공유 파일 작업 폴더입니다. 서비스를 멈춘 뒤 이 두 폴더를 별도로 백업하세요. Git과 소스 ZIP에서는 모두 제외됩니다.

## 개발·검증

```powershell
.\.venv\Scripts\python.exe -m unittest discover -p "test_*.py" -v
node test_queue_ui.cjs
.\.venv\Scripts\python.exe scripts/check_release.py
.\.venv\Scripts\python.exe scripts/build_release.py
```

테스트는 임시 DB·작업 폴더·합성 계정을 사용하며 실제 모델 추론이나 운영 서버 재시작을 요구하지 않습니다. GitHub Actions에는 Windows 테스트와 별도의 Linux relay 테스트를 넣었습니다. CI 구성의 로컬 검증과 실제 GitHub 실행은 구분합니다.

2026-11-03 공유본 준비 시 새 환경 bootstrap, Python 테스트 94개(앱 81개·릴레이 13개), 화면 로직 검사 12개, PowerShell 구문·스킬·문서 링크·공개 파일 검사를 통과했습니다. GitHub에서의 CI 실행과 공유본을 새 PC에 설치한 뒤의 실제 모델 추론·재부팅 검증은 아직 수행하지 않았습니다.

소스 ZIP은 `release-files.txt`에 명시한 파일만 포함합니다. 생성물은 `dist/local-ai-server-source.zip`이며 SHA-256 파일도 함께 만듭니다. 저장소 전체를 수동 압축하면 로컬 데이터가 섞일 수 있으므로 배포 스크립트를 사용하세요. 검사기는 알려진 비밀 형식과 경로를 검사하는 보조 도구이며 모든 비밀을 탐지한다는 보장은 없습니다.

## 문서

- [AGENTS.md](AGENTS.md): 코드 에이전트가 지켜야 할 프로젝트 작업 지침
- [AGENT.md](AGENT.md): 에이전트 모드와 파일 경계 설명
- [SKILL.md](SKILL.md): 이 프로젝트 유지보수용 스킬
- [아키텍처](docs/ARCHITECTURE.md), [운영·문제 해결](docs/OPERATIONS.md), [외부 접속](docs/REMOTE_ACCESS.md)
- [기여 안내](CONTRIBUTING.md), [보안 범위와 제보](SECURITY.md), [공유·릴리스 절차](docs/RELEASING.md)

## 라이선스

자체 코드는 [MIT](LICENSE)입니다. 포함된 Marked와 별도 설치하는 모델·실행 프로그램의 조건은 [외부 구성 요소 고지](THIRD_PARTY_NOTICES.md)를 확인하세요.

