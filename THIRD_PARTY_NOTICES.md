# 외부 구성 요소

이 저장소의 자체 코드에는 [MIT 라이선스](LICENSE)를 적용합니다. 외부 소프트웨어와 모델에는 각 제공자의 라이선스가 적용됩니다.

| 구성 요소 | 포함 여부 | 고지 |
| --- | --- | --- |
| Marked 18.1.2 | `vendor/marked.umd.js` 포함 | [원본 라이선스 전체](vendor/marked.LICENSE.txt), [버전·출처·무결성](vendor/marked-version.json)을 유지합니다. MIT 및 포함된 Markdown 관련 고지를 함께 보존합니다. |
| Python 및 Python 패키지 | 소스 공유본에 바이너리 미포함 | 설치 시 내려받거나 사용자의 Python에서 로컬 런타임을 준비합니다. 각 패키지의 라이선스와 배포 메타데이터를 따릅니다. |
| LM Studio | 미포함 | 별도로 설치합니다. 프로젝트 라이선스가 LM Studio의 배포 조건을 변경하지 않습니다. |
| GGUF 모델 및 비전 projector | 미포함 | 모델 카드와 배포 파일의 라이선스를 확인한 뒤 별도로 내려받습니다. |
| WireGuard, Nginx, Docker | 미포함 | 선택적인 외부 접속 구성에만 사용하며 각 프로젝트의 조건을 따릅니다. |

기본 모델 안내는 [HauhauCS 모델 카드](https://huggingface.co/HauhauCS/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF)를 참조합니다. 이 앱은 해당 모델 제작자나 LM Studio의 공식 제품이 아닙니다.

