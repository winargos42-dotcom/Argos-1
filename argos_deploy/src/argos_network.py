"""Bounded, authenticated ARGOS presence exchanges; no remote command execution."""
import hmac
import math
import os
import re
import threading
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, StrictBool, validator

NETWORK_ID = 'argos-recovery'
PRIMARY_ID = 'argos-x230-primary'
CLOUD_ID = 'railway-argos-full'
CHALLENGE_TTL = 45
OFFLINE_TTL = 60
MAX_UPTIME = 3155760000  # 100 years; finite, bounded telemetry only.
_BEARER = re.compile(r'Bearer +([A-Za-z0-9._~+/-]+=*)', re.IGNORECASE)


class Health(BaseModel):
    ready: StrictBool
    uptime_seconds: float = Field(ge=0, le=MAX_UPTIME)

    @validator('uptime_seconds', pre=True)
    def numeric_uptime(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('Uptime must be a finite number')
        return value

    class Config:
        extra = 'forbid'


class Registration(BaseModel):
    node_id: str = Field(min_length=1, max_length=64)
    boot_id: uuid.UUID
    status: Health

    class Config:
        extra = 'forbid'


class Heartbeat(Registration):
    challenge_id: uuid.UUID


def network_auth(request: Request):
    """Defense in depth: installing these routes alone must remain fail closed."""
    key = os.environ.get('ARGOS_NETWORK_KEY', '')
    if not key.strip():
        raise HTTPException(503, 'Network authentication is not configured',
                            headers={'Cache-Control': 'no-store'})
    values = request.headers.getlist('authorization')
    match = _BEARER.fullmatch(values[0]) if len(values) == 1 else None
    if not match or not hmac.compare_digest(match.group(1).encode(), key.encode()):
        raise HTTPException(401, 'Network authentication required',
                            headers={'WWW-Authenticate': 'Bearer', 'Cache-Control': 'no-store'})


def install_network_routes(app, health_provider):
    """Install one in-memory primary session and a local secondary health view.

    Requires a single server worker (the deployed entrypoint uses one). A process
    restart forgets sessions; clients recover through registration after HTTP409.
    All challenges are UUID4, consumed atomically, with monotonic expiry.
    """
    router = APIRouter(prefix='/network', dependencies=[Depends(network_auth)])
    lock = threading.Lock()
    session = None

    def cloud():
        # Never reflect callback errors, environment, or arbitrary extra fields.
        try:
            supplied = health_provider()
            ready = supplied.get('ready') is True
            uptime = supplied.get('uptime_seconds', 0)
            if isinstance(uptime, bool) or not isinstance(uptime, (int, float)) or not math.isfinite(uptime) or not 0 <= uptime <= MAX_UPTIME:
                uptime = 0
        except Exception:
            ready, uptime = False, 0
        return {'node_id': CLOUD_ID, 'role': 'secondary', 'online': True,
                'ready': ready, 'confirmed': True, 'last_seen_age_seconds': 0,
                'uptime_seconds': uptime}

    def identity(body):
        if body.node_id != PRIMARY_ID:
            raise HTTPException(403, 'Only the physical primary may register or heartbeat')

    def issue(current, now):
        current['challenge'] = uuid.uuid4()
        current['expires'] = now + CHALLENGE_TTL
        return {'node_id': PRIMARY_ID, 'primary_id': PRIMARY_ID,
                'confirmed': current['last_confirmed'] is not None,
                'challenge_id': str(current['challenge']),
                'challenge_expires_in': CHALLENGE_TTL, 'cloud': cloud()}

    def primary(now):
        confirmed = session is not None and session['last_confirmed'] is not None
        age = max(0, now - session['last_confirmed']) if confirmed else None
        online = confirmed and age < OFFLINE_TTL
        return {'node_id': PRIMARY_ID, 'role': 'primary', 'online': bool(online),
                'ready': bool(online and session['health']['ready']),
                'confirmed': bool(confirmed),
                'last_seen_age_seconds': round(age, 3) if age is not None else None,
                'uptime_seconds': session['health']['uptime_seconds'] if confirmed else 0}

    @router.get('/status')
    def status():
        with lock:
            peer = primary(time.monotonic())
        secondary = cloud()
        return {'network_id': NETWORK_ID, 'primary_id': PRIMARY_ID,
                'cloud': secondary, 'peers': [peer, secondary]}

    @router.post('/register')
    def register(body: Registration):
        nonlocal session
        identity(body)
        with lock:
            session = {'boot': body.boot_id, 'health': body.status.dict(),
                       'last_confirmed': None}
            return issue(session, time.monotonic())

    @router.post('/heartbeat')
    def heartbeat(body: Heartbeat):
        identity(body)
        with lock:
            now = time.monotonic()
            if (session is None or body.boot_id != session['boot']
                    or body.challenge_id != session['challenge'] or now >= session['expires']):
                raise HTTPException(409, 'Unknown, expired, or consumed challenge; register again')
            session['last_confirmed'] = now
            session['health'] = body.status.dict()
            return issue(session, now)

    app.include_router(router)
