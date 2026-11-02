---
name: maintain-local-ai-server
description: Maintain this Windows LM Studio chat and bounded file-agent server, including its FIFO request queue, login and attachment boundaries, startup, and optional WireGuard HTTPS relay. Use for work on this repository; not for general LLM selection or unrestricted desktop-agent tasks.
---

# Local AI 서버 유지보수

이 저장소의 구체적인 설치·운영·수정 작업에 사용한다. 먼저 [AGENTS.md](AGENTS.md)와 요청에 해당하는 소스 파일을 확인한다. 사용자 요청의 범위와 이미 부여된 승인을 유지한다. 이 스킬 자체가 재부팅, 원격 서버 변경 또는 공개 게시를 승인하지는 않는다.

## 작업에 필요한 문서

- 구성 요소·데이터 흐름 변경: [구조](docs/ARCHITECTURE.md).
- 새 설치, 모델 준비, 시작 실패, 백업·재부팅 확인: [운영](docs/OPERATIONS.md).
- 고정 HTTPS 주소, WireGuard, Nginx 릴레이: [원격 접속](docs/REMOTE_ACCESS.md).
- 로그인·업로드·파일 경계 검토: [보안 범위](SECURITY.md).

해당 작업에 필요한 문서만 읽는다. 실제 설정과 문서가 다르면 실행 중인 구성과 소스를 확인하고 차이를 기록한다.

## 구현 중 유지할 경계

`agent/`는 모든 계정이 공유하는 파일 작업 공간이며 `.runtime/sandbox-agent/`는 계정·대화·첨부 저장소다. Windows 파일 핸들 검사, 계정별 소유권 검사, 채팅 모드의 모델 도구 차단을 함께 유지한다. 파일 경계를 OS 컨테이너나 임의 코드 실행 환경으로 설명하지 않는다.

추론은 하나의 백엔드 프로세스와 FIFO 대기열로 직렬화한다. 취소 시 스트림 및 진행 중인 파일 작업의 정리가 끝나야 다음 요청을 시작할 수 있다. 재시작된 요청과 파일 도구 호출은 자동 재생하지 않는다.

시작 문제에서는 프로세스 생성, 실제 리스너, 모델 로드, 외부 HTTPS, 로그인 순서로 실패한 지점을 좁힌다. PowerShell의 stderr와 종료 코드를 구분하고, 숨김 Python 호출에 표준 입출력 핸들이 있는지 확인한다. 앱 패키지 안에서만 보이는 Python 경로는 로그인 시작 환경에서 사용할 수 없으므로 실제 경로를 확인한다.

## 검증과 보고

변경한 경계에 맞는 [기여 안내의 검사](CONTRIBUTING.md)를 실행한다. 운영 DB·비밀번호·대화 내용을 테스트에 사용하거나 보고서에 출력하지 않는다. 배포 파일은 예시 설정과 실행 코드만 포함한다.

검증 결과는 수행한 범위로 제한한다. HTTP 상태 확인, 모델이 메모리에 로드됐는지 확인, 실제 답변 생성, 두 사용자의 순차 처리, 재부팅 후 자동 복구는 서로 다른 검사다. 재부팅 결과를 기록할 때 이전·이후 부팅 시각과 수동 개입 여부를 남긴다. 수동 실행 뒤의 정상 접속을 자동 시작 통과로 바꾸지 않는다.
