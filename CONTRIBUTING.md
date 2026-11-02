# 기여 안내

설치는 [README.md](README.md), 작업 지침은 [AGENTS.md](AGENTS.md)를 먼저 확인한다. 기능 변경은 어떤 입력에서 어떤 동작이 달라지는지 설명하고, 관련된 검증 결과를 함께 남긴다.

## 개발 환경

Windows x64와 Python 3.12를 사용한다. 백엔드는 Windows 파일 핸들 검사를 사용하므로 비 Windows 환경에서 전체 앱 테스트를 통과했다고 가정하지 않는다. Node.js는 UI 테스트용이며 서버 실행의 필수 의존성은 아니다.

```powershell
.\bootstrap.ps1 -SkipAccountSetup
```

테스트는 임시 디렉터리와 합성 계정을 사용한다. 실제 운영 폴더 대신 별도 clone에서 실행하고, 사용자의 대화·비밀번호·첨부를 테스트 데이터로 복사하지 않는다. 테스트만 실행할 때 모델을 다운로드하거나 서버 포트를 열 필요는 없다.

## 검사

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_inference_queue test_sandbox_api test_sandbox_fs test_local_urls test_vpn_access
node test_queue_ui.cjs
```

계정 관리와 부트스트랩 변경은 해당 테스트 파일도 실행한다. Linux 릴레이를 수정했다면 다음 독립 테스트를 추가로 실행한다.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s deploy/aws-relay -p test_relay.py -v
```

모델 없이 실행하는 단위 테스트는 실제 생성 품질, GPU 메모리 사용량, 비전 모델의 정상 동작 또는 외부 DNS·TLS를 검증하지 않는다. 관련 기능을 수정했다면 그 기능의 실제 검사를 별도로 수행하고 수행하지 못한 범위도 적는다.

## 변경 제안

- 파일 경계, 소유권 검사, 헤더 신뢰 규칙 또는 대기열 취소 순서를 바꾸는 경우 해당 실패 시나리오를 재현하는 검사를 포함한다.
- UI 변경은 모바일 폭, Enter/Shift+Enter, 한국어 IME, 대기·취소·오류 표시를 확인한다.
- 설정 예제에는 예약된 예시 도메인과 합성 데이터를 사용한다. 운영 계정, 실제 접속 주소, 토큰을 넣지 않는다.
- 외부 라이브러리 파일을 갱신할 때 버전 정보와 라이선스 고지를 함께 갱신한다.

커밋 전에 `git diff --cached`와 추적 파일 목록을 확인한다. `.runtime/`, `.venv/`, `bin/`, `agent/` 및 개인키가 포함되지 않아야 한다. 보안 제보는 [SECURITY.md](SECURITY.md)의 비공개 절차를 따른다.
