# 의존성 보안 수정 기록

## 2026-11-03 — pypdf 6.21.2 → 6.21.4

GitHub Dependabot의 열린 높음 등급 알림 14건을 전부 확인했습니다. 서로 다른 취약점은 7개이며, `requirements.txt`와 `requirements-lock.txt`에 각각 등록돼 14건으로 표시되었습니다. 두 파일을 함께 `pypdf==6.21.4`로 수정합니다. 알림을 무시하거나 검사에서 제외하는 방식은 사용하지 않습니다.

| 저장소 알림 | 공식 권고문 | 영향 | 최초 수정 버전 |
| --- | --- | --- | --- |
| #4, #11 | [GHSA-42gh-23hr-vphw](https://github.com/py-pdf/pypdf/security/advisories/GHSA-42gh-23hr-vphw) | pypdf has possible long runtimes when retrieving page labels | 6.21.3 |
| #3, #10 | [GHSA-4vqv-7265-gm2w](https://github.com/py-pdf/pypdf/security/advisories/GHSA-4vqv-7265-gm2w) | pypdf has possible large memory usage for exceptions in strict mode | 6.21.3 |
| #7, #14 | [GHSA-5849-834g-hvw5](https://github.com/py-pdf/pypdf/security/advisories/GHSA-5849-834g-hvw5) | pypdf has possible infinite loop when hitting EOF in standard xref table during trailer search | 6.21.4 |
| #2, #9 | [GHSA-62m5-6wh8-28x7](https://github.com/py-pdf/pypdf/security/advisories/GHSA-62m5-6wh8-28x7) | pypdf has possible long runtimes/large memory usage for encrypted PDF files | 6.21.3 |
| #6, #13 | [GHSA-cggf-277g-3q54](https://github.com/py-pdf/pypdf/security/advisories/GHSA-cggf-277g-3q54) | pypdf has possible large memory usage when parsing page tree | 6.21.4 |
| #5, #12 | [GHSA-mfr5-77xh-9j7h](https://github.com/py-pdf/pypdf/security/advisories/GHSA-mfr5-77xh-9j7h) | pypdf has possible infinite loop when cloning dictionaries | 6.21.4 |
| #1, #8 | [GHSA-x3rh-gjgm-fmqc](https://github.com/py-pdf/pypdf/security/advisories/GHSA-x3rh-gjgm-fmqc) | pypdf has possible large memory usage when parsing cross-reference streams | 6.21.3 |

공식 [6.21.4 릴리스](https://github.com/py-pdf/pypdf/releases/tag/6.21.4)는 위 6.21.3 수정 사항을 포함합니다. 앱은 인증된 사용자가 올린 PDF를 `PdfReader`로 열고 페이지 수와 텍스트를 읽으므로 파싱 경로를 실제로 사용합니다. 페이지 라벨 조회나 사전 복제처럼 현재 앱이 직접 호출하지 않는 경로의 알림도 의존성 전체를 업데이트해 해결합니다.

### 검증 범위

2026-11-03 로컬 검증 결과: Windows 테스트 87개와 릴레이 테스트 13개(총 100개), 화면 검사 12개 통과. 설치된 6.21.4가 14개 알림의 취약 버전 범위 밖에 있음을 별도로 비교했습니다.

- 빈 Python 3.12 가상환경에 수정된 두 의존성 파일로 새 설치 후 `pip check`.
- 전체 Windows 앱·계정·파일 경계·대기열 테스트와 독립 릴레이·화면 로직 검사.
- 정상 PDF 업로드의 텍스트 추출 및 원본 다운로드, 암호화 PDF 거부와 저장 방지.
- 잘린 xref/trailer EOF 회귀 입력이 파싱 오류로 종료되는지 별도 프로세스에서 확인. 10초 제한을 두어 회귀 시 검사가 무한 대기하지 않게 했습니다. 입력은 upstream PR #4289의 작은 회귀 사례를 사용합니다.

이 검사는 모든 악성 PDF 입력에 대한 자원 제한이나 보안 증명을 의미하지 않습니다. 7개 취약점 전체의 수정 여부는 공식 수정 버전과 GitHub의 알림 상태를 함께 확인합니다.

### 기존 설치에 적용

새 clone은 수정 버전을 설치합니다. 기존 설치는 서버를 중지한 뒤 최신 코드를 받고 아래 명령으로 설치 환경도 갱신해야 합니다. GitHub 소스 변경만으로 실행 중인 다른 PC의 라이브러리가 교체되지는 않습니다.

```powershell
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -c requirements-lock.txt
.\.venv\Scripts\python.exe -m pip check
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

기존 백엔드 프로세스를 다시 시작해야 새 라이브러리가 적용됩니다. 원래 운영 설치처럼 별도 소스·가상환경을 유지하는 환경은 그 설치 경로에 별도로 적용하세요.
