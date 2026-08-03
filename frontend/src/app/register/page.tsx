"use client";

import Link from "next/link";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

export default function RegisterPage() {
  return (
    <main className="flex min-h-[calc(100vh-4rem)] items-center justify-center p-4">
      <div className="w-full max-w-md text-center">
        <Card variant="bordered" padding="lg">
          <div className="mx-auto mb-5 grid h-14 w-14 place-items-center rounded-lg border border-neutral-950 bg-neutral-950 text-sm font-semibold text-[var(--signal-lime)]">
            LOCK
          </div>
          <h1 className="mb-2 text-xl font-semibold text-neutral-950">
            注册功能暂未开放
          </h1>
          <p className="mb-6 text-sm leading-6 text-neutral-600">
            当前版本不支持自主注册，请联系管理员获取账号。
          </p>
          <Link href="/login">
            <Button variant="secondary" type="button">
              返回登录
            </Button>
          </Link>
        </Card>
      </div>
    </main>
  );
}
