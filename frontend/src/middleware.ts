import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

const protectedRoutes = ['/', '/history', '/tasks', '/settings', '/batches'];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  const hasAccessCookie = Boolean(request.cookies.get('kanyikan_access')?.value);
  const hasRefreshCookie = Boolean(request.cookies.get('kanyikan_refresh')?.value);
  const hasSessionCookie = hasAccessCookie || hasRefreshCookie;

  if (pathname.startsWith('/api/')) {
    return NextResponse.next();
  }

  // 只有 Access Cookie 能在中间件层证明会话可直接使用；仅有 Refresh
  // Cookie 时由登录页的 AuthProvider 验证并刷新，避免无效 Cookie 重定向循环。
  if (pathname === '/login' && hasAccessCookie) {
    return NextResponse.redirect(new URL('/', request.url));
  }

  const isProtectedRoute = protectedRoutes.some(
    (route) => pathname === route || (route !== '/' && pathname.startsWith(`${route}/`)),
  );
  if (isProtectedRoute && !hasSessionCookie) {
    const loginUrl = new URL('/login', request.url);
    loginUrl.searchParams.set('redirect', `${pathname}${request.nextUrl.search}`);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
