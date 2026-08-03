"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { PageHeader, PageShell, StatusBadge } from "@/components/ui/workspace";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useToast } from "@/components/ui/toast";
import { TargetAccountForm } from "@/app/components/target-account-form";
import {
  createTargetAccount,
  listTargetAccounts,
  type TargetAccount,
  type TargetAccountInput,
} from "@/lib/target-accounts";

const STATUS_LABEL: Record<TargetAccount["status"], string> = {
  UNRESOLVED: "待消歧",
  CONFIRMED: "已确认",
  ARCHIVED: "已归档",
};

export default function CustomersPage() {
  const [accounts, setAccounts] = useState<TargetAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const { error: toastError, success: toastSuccess } = useToast();

  const load = async () => {
    setLoading(true);
    try {
      setAccounts(await listTargetAccounts());
    } catch (error) {
      toastError(error instanceof Error ? error.message : "目标企业加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const submit = async (input: TargetAccountInput) => {
    setCreating(true);
    try {
      const result = await createTargetAccount(input);
      if (!result.created) {
        toastError(`发现 ${result.candidates.length} 个同名候选，请先在列表中确认企业主体。`);
      } else {
        toastSuccess("目标企业已创建");
        setShowForm(false);
      }
      await load();
    } catch (error) {
      toastError(error instanceof Error ? error.message : "目标企业创建失败");
    } finally {
      setCreating(false);
    }
  };

  return (
    <PageShell>
      <PageHeader
        eyebrow="TARGET ACCOUNTS"
        title="目标企业"
        description="企业名称是唯一必填项；其余信息仅用于消歧，不会阻断研究。"
        action={<Button variant="primary" onClick={() => setShowForm(true)}>新增目标企业</Button>}
      />

      {showForm && (
        <Card variant="bordered" padding="lg" className="mb-6">
          <h2 className="mb-4 text-base font-medium text-neutral-950">创建目标企业</h2>
          <TargetAccountForm submitting={creating} onSubmit={submit} onCancel={() => setShowForm(false)} />
        </Card>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-16 text-sm text-neutral-600">正在加载目标企业…</div>
      ) : accounts.length === 0 ? (
        <Card variant="bordered" padding="lg" className="py-16 text-center">
          <h2 className="text-lg font-medium text-neutral-950">还没有目标企业</h2>
          <p className="mt-2 text-sm text-neutral-600">先建立企业主体，再开始研究、消歧和商机验证。</p>
          <Button variant="primary" className="mt-5" onClick={() => setShowForm(true)}>创建第一个企业</Button>
        </Card>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {accounts.map((account) => (
            <Card key={account.id} variant="bordered" padding="md" className="space-y-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="truncate text-base font-medium text-neutral-950">{account.official_name || account.input_name}</h2>
                  {account.official_name && <p className="mt-1 truncate text-sm text-neutral-500">输入：{account.input_name}</p>}
                </div>
                <StatusBadge status={account.status} label={STATUS_LABEL[account.status]} />
              </div>
              <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
                <div><dt className="text-neutral-500">行业</dt><dd className="truncate text-neutral-800">{account.industry || "未填写"}</dd></div>
                <div><dt className="text-neutral-500">地区</dt><dd className="truncate text-neutral-800">{account.region || "未填写"}</dd></div>
              </dl>
              <Link
                href={`/customers/${account.id}`}
                className="inline-flex text-sm font-medium text-neutral-950 underline decoration-neutral-300 underline-offset-4 hover:decoration-neutral-950"
              >
                打开客户工作台
              </Link>
            </Card>
          ))}
        </div>
      )}
    </PageShell>
  );
}
