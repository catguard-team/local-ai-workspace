# 설치 이후 운영

처음 설치는 [README.md](../README.md)의 순서를 따른다. 이 문서는 모델 준비, 계정, 시작 프로그램과 장애 확인을 다룬다.

## Python과 계정

```powershell
.\bootstrap.ps1
```

부트스트랩은 Windows x64용 Python 3.12를 사용한다. `py.exe -3.12`로 찾을 수 없다면 `-Python 'C:\Python312\python.exe'`처럼 설치된 실행 파일을 지정한다. 완전한 Python 런타임을 `bin\python312`에 준비하고 `.venv`와 의존성을 설치한다. `.runtime/`에는 현재 Windows 사용자와 SYSTEM 중심의 접근 권한을 적용한다. 운영 서비스를 시작하거나 로그인 자동 실행을 등록하지는 않는다.

기본 실행은 마지막에 계정 이름과 비밀번호를 묻는다. 설치와 계정 생성을 분리하려면 `-SkipAccountSetup`을 사용한다. 이후 계정 작업은 다음 명령으로 한다.

```powershell
.\.venv\Scripts\python.exe setup_accounts.py create
.\.venv\Scripts\python.exe setup_accounts.py create alice
.\.venv\Scripts\python.exe setup_accounts.py reset-password alice
.\.venv\Scripts\python.exe setup_accounts.py list
```

`alice`는 예시 계정이다. 계정 ID는 3~64자의 영문 소문자·숫자·점·밑줄·하이픈을 사용하며 첫 글자는 영문자 또는 숫자다. 입력한 영문 대문자는 소문자로 정규화한다. 이메일 주소나 `@`는 필요하지 않다.

비밀번호는 최소 12자, UTF-8 인코딩 기준 최대 72바이트이며 터미널에서 숨김 입력한다. 명령 인자나 환경변수로 전달하지 않는다. `reset-password`는 해당 계정의 기존 세션도 만료시킨다. 공유본에 기본 계정과 기본 비밀번호는 없다. 계정이 준비되지 않으면 백엔드는 익명 서비스로 시작하지 않는다.

## LM Studio와 모델

LM Studio에서 사용할 GGUF 모델을 먼저 내려받는다. 모델 파일과 비전용 projector는 저장소에 포함되지 않는다. 이미지 입력에는 모델·추론 엔진이 비전을 지원하고 필요한 projector가 준비되어 있어야 한다. 이 서버의 문서 처리에는 Nomic 임베딩 모델이 필수 조건이 아니다.

LM Studio CLI의 `lms ls`로 로컬 모델 키를 확인한다. 시작 스크립트는 해당 모델을 `qwen3.8-27b`라는 고정 API 식별자로 로드한다. 기본 모델 키는 Qwen3.8 27B Uncensored HauhauCS Aggressive 배포를 기준으로 한다. 보유한 키가 다르면 명시한다.

```powershell
.\start.ps1 -ModelKey 'qwen3.8-27b-uncensored-hauhaucs-aggressive-mtp' -GpuOffload 0.25 -ContextLength 32768
```

기본 GPU offload 0.25와 문맥 32,768은 모든 하드웨어에서 최적인 값이 아니다. 27B 모델과 긴 문맥·이미지는 메모리를 많이 사용하며 CPU offload 시 응답이 느릴 수 있다. 사용 가능한 RAM·VRAM에 맞춰 시작 인자를 조정한다. 모델의 Native MTP draft는 기본 시작 명령에서 끈다.

모델 준비 후 `http://127.0.0.1:8787`을 연다. 추론 API와 백엔드는 계속 loopback에 남는다. 시작 스크립트는 모델 가중치를 자동 다운로드하거나 외부 터널을 임의로 만들지 않는다.

## 로그인 자동 시작

수동 시작과 로그인을 확인한 다음 등록한다.

```powershell
.\install-startup.ps1
```

등록을 제거하려면 다음을 사용한다.

```powershell
.\install-startup.ps1 -Remove
```

수동 실행에서 모델 키나 로딩 설정을 바꿨다면 같은 값을 `install-startup.ps1`의 `-ModelKey`, `-GpuOffload`, `-ContextLength` 인자로 전달하여 자동 시작에도 저장한다.

이는 현재 Windows 사용자의 **로그인 후** 실행이다. PC가 켜졌지만 로그인하지 않은 상태의 무인 서비스와 같지 않다. Windows의 시작 프로그램 지연과 모델 로딩 때문에 준비까지 몇 분이 걸릴 수 있다. 등록 명령 자체는 PC를 재부팅하지 않는다.

Python 런타임을 앱 패키지의 가상 AppData에만 두면 일반 Windows 시작 프로그램에서 찾지 못할 수 있다. `bin\python312`와 `.venv`를 함께 유지한다. 별도 개발 환경의 `python.exe`로 바꾸기 전에 Windows 로그인 환경에서 실제 경로를 확인한다.

## 정상 동작 확인

| 확인 항목 | 확인 방법 | 알 수 있는 범위 |
| --- | --- | --- |
| 모델 메모리 로드 | `lms ps --json` | `qwen3.8-27b`의 실제 로드 상태, 병렬 처리 값 |
| 백엔드 | `http://127.0.0.1:8081/api/health` | 앱 프로세스의 응답 |
| 게이트웨이 | `http://127.0.0.1:8787/api/health` | 로컬 프록시 연결 |
| 계정·대화 | 브라우저 로그인 후 기존 대화 열기 | 인증과 저장 데이터 접근 |
| 실제 추론 | 짧은 질문에 끝까지 답변 받기 | 해당 모델 요청의 완료 |
| 비전 | 알려진 작은 이미지로 질문 | 해당 비전 구성의 동작 |

`/api/health`는 모델의 답변 완료를 대신하지 않는다. 이미지·문서 처리도 따로 확인한다. 외부 주소는 로컬 정상 확인 이후 [원격 접속 검사](REMOTE_ACCESS.md)로 확인한다.

## 장애 확인

| 증상 | 확인할 내용 |
| --- | --- |
| 로그인 후 페이지가 바로 안 열림 | 모델 준비 중인지, `startup-success.log`와 `startup-error.log`의 새 시각 |
| `No Python` 또는 런처 오류 | `.venv/pyvenv.cfg`의 `home`이 실제 접근 가능한 런타임인지 |
| 모델 로드 성공 메시지 뒤 시작 중단 | 프로세스 종료 코드와 stderr를 구분하는 현재 시작 스크립트인지 |
| 외부 502, 로컬 정상 | Nginx upstream, WireGuard handshake, 사설 게이트웨이 주소·방화벽 |
| 로컬 8081 정상, 8787 실패 | 게이트웨이 프로세스와 설정 검증 오류 |
| 대기열이 길어짐 | 실행 중인 긴 요청, 최대 대기 수와 계정별 제한, 불필요한 요청 취소 |

로그는 `.runtime/`에 있다. 파일 이름은 실행 구성에 따라 `startup-error.log`, `agent-launcher-error.log`, `gateway-launcher-error.log`, `computer-error.log`, `gateway-error.log`, `lm-start.log` 등을 사용한다. 로그를 공개하기 전에 주소·계정·문서 내용·토큰이 섞였는지 확인한다.

백엔드를 여러 worker로 늘려 대기열 문제를 해결하지 않는다. 대기열은 단일 프로세스에서 한 요청 전체를 직렬화한다. 서버가 재시작되면 미완료 요청은 중단으로 표시되며 사용자가 다시 제출해야 한다.

## 백업과 업데이트

새 요청이 없는 때에 백엔드와 게이트웨이를 정상 종료하고 `.runtime/`와 `agent/`를 함께 별도 비공개 위치에 백업한다. 실행 중인 SQLite의 `app.db` 하나만 복사하면 WAL의 최신 내용이 빠질 수 있다. 백업에는 계정, 대화, 첨부와 접속 토큰이 포함될 수 있으므로 저장소 밖에 보관한다.

업데이트는 소스와 필요한 의존성만 갱신한다. `.runtime/`나 `agent/`를 새 배포 파일로 덮어쓰지 않는다. 복원 시에는 서비스를 멈춘 상태에서 백업 묶음과 파일 권한을 함께 복구한 뒤 로그인·기록을 확인한다.

## 실제 재부팅 검사

재부팅 전 부팅 시각, 모델·계정·기록 상태를 남기고 사용 중인 요청을 마친다. 재부팅 후 Windows에 로그인하여 시작 프로그램이 자연스럽게 완료되는지 기다린다. 수동으로 서버를 켜기 전에 새 부팅 시각, `startup-success.log`, 모델 로드, 브라우저 로그인과 기존 기록을 확인한다.

원격 접속을 사용한다면 외부 네트워크에서 TLS·로그인 화면까지 확인한다. 서비스 수동 실행이나 수정이 필요했다면 그 부팅은 **수동 복구**로 기록하고 자동 시작 통과로 표시하지 않는다. 공개 저장소에는 특정 운영자의 자동 재부팅 명령이나 인증 정보를 포함하지 않는다.

## 시간 설정과 수동 서버 종료

시간은 웹의 **⚙ 서버 설정**에서 저장하며 새 요청부터 바로 적용된다. 서버 PC에서는 `서버관리.cmd`로 로컬 관리 화면을 열어 **서버 종료…**를 사용할 수 있다. 모든 요청이 완료되거나 취소된 상태에서만 종료된다. 다시 실행은 `실행.cmd`이다. [자세한 동작](../README.md#이-pc에서-서버-종료)을 참고한다. `.runtime/server-stop-status.json`의 종료 상태와 `server-control-error.log`를 확인할 수 있다.
