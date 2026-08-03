'use client';

import { Suspense, useEffect, useState, FormEvent } from 'react';
import { useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { useAuth } from '@/components/providers/auth-provider';
import { useConfig } from '@/components/providers/config-provider';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card } from '@/components/ui/card';
import { useToast } from '@/components/ui/toast';
import { login } from '@/lib/auth';

function LoginForm() {
  const searchParams = useSearchParams();
  const { user, isLoading: isAuthLoading, refreshUser } = useAuth();
  const { refresh: refreshConfig } = useConfig();
  const requestedRedirect = searchParams.get('redirect') || '/';
  const redirect = requestedRedirect.startsWith('/') &&
    !requestedRedirect.startsWith('//') &&
    !requestedRedirect.startsWith('/login')
    ? requestedRedirect
    : '/';

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const { error: toastError } = useToast();

  useEffect(() => {
    if (!isAuthLoading && user) {
      window.location.replace(redirect);
    }
  }, [isAuthLoading, redirect, user]);

  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setIsLoading(true);

    try {
      await login(username, password);
      await refreshUser();
      await refreshConfig();
      window.location.replace(redirect);
    } catch (err) {
      toastError(err instanceof Error ? err.message : '登录失败，请检查用户名和密码');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="relative flex min-h-[calc(100vh-4rem)] items-center justify-center overflow-hidden px-4 py-10">
      <div className="absolute inset-x-0 top-0 h-px bg-neutral-950/10" />
      <div className="grid w-full max-w-5xl gap-6 lg:grid-cols-[1fr_420px] lg:items-center">
        <section className="hidden lg:block">
          <p className="mb-4 text-sm font-medium text-neutral-500">ACCESS NODE</p>
          <h1 className="max-w-xl text-5xl font-semibold leading-tight text-neutral-950">
            潜在需求分析系统
          </h1>
          <p className="mt-5 max-w-lg text-base leading-7 text-neutral-600">
            基于智能体 Harness 架构的企业需求挖掘平台。登录后进入任务指挥台，创建分析、跟踪执行，并回溯证据链。
          </p>
          <div className="mt-8 grid max-w-lg grid-cols-3 gap-3">
            {['策略规划', '证据采集', '报告生成'].map((item) => (
              <div key={item} className="rounded-lg border border-neutral-950/10 bg-white/75 p-4">
                <div className="mb-3 h-1.5 w-8 rounded-full bg-[var(--signal-lime)]" />
                <p className="text-sm font-medium text-neutral-950">{item}</p>
              </div>
            ))}
          </div>
        </section>

        <div className="w-full">
          <div className="mb-6 text-center lg:hidden">
            <div className="mx-auto mb-4 grid h-11 w-11 place-items-center rounded-lg border border-neutral-950 bg-neutral-950 text-sm font-semibold text-[var(--signal-lime)]">
              K
            </div>
            <h1 className="text-2xl font-semibold text-neutral-950">
              潜在需求分析系统
            </h1>
            <p className="mt-2 text-sm text-neutral-600">
              基于智能体 Harness 架构的企业需求挖掘平台
            </p>
          </div>

          <Card padding="lg" variant="bordered">
            <div className="mb-6">
              <p className="mb-2 text-xs font-semibold text-neutral-500">SECURE LOGIN</p>
              <h2 className="text-xl font-semibold text-neutral-950">登录</h2>
              <p className="mt-1 text-sm text-neutral-600">
              请输入您的账号信息
              </p>
            </div>

            <form onSubmit={handleSubmit} className="space-y-5">
              <Input
                label="用户名"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="请输入用户名"
                autoComplete="username"
                required
              />

              <Input
                label="密码"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="请输入密码"
                autoComplete="current-password"
                required
              />

              <Button
                type="submit"
                variant="primary"
                size="lg"
                isLoading={isLoading}
                className="w-full"
              >
                {isLoading ? '登录中...' : '登录'}
              </Button>
            </form>
          </Card>

          <p className="mt-6 text-center text-sm text-neutral-500">
            还没有账号？{' '}
            <Link href="/register" className="font-medium text-neutral-950 underline underline-offset-4 hover:text-neutral-600">
              注册账号
            </Link>
          </p>

          <div className="mt-6 rounded-lg border border-neutral-950/10 bg-white/70 p-4">
            <p className="text-center text-xs text-neutral-600">
              账号由系统管理员创建；首次使用请按部署说明获取登录信息。
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={
      <div className="flex min-h-[calc(100vh-4rem)] items-center justify-center">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-neutral-950 border-t-transparent" />
      </div>
    }>
      <LoginForm />
    </Suspense>
  );
}
