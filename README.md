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

### 계정 추가·조회·비밀번호 변경·삭제

계정 관리는 **서버 PC의 실제 설치 폴더에서 PowerShell로** 실행합니다. 웹 화면에는 관리자용 계정 관리 메뉴가 없습니다. 공유본이나 다른 복사본 폴더에서 실행하면 그 복사본의 DB를 관리하므로 설치 위치를 먼저 확인하세요.

```powershell
# 새 계정 추가: 비밀번호는 화면 안내에 따라 숨김 입력
.\.venv\Scripts\python.exe setup_accounts.py create new_user
# 계정 목록
.\.venv\Scripts\python.exe setup_accounts.py list
# 비밀번호 변경: 해당 계정의 기존 로그인 세션도 만료
.\.venv\Scripts\python.exe setup_accounts.py reset-password new_user
```

추가와 비밀번호 변경은 서버 실행 중에도 가능합니다. 삭제는 대기·실행 중인 요청을 마무리하고, 필요한 기록을 백업한 뒤 **백엔드를 멈춘 상태에서만** 가능합니다. 숨김 실행 중인 이 설치본의 백엔드는 다음처럼 중지할 수 있습니다. 다른 계정의 접속도 잠시 중단됩니다.

```powershell
$backendPath = [regex]::Escape((Join-Path $PWD 'sandbox_agent.py')) + '(?:["\s]|$)'
Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq 'python.exe' -and $_.CommandLine -match $backendPath
} | ForEach-Object { Stop-Process -Id $_.ProcessId }

.\.venv\Scripts\python.exe setup_accounts.py delete new_user
# 삭제 확인에 사용자 이름을 정확히 입력합니다.
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

삭제하면 해당 계정·세션·비공개 대화·첨부를 제거합니다. **공유 `agent/` 파일과 다른 계정 데이터는 유지**합니다. 마지막 남은 계정은 삭제할 수 없으므로 필요한 경우 대체 계정을 먼저 만드세요. 백업에 남은 기록은 별도이며, 이 명령은 디스크의 보안 삭제를 수행하지 않습니다. 첨부 파일을 지우지 못한 경우 계정 삭제 후 남은 파일 ID를 표시하고 오류로 종료하므로 안내된 개인 업로드 폴더에서 정리하세요.

### 2. LM Studio 모델 준비

LM Studio에서 모델을 내려받거나 가져옵니다. `lms ls`로 로컬 모델 키를 확인합니다.

```powershell
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" ls
```

모델 준비 후 서버를 시작합니다.

평소에는 **[실행.cmd](실행.cmd)를 더블클릭**하세요. 서버를 켠 뒤 모델의 실제 로드 상태와 웹 화면 준비를 확인하고 **기본 브라우저를 자동으로 엽니다.** 고정 접속 주소를 설정했다면 그 주소를 열고, 설정이 없으면 `http://127.0.0.1:8787/`을 엽니다. 이미 서버가 켜져 있어도 준비 상태를 확인한 뒤 접속 화면을 열 수 있습니다. 처음 시작할 때는 모델 로딩에 시간이 걸릴 수 있습니다.

브라우저를 자동으로 열지 않고 서버만 시작하려면 다음 명령을 사용합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

모델 키가 다르거나 메모리 설정을 조절해야 하면 다음 인수를 사용합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -ModelKey "your-local-model-key" -GpuOffload 0.25 -ContextLength 32768
```

`ModelKey`는 모델을 자동 다운로드하는 주소가 아니라 **이미 설치한 LM Studio 모델의 키**입니다. 시작 스크립트는 API 식별자를 `qwen3.8-27b`로 고정합니다. 모델을 바꾸더라도 비전·도구 호출 지원 여부는 해당 모델에 따라 다릅니다.

### 3. 브라우저 접속

`실행.cmd`에서 열린 화면에 로그인하세요. 시작 명령이 실패하거나 준비 확인이 끝나지 않으면 오류가 있는 실행 창을 유지합니다. 모델과 로컬 서버가 준비되어도 고정 주소의 DNS·TLS·외부 연결이 준비되지 않으면 브라우저를 열지 않고 최대 약 3분간 추가 확인합니다. 오류를 해결한 뒤 `실행.cmd`를 다시 실행하세요. Windows 로그인 자동 시작은 기존처럼 서버만 켜며 브라우저 자동 열기는 수동 `실행.cmd`에만 적용됩니다.

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

모든 계정과 두 모드는 하나의 FIFO 대기열을 사용합니다. 한 계정은 실행·대기를 합쳐 최대 2개 요청, 서버는 최대 8개 대기 요청을 받습니다. 실행 제한은 기본 **60분**, 대기 제한은 **120분**입니다. 서버 재시작으로 중단된 요청은 자동 재실행하지 않으며 부분 응답을 보존합니다.

`.runtime/`에는 계정 해시, 세션, 대화, 첨부, 로그, VPN 설정이 있습니다. `agent/`는 공유 파일 작업 폴더입니다. 서비스를 멈춘 뒤 이 두 폴더를 별도로 백업하세요. Git과 소스 ZIP에서는 모두 제외됩니다.

### 이전 대화 삭제

왼쪽 대화 목록에서 대화 오른쪽의 **삭제**를 누른 뒤 확인 창에서 **삭제**를 선택합니다. 모바일에서는 왼쪽 위 메뉴(☰)로 목록을 엽니다. 현재 대화를 삭제하면 새 대화 화면으로 돌아갑니다. 다른 대화를 삭제하면 작성 중인 입력은 유지됩니다.

대화와 메시지는 영구 삭제됩니다. 계정의 **최근 첨부 보관함**과 공유 `agent/` 파일은 유지됩니다. 답변 중이거나 대기 중인 대화는 먼저 **답변 중지 / 대기 취소**를 누르고 중지가 끝난 뒤 삭제하세요. 다른 계정의 대화는 조회하거나 삭제할 수 없습니다. 백업본의 기록은 별도이며 디스크 보안 삭제 기능은 아닙니다.

### 응답 시간 설정

로그인한 뒤 왼쪽 아래 **⚙ 서버 설정**에서 **응답 제한 (분)**과 **대기 제한 (분)**을 입력하고 **설정 저장**을 누르세요. 모바일에서는 ☰ 메뉴를 먼저 엽니다.

웹에서 저장하면 **재시작 없이 새로 접수하는 요청부터 적용**됩니다. 실행 중인 요청과 이미 대기열에 들어간 요청은 접수 당시 제한을 유지합니다. 모든 로그인 계정이 설정을 공유하며, 마지막으로 저장한 값이 서버 기본값이 됩니다.

설정은 `.runtime/inference-settings.json`에 저장되므로 서버 재시작이나 다음 Windows 로그인 후에도 유지됩니다. 파일이 없으면 60분/120분 기본값입니다. 직접 파일을 편집할 수도 있습니다. [예시 파일](config/inference-settings.json.example):

```json
{
  "max_response_minutes": 60,
  "max_queue_wait_minutes": 120
}
```

| 설정 | 의미 | 기본값 |
| --- | --- | --- |
| `max_response_minutes` | 대기 후 실제 실행이 시작된 시점부터 한 답변과 에이전트 도구 작업 전체의 제한 | 60분 |
| `max_queue_wait_minutes` | 다른 요청 뒤에서 기다릴 수 있는 시간 | 120분 |

단위는 분이며 1~1440의 정수만 허용합니다. 대기 제한은 응답 제한 이상이어야 합니다. 예를 들어 90분 응답/180분 대기는 각각 `90`, `180`으로 설정합니다. 잘못된 설정은 시작 오류로 알려주며 무시하지 않습니다. 제한을 늘려도 모델 속도·메모리·문맥 크기나 출력 토큰 상한은 바뀌지 않습니다.

**파일을 직접 편집한 경우에만 백엔드 재시작이 필요합니다.** 웹 설정을 사용하면 재시작할 필요가 없습니다. 수동 편집은 서버를 종료한 상태에서 한 뒤 `실행.cmd`로 다시 시작하세요.

화면의 대기열 안내와 로그인한 상태의 `/api/queue` → `limits`에서 적용 값을 확인할 수 있습니다(API 단위는 초). 설정은 다음 Windows 로그인 때에도 유지됩니다. 이미 15분 제한으로 중단된 답변은 자동 재개되지 않으므로 다시 요청하세요. 요청 접수는 즉시 반환하고 화면이 진행 상황을 조회하므로 외부 프록시의 연결 유휴 제한을 60분으로 늘릴 필요는 없습니다.

### 이 PC에서 서버 종료

**[종료.cmd](종료.cmd)**를 서버 PC에서 더블클릭하면 **브라우저·로그인·추가 확인 없이 바로 종료를 시작**합니다. 새 요청 접수를 닫고 진행 중·대기 중 답변을 중단합니다. 작성된 부분 응답과 대화·첨부는 남기며, 진행 중인 파일 작업 정리가 끝난 뒤 서버를 끕니다. 이미 끝난 파일 수정은 되돌리지 않습니다. 콘솔에 **서버 종료 완료**가 표시되면 창이 자동으로 닫힙니다. 시작 중이거나 다른 종료 작업이 진행 중이면 해당 작업이 끝나기를 먼저 기다립니다.

서버 PC에서 저장소의 **[서버관리.cmd](서버관리.cmd)**를 더블클릭하면 `http://127.0.0.1:8787/?settings=1`이 열립니다. 로그인 후 설정 창의 **서버 종료… → 서버 종료**를 누르세요. 서버가 실행 중일 때 사용하는 관리 바로가기입니다.

- **웹의 종료 버튼**은 실행·대기 중 요청이 있으면 종료를 거부합니다. 해당 사용자가 답변을 완료하거나 취소하고, 중지 처리가 끝난 다음 다시 누릅니다. `종료.cmd`는 로컬 PC 소유자가 직접 실행하는 명령이므로 요청을 중단하고 종료합니다.
- 종료를 수락한 뒤에는 새 요청을 받지 않습니다. 백엔드 정리가 끝나면 게이트웨이와 LM Studio API를 종료하고 `qwen3.8-27b` 모델을 해제합니다. LM Studio 창은 열려 있을 수 있습니다.
- PC 전원, 대화·첨부, `agent/` 파일, Windows 로그인 자동 시작 설정은 유지합니다. 다른 모델을 임의로 해제하거나 AWS·VPN 서비스를 끄지 않습니다. LM Studio API 종료는 그 API를 함께 사용하는 프로그램에도 영향을 줍니다.
- **외부 주소·VPN 접속에서는 종료할 수 없습니다.** 웹 종료는 로그인과 실제 loopback 연결·로컬 호스트 이름을 확인합니다. `종료.cmd`는 비공개 로컬 파일 신호를 사용하며 로그인 없는 웹 종료 API를 만들지 않습니다. 원격 접속에서는 시간 설정만 변경할 수 있습니다.

다시 켜려면 **[실행.cmd](실행.cmd)**를 실행하세요. 모델 로딩 후 고정 주소로 다시 접속할 수 있습니다. 종료 직후 시작하면 종료 처리가 끝날 때까지 잠시 기다리라는 오류가 나올 수 있습니다. 다음 Windows 로그인 때에는 기존 자동 시작 설정에 따라 다시 시작됩니다.

2026-11-08 기존 운영 PC에서 `종료.cmd`를 실행해 브라우저·로그인 없이 8081·8787·1234 포트 종료와 Qwen 모델 해제를 확인했습니다. 이미 종료된 상태에서 재실행도 통과했으며, `start.ps1`로 다시 켠 뒤 두 계정 접근·설정 유지·HTTPS 접속·기록 보존을 확인했습니다. 새 PC 설치와 Windows 재부팅 검증은 별도입니다.

종료 결과는 `.runtime/server-stop-status.json`의 `phase`에서 확인합니다. `stopped`는 마지막 종료에서 LM Studio 정리 명령까지 성공한 상태이고, `failed`이면 `errors`에 해당 작업이 표시됩니다. 이 파일은 현재 실행 상태를 뜻하지 않습니다. `종료.cmd`는 세 서비스 포트가 닫혔는지도 확인합니다. 종료 확인에 실패하면 오류를 보여 주고 창을 유지합니다. 정리가 오래 걸려도 Python 프로세스를 강제 종료하지 않습니다. Windows 시작 로그와 이 파일은 공개 소스에 포함하지 않습니다.

## 개발·검증

```powershell
.\.venv\Scripts\python.exe -m unittest discover -p "test_*.py" -v
node test_queue_ui.cjs
.\.venv\Scripts\python.exe -m unittest discover -s deploy/aws-relay -p test_relay.py -v
.\.venv\Scripts\python.exe scripts/check_release.py
.\.venv\Scripts\python.exe scripts/build_release.py
```

테스트는 임시 DB·작업 폴더·합성 계정을 사용하며 실제 모델 추론이나 운영 서버 재시작을 요구하지 않습니다. 위 검사를 한 번에 실행하려면 `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check-local.ps1`을 사용합니다. Node.js가 PATH에 없다면 `-Node 'C:\path\to\node.exe'`를 지정하세요.

**GitHub Actions는 사용하지 않습니다.** 이 저장소는 Actions 설정을 비활성화했고 workflow 파일도 포함하지 않습니다. 테스트와 소스 ZIP 생성은 사용자 PC에서만 실행합니다. 기여 시에도 유료 CI, 예약 workflow, 외부 자동 실행을 추가하지 마세요. 기존 외부 서버·도메인의 이용료는 이 로컬 검증 방식과 별도입니다.

2026-11-08 로컬 검증: Python 124개(앱 111개·릴레이 13개), 화면 로직 25개, PowerShell 구문 및 공개 파일 검사를 통과했습니다. 직접 종료의 대기열 중단·파일 작업 정리 대기·중복 실행·시작 충돌·실패 보고를 포함합니다. 모바일 390px 시간 저장·종료 확인 창도 앞선 화면 검증에서 확인했습니다. 제한 만료는 짧은 시간의 합성 요청으로 검증했으며 실제 60분 동안 추론하는 부하 검사는 수행하지 않았습니다. 새 PC 설치 후 실제 모델 추론·로그인 자동 시작·재부팅은 각 환경에서 별도로 확인해야 합니다.

같은 날 자동 브라우저 열기 검사 8개와 주소 선택·시작/종료·공개 파일 회귀 검사를 합쳐 36개를 추가 실행해 통과했습니다. 기존 운영 PC에서 서버를 종료한 뒤 `실행.cmd`로 시작하여 모델 로드, 기본 Edge 브라우저의 접속 화면 자동 열기, HTTPS 정상 응답과 기록·설정 보존을 확인했습니다.

소스 ZIP은 `release-files.txt`에 명시한 파일만 포함합니다. 생성물은 `dist/local-ai-server-source.zip`이며 SHA-256 파일도 함께 만듭니다. 저장소 전체를 수동 압축하면 로컬 데이터가 섞일 수 있으므로 배포 스크립트를 사용하세요. 검사기는 알려진 비밀 형식과 경로를 검사하는 보조 도구이며 모든 비밀을 탐지한다는 보장은 없습니다.

## 문서

- [AGENTS.md](AGENTS.md): 코드 에이전트가 지켜야 할 프로젝트 작업 지침
- [AGENT.md](AGENT.md): 에이전트 모드와 파일 경계 설명
- [SKILL.md](SKILL.md): 이 프로젝트 유지보수용 스킬
- [아키텍처](docs/ARCHITECTURE.md), [운영·문제 해결](docs/OPERATIONS.md), [외부 접속](docs/REMOTE_ACCESS.md)
- [기여 안내](CONTRIBUTING.md), [보안 범위와 제보](SECURITY.md), [공유·릴리스 절차](docs/RELEASING.md)

## 라이선스

자체 코드는 [MIT](LICENSE)입니다. 포함된 Marked와 별도 설치하는 모델·실행 프로그램의 조건은 [외부 구성 요소 고지](THIRD_PARTY_NOTICES.md)를 확인하세요.

