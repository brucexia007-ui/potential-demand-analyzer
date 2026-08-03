'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { useAuth } from '@/components/providers/auth-provider';
import { authenticatedFetch } from '@/lib/auth';
import { Button } from '@/components/ui/button';

type NotifItem = {
  id: string;
  task_id: string;
  type: string;
  title: string;
  message: string;
  is_read: boolean;
  created_at: string;
};

const navLinks = [
  { href: '/', label: '新建任务' },
  { href: '/batches', label: '批量任务' },
  { href: '/capabilities', label: '能力中心' },
  { href: '/dashboard', label: '经营看板' },
  { href: '/history', label: '历史记录' },
];

const settingsLinks = [
  { href: '/settings/providers', label: 'LLM Providers' },
  { href: '/settings/search', label: 'Search' },
  { href: '/settings/models', label: 'Model Routes' },
  { href: '/settings/crawler', label: 'Crawler' },
  { href: '/settings/budget', label: 'Budget' },
  { href: '/settings/skills', label: 'Skills' },
  { href: '/settings/data-retention', label: 'Data Retention' },
  { href: '/settings/security', label: 'Security' },
  { href: '/settings/export', label: 'Import/Export' },
];

export function Header() {
  const router = useRouter();
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const [unreadCount, setUnreadCount] = useState(0);
  const [notifs, setNotifs] = useState<NotifItem[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [showSettingsMenu, setShowSettingsMenu] = useState(false);
  const [showMobileMenu, setShowMobileMenu] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const settingsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!user) return;
    const fetchNotifs = () => {
      authenticatedFetch('/api/notifications')
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (data) {
            setUnreadCount(data.unread_count || 0);
            setNotifs(data.notifications || []);
          }
        })
        .catch(() => {});
    };
    fetchNotifs();
    const interval = setInterval(fetchNotifs, 15000);
    return () => clearInterval(interval);
  }, [user]);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
      if (settingsRef.current && !settingsRef.current.contains(e.target as Node)) {
        setShowSettingsMenu(false);
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  useEffect(() => {
    setShowDropdown(false);
    setShowSettingsMenu(false);
    setShowMobileMenu(false);
  }, [pathname]);

  const handleMarkRead = (nid: string) => {
    authenticatedFetch(`/api/notifications/${nid}/read`, {
      method: 'POST',
    });
    setUnreadCount((c) => Math.max(0, c - 1));
  };

  const handleNotifClick = (n: NotifItem) => {
    handleMarkRead(n.id);
    setShowDropdown(false);
    router.push(`/tasks/${n.task_id}`);
  };

  const handleLogout = async () => {
    await logout();
    router.push('/login');
  };

  const isActive = (href: string) => {
    if (href === '/') return pathname === '/';
    if (href.startsWith('/settings')) return pathname.startsWith('/settings');
    return pathname.startsWith(href);
  };

  return (
    <>
      <header className="sticky top-0 z-50 border-b border-neutral-950/10 bg-[var(--paper)]/90 backdrop-blur-xl">
        <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="flex h-16 items-center justify-between gap-4">
            <Link href="/" className="flex min-w-0 items-center gap-3">
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-neutral-950 bg-neutral-950 text-sm font-semibold text-[var(--signal-lime)]">
                K
              </span>
              <span className="truncate text-base font-semibold text-neutral-950 sm:text-lg">
                潜在需求分析系统
              </span>
            </Link>

            <nav className="hidden items-center gap-1 rounded-full border border-neutral-950/10 bg-white/75 p-1 md:flex">
              {navLinks.map((link) => (
                <Link
                  key={link.href}
                  href={link.href}
                  className={`rounded-full px-4 py-2 text-sm font-medium transition-all ${
                    isActive(link.href)
                      ? 'bg-neutral-950 text-white'
                      : 'text-neutral-600 hover:bg-neutral-950/5 hover:text-neutral-950'
                  }`}
                >
                  {link.label}
                </Link>
              ))}

              {/* 设置下拉菜单 */}
              <div ref={settingsRef} className="relative">
                <button
                  type="button"
                  onClick={() => setShowSettingsMenu((v) => !v)}
                  className={`rounded-full px-4 py-2 text-sm font-medium transition-all ${
                    pathname.startsWith('/settings')
                      ? 'bg-neutral-950 text-white'
                      : 'text-neutral-600 hover:bg-neutral-950/5 hover:text-neutral-950'
                  }`}
                >
                  设置
                  <svg
                    className={`ml-1 inline-block h-3 w-3 transition-transform ${showSettingsMenu ? 'rotate-180' : ''}`}
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                </button>

                {showSettingsMenu && (
                  <div className="absolute left-0 z-50 mt-2 w-44 rounded-lg border border-neutral-950/10 bg-white shadow-[var(--shadow-soft)]">
                    <div className="border-b border-neutral-950/10 px-3 py-2">
                      <h4 className="text-xs font-semibold text-neutral-500 uppercase tracking-wider">Settings</h4>
                    </div>
                    {settingsLinks.map((item) => (
                      <Link
                        key={item.href}
                        href={item.href}
                        className={`block px-4 py-2.5 text-sm transition-colors ${
                          pathname === item.href
                            ? 'bg-neutral-950/5 text-neutral-950 font-medium'
                            : 'text-neutral-600 hover:bg-neutral-950/5 hover:text-neutral-950'
                        }`}
                      >
                        {item.label}
                      </Link>
                    ))}
                  </div>
                )}
              </div>
            </nav>

            <div className="flex items-center gap-2">
              {user && (
                <div ref={dropdownRef} className="relative">
                  <button
                    type="button"
                    onClick={() => setShowDropdown(!showDropdown)}
                    className="relative grid h-10 w-10 place-items-center rounded-full border border-neutral-950/10 bg-white/80 text-neutral-600 transition-all hover:border-neutral-950 hover:text-neutral-950"
                    aria-label="通知"
                  >
                    <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
                    </svg>
                    {unreadCount > 0 && (
                      <span className="absolute -right-1 -top-1 inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-red-600 px-1 text-[10px] font-bold text-white">
                        {unreadCount > 9 ? '9+' : unreadCount}
                      </span>
                    )}
                  </button>

                  {showDropdown && (
                    <div className="absolute right-0 z-50 mt-3 max-h-96 w-[min(20rem,calc(100vw-2rem))] overflow-y-auto rounded-lg border border-neutral-950/10 bg-white shadow-[var(--shadow-soft)]">
                      <div className="border-b border-neutral-950/10 px-4 py-3">
                        <h4 className="text-sm font-semibold text-neutral-950">通知</h4>
                      </div>
                      {notifs.length === 0 ? (
                        <p className="px-4 py-8 text-center text-sm text-neutral-500">暂无新通知</p>
                      ) : (
                        notifs.slice(0, 10).map((n) => (
                          <button
                            key={n.id}
                            type="button"
                            onClick={() => handleNotifClick(n)}
                            className="w-full border-b border-neutral-950/5 px-4 py-3 text-left transition-colors hover:bg-neutral-950/5"
                          >
                            <p className="truncate text-sm font-medium text-neutral-950">{n.title}</p>
                            <p className="mt-0.5 truncate text-xs text-neutral-500">{n.message}</p>
                          </button>
                        ))
                      )}
                    </div>
                  )}
                </div>
              )}

              {user && (
                <div className="hidden items-center gap-3 border-l border-neutral-950/10 pl-3 md:flex">
                  <span className="max-w-36 truncate text-sm text-neutral-600">{user.username}</span>
                  <Button variant="ghost" size="sm" onClick={handleLogout}>登出</Button>
                </div>
              )}

              <button
                type="button"
                className="flex h-10 w-10 flex-col items-center justify-center gap-[5px] rounded-full border border-neutral-950/10 bg-white/80 md:hidden"
                onClick={() => setShowMobileMenu((v) => !v)}
                aria-label="打开导航"
                aria-expanded={showMobileMenu}
              >
                <span className={`h-[2px] w-5 bg-neutral-950 transition-all duration-300 ${showMobileMenu ? 'translate-y-[7px] rotate-45' : ''}`} />
                <span className={`h-[2px] w-5 bg-neutral-950 transition-all duration-300 ${showMobileMenu ? 'opacity-0' : ''}`} />
                <span className={`h-[2px] w-5 bg-neutral-950 transition-all duration-300 ${showMobileMenu ? '-translate-y-[7px] -rotate-45' : ''}`} />
              </button>
            </div>
          </div>
        </div>
      </header>

      <div
        className={`fixed inset-0 z-40 bg-[var(--paper)]/95 px-8 pt-28 backdrop-blur-xl transition-all duration-300 md:hidden ${
          showMobileMenu ? 'pointer-events-auto opacity-100' : 'pointer-events-none opacity-0'
        }`}
      >
        <nav className="flex flex-col gap-5">
          {navLinks.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className={`text-3xl font-semibold ${
                isActive(link.href) ? 'text-neutral-950' : 'text-neutral-500'
              }`}
            >
              {link.label}
            </Link>
          ))}
          <div className="mt-2 border-t border-neutral-950/10 pt-4">
            <p className="mb-3 text-sm font-semibold text-neutral-500 uppercase tracking-wider">Settings</p>
            {settingsLinks.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={`block py-2 text-xl font-medium ${
                  pathname === item.href ? 'text-neutral-950' : 'text-neutral-500'
                }`}
              >
                {item.label}
              </Link>
            ))}
          </div>
          {user && (
            <div className="mt-6 border-t border-neutral-950/10 pt-6">
              <p className="mb-3 text-sm text-neutral-500">{user.username}</p>
              <Button variant="secondary" onClick={handleLogout}>登出</Button>
            </div>
          )}
        </nav>
      </div>
    </>
  );
}
