"""Single public origin for the unified local chat and bounded agent."""
import asyncio
import hmac
import re
from pathlib import Path
import time
from urllib.parse import urlsplit
from aiohttp import ClientSession, ClientTimeout, web
from vpn_access import (INTERNAL_PREFIX, TOKEN_HEADER, PEER_HEADER, RELAY_HEADER, RELAY_PREFIX,
                        PUBLIC_PREFIX, PUBLIC_TOKEN_HEADER, PUBLIC_CLIENT_HEADER,
                        load_vpn_config, load_vpn_public_config, permitted_peer, vpn_authority,
                        canonical_client_ip, public_client_token)
from local_urls import vpn_only_access

HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade', 'content-length'}
UNTRUSTED_PROXY = {'cf-connecting-ip', 'x-forwarded-for', 'x-real-ip', 'forwarded'}
DRAIN_MARKER = Path(__file__).resolve().parent / '.runtime' / 'drain-requests'


def draining_requests():
    """Temporarily stop new model work while existing answers drain for deploy."""
    try:
        return 0 <= time.time() - DRAIN_MARKER.stat().st_mtime < 1800
    except FileNotFoundError:
        return False


def proxy_headers(headers, local_ip, peer_ip, config, vpn_only=False, public_config=None):
    """The bound socket, not user-supplied headers, selects the VPN boundary."""
    result = {k: v for k, v in headers.items()
              if k.lower() not in HOP | UNTRUSTED_PROXY and not k.lower().startswith((INTERNAL_PREFIX, RELAY_PREFIX, PUBLIC_PREFIX))}
    values = {k.lower(): v for k, v in headers.items()}
    host = values.get('host', '')
    if local_ip == '127.0.0.1':
        if vpn_only and host not in ('127.0.0.1:8787', 'localhost:8787'):
            raise PermissionError('Public origins are disabled in VPN-only mode')
        return result
    if (not config or not config['enabled'] or local_ip != config['bind_ip']
            or not permitted_peer(peer_ip, config)):
        raise PermissionError('VPN listener accepts only the configured host and peer subnet')
    origin = values.get('origin')
    if public_config and host == urlsplit(public_config['public_url']).netloc:
        token = values.get(RELAY_HEADER, '')
        if (peer_ip != public_config['relay_peer'] or not re.fullmatch(r'[0-9a-f]{64}', token)
                or not hmac.compare_digest(token, public_config['relay_token'])
                or origin not in (None, public_config['public_url'])):
            raise PermissionError('Public requests require the configured authenticated VPN relay')
        # The browser origin is HTTPS: never grant the direct-VPN cookie exception.
        # Nginx overwrites X-Real-IP; only this authenticated relay may assert it.
        client_ip = canonical_client_ip(values.get('x-real-ip'))
        result[PUBLIC_CLIENT_HEADER] = client_ip
        result[PUBLIC_TOKEN_HEADER] = public_client_token(client_ip, config, public_config)
        return result
    if host != vpn_authority(config):
        raise PermissionError('VPN listener requires its exact configured host')
    if origin is not None and origin != 'http://' + vpn_authority(config):
        raise PermissionError('VPN origin does not match its bound listener')
    result[TOKEN_HEADER] = config['shared_token']
    result[PEER_HEADER] = peer_ip
    return result


async def session_context(app):
    app['vpn_config'] = load_vpn_config()
    app['vpn_public_config'] = load_vpn_public_config(vpn_config=app['vpn_config'])
    app['client'] = ClientSession(timeout=ClientTimeout(total=None, sock_connect=10, sock_read=900), auto_decompress=False)
    yield
    await app['client'].close()


async def handle(request):
    if (request.method == 'POST' and re.fullmatch(r'/api/chats/[^/]+/messages', request.path)
            and draining_requests()):
        return web.json_response({'detail': '업데이트 적용 중입니다. 현재 답변이 끝나면 다시 보낼 수 있습니다. 입력한 내용은 유지됩니다.'},
                                 status=503, headers={'Retry-After': '10', 'Cache-Control': 'no-store'})
    socket = request.transport.get_extra_info('sockname') if request.transport else None
    local_ip = socket[0] if socket else None
    try:
        headers = proxy_headers(request.headers, local_ip, request.remote, request.app['vpn_config'],
                                vpn_only_access() is not None, request.app['vpn_public_config'])
    except (PermissionError, ValueError):
        return web.Response(status=403, text='This listener accepts only authorized VPN peers and its fixed address.')
    if request.method == 'GET' and request.path == '/computer':
        raise web.HTTPFound('/?mode=agent')
    if request.headers.get('Upgrade'):
        return web.Response(status=403, text='This service does not expose execution or terminal sockets.')
    payload = request.content.iter_chunked(65536) if request.can_read_body else None
    try:
        async with request.app['client'].request(request.method, 'http://127.0.0.1:8081' + request.rel_url.raw_path_qs, headers=headers, data=payload, allow_redirects=False) as upstream:
            response = web.StreamResponse(status=upstream.status)
            for key, value in upstream.headers.items():
                if key.lower() not in HOP:
                    response.headers.add(key, value)
            await response.prepare(request)
            async for chunk in upstream.content.iter_chunked(65536):
                await response.write(chunk)
            await response.write_eof()
            return response
    except (ConnectionResetError, asyncio.CancelledError):
        raise
    except Exception:
        return web.Response(status=503, text='Local AI 연결을 준비하고 있습니다. 잠시 후 새로고침하세요.')


app = web.Application(client_max_size=11 * 1024 * 1024)
app.cleanup_ctx.append(session_context)
app.router.add_route('*', '/{path:.*}', handle)


async def bind_vpn_when_ready(runner, config, retry_interval=3):
    """A delayed VPN interface must not prevent the loopback site from starting."""
    if not config or not config['enabled']:
        return None
    while True:
        site = web.TCPSite(runner, host=config['bind_ip'], port=8787)
        try:
            await site.start()
            return site
        except OSError:
            await site.stop()
            await asyncio.sleep(retry_interval)


async def run_server():
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    vpn_task = None
    try:
        await web.TCPSite(runner, host='127.0.0.1', port=8787).start()
        vpn_task = asyncio.create_task(bind_vpn_when_ready(runner, app['vpn_config']))
        await asyncio.Event().wait()
    finally:
        if vpn_task:
            vpn_task.cancel()
            await asyncio.gather(vpn_task, return_exceptions=True)
        await runner.cleanup()


if __name__ == '__main__':
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        pass
