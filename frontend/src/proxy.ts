import { NextResponse, type NextRequest } from 'next/server';

// Every /api request goes to FastAPI through here, and the admin panel exists only at the secret /<ADMIN_PATH>.
// All settings are server-only (no NEXT_PUBLIC_*), so neither the secret path nor any key reaches the browser.
const BACKEND = process.env.API_BACKEND_URL || 'http://127.0.0.1:8000';
const ADMIN_PATH = process.env.ADMIN_PATH || '';
const FRONTEND_SECRET = process.env.FRONTEND_PROXY_SECRET || '';
const ALLOWED_IPS = (process.env.ADMIN_ALLOWED_IPS || '127.0.0.1,::1').split(',').map(ip => ip.trim()).filter(Boolean);
// Railway's edge overwrites X-Real-IP with the connecting address; only trust it when deployed behind that edge.
const TRUST_X_REAL_IP = process.env.TRUST_X_REAL_IP === 'true';
// Headers the backend trusts only from us; a browser-supplied copy is always discarded.
const INTERNAL_HEADERS = ['x-frontend-secret', 'x-client-ip'];

function clientIp(request: NextRequest): string | null {
  if (TRUST_X_REAL_IP) return request.headers.get('x-real-ip')?.trim().replace(/^::ffff:/, '') || null;
  return process.env.NODE_ENV === 'production' ? null : '127.0.0.1'; // local `next dev` only
}

function notFound(request: NextRequest) {
  return NextResponse.rewrite(new URL('/__not_found__', request.url)); // Next's ordinary 404 page
}

function toBackend(path: string, request: NextRequest, ip: string | null) {
  const headers = new Headers(request.headers);
  INTERNAL_HEADERS.forEach(h => headers.delete(h));
  if (FRONTEND_SECRET && ip) { headers.set('x-frontend-secret', FRONTEND_SECRET); headers.set('x-client-ip', ip); }
  return NextResponse.rewrite(new URL(path, BACKEND), { request: { headers } });
}

function log(event: string, request: NextRequest, ip: string | null) {
  console.warn(JSON.stringify({ event, ip: ip ?? 'unknown', method: request.method, path: request.nextUrl.pathname,
    user_agent: (request.headers.get('user-agent') || '').slice(0, 200), at: new Date().toISOString() }));
}

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const ip = clientIp(request);
  const adminAllowed = !!ADMIN_PATH && !!ip && ALLOWED_IPS.includes(ip);
  const adminApi = !!ADMIN_PATH && (pathname === `/api/v1/${ADMIN_PATH}` || pathname.startsWith(`/api/v1/${ADMIN_PATH}/`));

  // The old, guessable locations do not exist.
  if (/^\/(admin|adminpanel|sistem)(\/|$)/i.test(pathname) || /^\/api\/v1\/admin(\/|$)/i.test(pathname)) {
    log('admin_probe', request, ip);
    return notFound(request);
  }
  if (ADMIN_PATH && pathname === `/${ADMIN_PATH}`) {
    if (!adminAllowed) { log('admin_blocked_ip', request, ip); return notFound(request); }
    log('admin_page', request, ip);
    return toBackend(`/api/v1/${ADMIN_PATH}/panel`, request, ip); // the backend serves the page itself
  }
  if (!pathname.startsWith('/api/')) return NextResponse.next();
  if (adminApi && !adminAllowed) { log('admin_blocked_ip', request, ip); return notFound(request); }

  return toBackend(pathname + search, request, ip);
}

export const config = { matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'] };
