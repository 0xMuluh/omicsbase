"""Enforce OpenHands workspace session expiry and revocation for live sockets."""
import asyncio
from http.cookies import SimpleCookie

from omicsbase_shared.identity import COOKIE, verify_ticket


async def _monitor_session(sio, connection_id: str, token: str, interval: int = 15):
    try:
        while sio.manager.is_connected(connection_id, '/'):
            await asyncio.sleep(interval)
            try:
                claims = verify_ticket(token)
                if claims.get('purpose') != 'session':
                    raise ValueError('Session credential required')
            except (ValueError, KeyError, TypeError):
                await sio.disconnect(connection_id)
                return
    except asyncio.CancelledError:
        return


def install_socket_sessions(sio):
    """Wrap the stock connect handler without changing OpenHands event flow."""
    original = sio.handlers['/']['connect']
    tasks: set[asyncio.Task] = set()

    async def connect(connection_id: str, environ: dict, auth=None):
        cookies = SimpleCookie()
        cookies.load(environ.get('HTTP_COOKIE', ''))
        token = cookies[COOKIE].value if COOKIE in cookies else ''
        if not token:
            header = environ.get('HTTP_AUTHORIZATION', '')
            token = header[7:] if header.startswith('Bearer ') else ''
        try:
            claims = verify_ticket(token)
            if claims.get('purpose') != 'session':
                raise ValueError('Session credential required')
        except (ValueError, KeyError, TypeError):
            return False

        result = await original(connection_id, environ)
        if result is not False:
            task = asyncio.create_task(_monitor_session(sio, connection_id, token))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        return result

    sio.on('connect', handler=connect)
