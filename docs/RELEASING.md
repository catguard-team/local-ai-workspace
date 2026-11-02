# GitHub 공유와 소스 릴리스

이 프로젝트는 소스만 공개하는 구성을 사용합니다. 계정, 대화, 첨부, 작업 파일, 모델, Python 바이너리, 개인 키를 올리지 않습니다.

## 올릴 파일 확인

`release-files.txt`는 공개 파일의 명시적 목록입니다. 새 소스나 문서를 추가한 뒤 이 목록에도 해당 상대 경로를 추가하세요. 런타임 데이터를 포함하는 자동 목록 생성은 사용하지 않습니다.

```powershell
.\.venv\Scripts\python.exe scripts/check_release.py
git status --short
git diff --cached --stat
```

기본 `.gitignore`가 민감한 경로를 제외합니다. 검사기는 공개 목록 외의 Git 추적 파일도 거부합니다. `git add -f`로 개인 파일을 강제로 포함하지 마세요. 원격 저장소에 이미 올린 비밀은 파일 삭제만으로 제거되지 않으므로 해당 자격 증명 교체와 별도 기록 정리가 필요합니다.

## 처음 올릴 때

GitHub에서 빈 저장소를 생성하고 실제 URL을 사용하세요. 아래 명령은 문서 예시이며 이 프로젝트가 자동으로 원격 저장소를 만들거나 업로드하지는 않습니다.

```powershell
git init -b main
git add -- .
git diff --cached --stat
git commit -m "Prepare Local AI Server source release"
git remote add origin https://github.com/YOUR_ACCOUNT/YOUR_REPOSITORY.git
git push -u origin main
```

커밋 작성자 이름과 이메일은 본인의 Git 설정을 사용합니다. 이메일 공개를 원하지 않으면 GitHub 계정에서 제공하는 비공개 noreply 주소를 설정하세요. 기존 `origin`이 있으면 새로 추가하지 말고 목적지를 확인합니다. 업로드 전에 `scripts/check-local.ps1`로 로컬 검증을 완료하세요. GitHub Actions는 비활성화 상태로 유지하며 workflow를 추가하지 않습니다.

## ZIP 공유

```powershell
.\.venv\Scripts\python.exe scripts/build_release.py
```

`dist/local-ai-server-source.zip`과 같은 이름의 `.sha256` 파일이 생성됩니다. ZIP에는 `.git` 기록이나 설치된 `.venv`, `bin`, `.runtime`, `agent`가 포함되지 않습니다. 받은 사람은 압축을 푼 후 README의 초기 설치부터 진행합니다.

변경 기록에는 실제 수행한 테스트와 남은 제한을 적습니다. 로컬 단위 테스트, 모델 추론, 외부 HTTPS, Windows 로그인 시작, 실제 재부팅 검증은 각각 별개의 확인입니다. 하나의 결과로 나머지를 통과했다고 표시하지 않습니다.
