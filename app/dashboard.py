"""Private, same-origin web dashboard backed by the existing SQLite store."""
from datetime import date
import ipaddress
from hmac import compare_digest, new as hmac_new
from hashlib import sha256
import secrets
import time
from threading import Lock
from pathlib import Path
import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.config import Settings
from app.models import DataError, symbol_code
from app.services import BotService
from app.storage import Store
from app.symbols import SymbolResolver

COOKIE = "stock_briefing_session"
SESSION_SECONDS = 12 * 60 * 60
_login_attempts = {}
_login_lock = Lock()
ASSETS = Path(__file__).with_name('web')
HEADERS = {
    'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
    'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
    'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
}


def _transport_allowed(request: Request) -> bool:
    if request.url.scheme == 'https' or request.url.hostname in {'localhost', '127.0.0.1', '::1'}:
        return True
    if os.getenv('DASHBOARD_ALLOW_HTTP_PRIVATE') != '1':
        return False
    try:
        address = ipaddress.ip_address(request.url.hostname or '')
    except ValueError:
        return False
    return address.is_private and not address.is_loopback


def configured(request: Request):
    if not (os.getenv('DASHBOARD_SESSION_SECRET') or os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('MASTER_KEY')):
        raise HTTPException(404)
    if not _transport_allowed(request):
        raise HTTPException(403, 'HTTPS 연결이 필요합니다.')


def same_origin(request: Request):
    if request.headers.get('x-dashboard-request') != '1' or request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, '웹 대시보드에서 다시 요청하세요.')
    origin = request.headers.get('origin')
    if origin and origin != str(request.base_url).rstrip('/'):
        raise HTTPException(403, '웹 대시보드에서 다시 요청하세요.')


def signature(value: str) -> str:
    key_source = (os.getenv('DASHBOARD_SESSION_SECRET') or
                  os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('MASTER_KEY', ''))
    key = sha256(("stock-briefing-session\\0" + key_source).encode()).digest()
    return hmac_new(key, value.encode(), sha256).hexdigest()


def session_user_id(request: Request) -> str | None:
    token = request.cookies.get(COOKIE, '')
    if len(token) > 512:
        return None
    try:
        expiry, user_id, nonce, digest = token.split('.')
        remaining = int(expiry) - time.time()
        signed = expiry + '.' + user_id + '.' + nonce
        if not (0 < remaining <= SESSION_SECONDS and len(nonce) == 32 and user_id.isascii() and user_id.isdigit()
                and compare_digest(digest, signature(signed))):
            return None
        store = Store(Settings.from_env().db_path)
        store.init()
        with store.connect() as db:
            exists = db.execute("SELECT 1 FROM dashboard_accounts WHERE user_id=?", (user_id,)).fetchone()
        return user_id if exists else None
    except (ValueError, OverflowError):
        return None


def signed_in(request: Request) -> bool:
    return session_user_id(request) is not None


def authorize(request: Request, response: Response):
    configured(request)
    if not signed_in(request):
        raise HTTPException(401, '로그인이 필요합니다.', headers=HEADERS)
    response.headers.update(HEADERS)
    request.state.dashboard_user_id = session_user_id(request)
    if request.method not in {'GET', 'HEAD'}:
        same_origin(request)
    store = Store(Settings.from_env().db_path)
    store.init()
    return store


router = APIRouter(prefix='/stock-briefing', dependencies=[Depends(configured)])


@router.get('', include_in_schema=False)
@router.get('/', include_in_schema=False)
def page(request: Request):
    if not signed_in(request):
        return RedirectResponse('/stock-briefing/login', status_code=303, headers=HEADERS)
    return FileResponse(ASSETS / 'index.html', headers=HEADERS)


@router.get('/login', include_in_schema=False)
def login_page(request: Request):
    if signed_in(request):
        return RedirectResponse('/stock-briefing/', status_code=303, headers=HEADERS)
    return FileResponse(ASSETS / 'login.html', headers=HEADERS)


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


@router.post('/api/login', dependencies=[Depends(same_origin)])
def login(payload: LoginInput, request: Request):
    now = time.monotonic()
    peer = request.client.host if request.client else 'unknown'
    with _login_lock:
        for key in list(_login_attempts):
            if now - _login_attempts[key][0] >= 300:
                del _login_attempts[key]
        started, attempts = _login_attempts.get(peer, (now, 0))
        if attempts >= 10 or (peer not in _login_attempts and len(_login_attempts) >= 4096):
            raise HTTPException(429, '잠시 후 다시 로그인하세요.', headers={'Retry-After': '300'})
        _login_attempts[peer] = (started, attempts + 1)
    username = payload.username.strip().lower()
    store = Store(Settings.from_env().db_path)
    store.init()
    with store.connect() as db:
        row = db.execute(
            "SELECT user_id,password_hash,password_salt FROM dashboard_accounts WHERE username=?",
            (username,),
        ).fetchone()
    valid = False
    user_id = None
    if row:
        try:
            candidate = __import__('hashlib').pbkdf2_hmac(
                'sha256', payload.password.encode(), bytes.fromhex(row[2]), 600_000
            ).hex()
            valid = compare_digest(candidate, row[1])
            user_id = str(row[0]) if valid else None
        except (ValueError, TypeError):
            valid = False
    if not valid:
        raise HTTPException(401, '아이디 또는 비밀번호를 확인하세요.', headers=HEADERS)
    with _login_lock:
        _login_attempts.pop(peer, None)
    expiry = str(int(time.time()) + SESSION_SECONDS)
    nonce = secrets.token_hex(16)
    value = expiry + '.' + user_id + '.' + nonce
    response = Response(status_code=204, headers=HEADERS)
    response.set_cookie(COOKIE, value + '.' + signature(value), max_age=SESSION_SECONDS,
                        httponly=True, secure=request.url.scheme == 'https',
                        samesite='strict', path='/stock-briefing')
    return response

@router.post('/api/logout', dependencies=[Depends(same_origin)])
def logout():
    response = Response(status_code=204, headers=HEADERS)
    response.delete_cookie(COOKIE, path='/stock-briefing', httponly=True, samesite='strict')
    return response


@router.get('/assets/{filename}', include_in_schema=False)
def asset(filename: str):
    if filename not in {'dashboard.css', 'dashboard.js', 'login.js'}:
        raise HTTPException(404)
    return FileResponse(ASSETS / filename, headers=HEADERS)


def _dashboard_user_id(request: Request):
    user_id = getattr(request.state, 'dashboard_user_id', None)
    if not user_id:
        raise HTTPException(401, '로그인이 필요합니다.')
    return user_id


@router.get('/api/overview')
def overview(request: Request, store: Store = Depends(authorize)):
    user_id = _dashboard_user_id(request)
    latest = store.briefing(user_id=user_id)
    proposals = dict(store.items_for_user('watch_proposals', user_id))
    watchlist = []
    for symbol, info in sorted(store.items_for_user('watchlist', user_id)):
        record = proposals.get(symbol, {})
        values = record.get('indicators', {})
        watchlist.append({'symbol': symbol, 'name': info.get('name', symbol),
                          'action': record.get('action', '미분석'), 'day': record.get('day'),
                          'at': record.get('at'), 'reason': record.get('reason'),
                          'close': values.get('close'), 'daily_return': values.get('daily_return')})
    markets = [r['indicators']['market'] for r in proposals.values()
               if r.get('indicators', {}).get('market')]
    market = max(markets, key=lambda m: m.get('day', '')) if markets else None
    # Explicit field selection: credentials, account identifiers and raw logs never enter the response.
    market = {k: market.get(k) for k in ('day', 'close', 'phase', 'ma20', 'ma60', 'k', 'd')} if market else None
    job = store.latest_job()
    return {'latest': {'day': latest[0], 'body': latest[1]} if latest else None,
            'watchlist': watchlist, 'market': market,
            'job': {'id': job[0], 'status': job[1]} if job else None}


@router.get('/api/history')
def history(request: Request, before: date | None = None, store: Store = Depends(authorize)):
    rows = store.briefing_history(_dashboard_user_id(request), before, 31)
    return {'items': [{'day': r[0], 'created_at': r[1]} for r in rows[:30]],
            'next': rows[29][0] if len(rows) > 30 else None}


@router.get('/api/history/{day}')
def historical(day: date, request: Request, store: Store = Depends(authorize)):
    result = store.briefing(day, _dashboard_user_id(request))
    if not result:
        raise HTTPException(404, '해당 날짜의 브리핑이 없습니다.')
    return {'day': result[0], 'body': result[1]}


@router.post('/api/refresh', status_code=202)
def refresh(store: Store = Depends(authorize)):
    job = store.enqueue_refresh()
    store.audit('web_briefing_refresh', str(job))
    return {'id': job, 'message': '브리핑 갱신을 요청했습니다.'}


class WatchInput(BaseModel):
    query: str = Field(min_length=1, max_length=40)


@router.post('/api/watchlist')
def add_watch(payload: WatchInput, request: Request, store: Store = Depends(authorize)):
    settings = Settings.from_env()
    user_id = _dashboard_user_id(request)
    query = payload.query.strip()
    if not query:
        raise HTTPException(400, '종목명 또는 종목코드를 입력하세요.')
    try:
        if len(query) == 6 and query.isascii() and query.isalnum():
            symbol = symbol_code(query.upper())
            known = store.get_for_user('watchlist', symbol, user_id) or store.get('symbol_names', symbol) or {}
            name = known.get('name', symbol)
        else:
            matches = SymbolResolver(settings, store).resolve(query)
            if len(matches) != 1:
                raise DataError('종목을 하나로 확인할 수 없습니다. 6자리 종목코드로 입력하세요.')
            symbol, name = matches[0], query
        BotService(settings, store)._watchlist_change(symbol, {'name': name}, user_id)
        store.audit('web_watchlist_add', symbol)
    except DataError as exc:
        raise HTTPException(400, str(exc)) from None
    return {'symbol': symbol, 'name': name}


@router.delete('/api/watchlist/{symbol}')
def remove_watch(symbol: str, request: Request, store: Store = Depends(authorize)):
    try:
        symbol = symbol_code(symbol)
        BotService(Settings.from_env(), store)._watchlist_change(symbol, None, _dashboard_user_id(request))
        store.audit('web_watchlist_remove', symbol)
    except DataError as exc:
        raise HTTPException(409, str(exc)) from None
    return {'message': '관심종목에서 제거했습니다. 과거 기록은 보관합니다.'}
