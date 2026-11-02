from pathlib import Path
from local_urls import public_url, fixed_access, vpn_only_access


def access_text(root, chat, *, vpn_only=False, fixed=False, public_gateway=False):
    if vpn_only and public_gateway:
        address_note = ('고정 HTTPS 주소입니다. 외부 PC와 휴대폰에서 브라우저로 접속하세요.\n'
                        '접속 기기에는 VPN을 설치할 필요가 없습니다.\n'
                        '서버 PC와 AWS의 연결은 WireGuard 암호화 터널을 사용합니다.\n'
                        'Windows 로그인 후 앱이 시작되며, 서버의 VPN 연결이 준비되면 접속할 수 있습니다.\n')
    elif vpn_only:
        address_note = ('VPN 전용 고정 주소입니다. 접속 기기에서 WireGuard를 먼저 연결하세요.\n'
                        'HTTP 접속은 WireGuard 암호화 터널 안에서 사용합니다.\n'
                        'Windows 로그인 후 앱이 시작되며, VPN 연결이 준비되면 접속할 수 있습니다.\n')
    elif fixed:
        address_note = '고정 HTTPS 주소입니다. Windows 로그인 후 서버와 터널이 자동으로 시작됩니다.\n'
    else:
        address_note = '기본 접속은 이 PC의 로컬 브라우저에서만 가능합니다. 외부 접속은 별도 설정이 필요합니다.\n'
    return ('Local AI — LM Studio + Qwen3.8 27B Uncensored\n\n'
            '통합 채팅 (기본): ' + chat + '\n에이전트 바로가기: ' + chat + '/?mode=agent\n\n'
            '같은 화면에서 채팅 ↔ 에이전트 전환 · 모바일 지원\n'
            'Enter 전송 / Shift+Enter 줄바꿈\n'
            '계정: setup_accounts.py로 생성한 사용자 이름\n'
            '비밀번호: 각각 지정하신 비밀번호\n\n'
            '에이전트 파일 작업 범위: ' + str(root / 'agent') + '\n'
            '폴더 내 파일 작업만 허용하며 터미널·코드 실행은 차단됩니다.\n'
            '대화 기록과 첨부는 계정별 비공개이며 파일 작업 폴더는 공유합니다.\n\n'
            '서버 PC가 켜져 있어야 합니다.\n' + address_note)


def main():
    root = Path(__file__).resolve().parent
    vpn = vpn_only_access()
    chat = public_url() or 'http://127.0.0.1:8787'
    fixed = vpn is None and fixed_access() is not None
    public_gateway = vpn is not None and vpn["public_gateway"]
    (root / '접속주소.txt').write_text(access_text(root, chat, vpn_only=vpn is not None, fixed=fixed, public_gateway=public_gateway), encoding='utf-8')
    print('Chat:', chat)
    print('Agent:', chat + '/?mode=agent')
    if public_gateway:
        print('Access: Public HTTPS; browser only, no client VPN required')
    elif vpn is not None:
        print('Access: WireGuard VPN connection required')


if __name__ == '__main__':
    main()
