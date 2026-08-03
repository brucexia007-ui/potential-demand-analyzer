"use client";

import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { authenticatedFetch } from "@/lib/auth";

type Delivery = {
  id: string;
  schema_version: string;
  target_account_id: string;
  idempotency_key: string;
  destination_display: string;
  status: "PREVIEWED" | "CONFIRMED" | "SENDING" | "SUCCEEDED" | "FAILED" | "EXPIRED";
  expires_at: string;
  confirmed_at: string | null;
  completed_at: string | null;
  attempt_count: number;
  http_status: number | null;
  failure_code: string | null;
  failure_message: string | null;
};

type Preview = Delivery & {
  created: boolean;
  payload: {
    claims?: unknown[];
    hypotheses?: unknown[];
    qualifications?: unknown[];
    actions?: unknown[];
    opportunities?: unknown[];
    [key: string]: unknown;
  };
};

type Props = {
  accountId: string;
  onError: (message: string) => void;
};

async function apiError(response: Response, fallback: string): Promise<Error> {
  const body = await response.json().catch(() => null);
  return new Error(typeof body?.detail === "string" ? body.detail : fallback);
}

export function BusinessExportDialog({ accountId, onError }: Props) {
  const [showWebhook, setShowWebhook] = useState(false);
  const [destinationUrl, setDestinationUrl] = useState("");
  const [signingSecret, setSigningSecret] = useState("");
  const [confirmationChecked, setConfirmationChecked] = useState(false);
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [delivery, setDelivery] = useState<Delivery | null>(null);
  const [busy, setBusy] = useState(false);

  const counts = useMemo(() => {
    if (!preview) return [];
    return [
      ["Claim", preview.payload.claims?.length ?? 0],
      ["商机假设", preview.payload.hypotheses?.length ?? 0],
      ["资格评估", preview.payload.qualifications?.length ?? 0],
      ["行动", preview.payload.actions?.length ?? 0],
      ["正式商机", preview.payload.opportunities?.length ?? 0],
    ] as const;
  }, [preview]);

  const download = async (format: "json" | "csv") => {
    setBusy(true);
    try {
      const response = await authenticatedFetch(
        `/api/integrations/target-accounts/${accountId}/exports/${format}`,
      );
      if (!response.ok) throw await apiError(response, "业务快照下载失败");
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = `business-export-${accountId}.${format}`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (error) {
      onError(error instanceof Error ? error.message : "业务快照下载失败");
    } finally {
      setBusy(false);
    }
  };

  const createPreview = async () => {
    if (!destinationUrl.trim()) {
      onError("请填写 HTTPS Webhook 地址");
      return;
    }
    const requestKey = idempotencyKey || `business-export:${crypto.randomUUID()}`;
    setIdempotencyKey(requestKey);
    setBusy(true);
    try {
      const response = await authenticatedFetch(
        `/api/integrations/target-accounts/${accountId}/webhook-previews`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ destination_url: destinationUrl.trim(), idempotency_key: requestKey }),
        },
      );
      if (!response.ok) throw await apiError(response, "Webhook 预览失败");
      const value = await response.json() as Preview;
      setPreview(value);
      setDelivery(null);
      setConfirmationChecked(false);
    } catch (error) {
      onError(error instanceof Error ? error.message : "Webhook 预览失败");
    } finally {
      setBusy(false);
    }
  };

  const confirmAndSend = async () => {
    if (!preview || !confirmationChecked) {
      onError("请先审阅载荷并显式确认发送");
      return;
    }
    if (new TextEncoder().encode(signingSecret).length < 32) {
      onError("签名密钥至少需要 32 字节");
      return;
    }
    setBusy(true);
    try {
      const response = await authenticatedFetch(
        `/api/integrations/webhook-deliveries/${preview.id}/confirm-and-send`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            confirmed: true,
            destination_url: destinationUrl.trim(),
            signing_secret: signingSecret,
          }),
        },
      );
      if (!response.ok) throw await apiError(response, "Webhook 发送失败");
      setDelivery(await response.json() as Delivery);
      setSigningSecret("");
    } catch (error) {
      onError(error instanceof Error ? error.message : "Webhook 发送失败");
    } finally {
      setBusy(false);
    }
  };

  const resetPreview = () => {
    setPreview(null);
    setDelivery(null);
    setConfirmationChecked(false);
    setSigningSecret("");
    setIdempotencyKey("");
  };

  return (
    <section data-testid="business-export" className="space-y-4">
      <div>
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-neutral-500">BUSINESS EXPORT</p>
        <h2 className="mt-1 text-lg font-semibold text-neutral-950">业务输出</h2>
        <p className="mt-1 text-sm text-neutral-600">导出客户、Claim、商机假设、资格、行动和正式商机；受控原文与执行载荷不包含在内。</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" size="sm" disabled={busy} onClick={() => void download("json")}>下载 JSON</Button>
        <Button variant="secondary" size="sm" disabled={busy} onClick={() => void download("csv")}>下载 CSV</Button>
        <Button variant="ghost" size="sm" disabled={busy} onClick={() => { setShowWebhook((value) => !value); resetPreview(); }}>
          {showWebhook ? "收起 Webhook" : "推送 Webhook"}
        </Button>
      </div>

      {showWebhook && (
        <div className="space-y-4 rounded-xl border border-neutral-200 bg-neutral-50 p-4">
          <label className="block text-sm font-medium text-neutral-700">
            HTTPS Webhook 地址
            <input
              type="url"
              value={destinationUrl}
              disabled={busy || Boolean(preview)}
              onChange={(event) => setDestinationUrl(event.target.value)}
              placeholder="https://crm.example.com/hooks/opportunities"
              className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm"
            />
          </label>

          {!preview ? (
            <Button size="sm" disabled={busy} onClick={() => void createPreview()}>{busy ? "正在校验…" : "生成发送预览"}</Button>
          ) : (
            <>
              <div className="rounded-lg border border-neutral-200 bg-white p-3 text-sm">
                <p className="font-medium text-neutral-950">待发送：{preview.schema_version}</p>
                <p className="mt-1 break-all text-xs text-neutral-500">目标：{preview.destination_display}</p>
                <div className="mt-3 grid grid-cols-2 gap-2">
                  {counts.map(([label, value]) => <p key={label} className="text-neutral-700">{label}：{value}</p>)}
                </div>
                <details className="mt-3">
                  <summary className="cursor-pointer text-xs font-medium text-neutral-700">检查完整 JSON 载荷</summary>
                  <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-neutral-950 p-3 text-[11px] text-neutral-100">{JSON.stringify(preview.payload, null, 2)}</pre>
                </details>
              </div>
              {!delivery && (
                <>
                  <label className="block text-sm font-medium text-neutral-700">
                    HMAC 签名密钥（至少 32 字节）
                    <input
                      type="password"
                      autoComplete="new-password"
                      value={signingSecret}
                      disabled={busy}
                      onChange={(event) => setSigningSecret(event.target.value)}
                      className="mt-1 w-full rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm"
                    />
                    <span className="mt-1 block text-xs font-normal text-neutral-500">密钥仅用于本次签名，不保存在服务器审计记录或浏览器存储中。</span>
                  </label>
                  <label className="flex items-start gap-2 text-sm text-neutral-700">
                    <input type="checkbox" checked={confirmationChecked} disabled={busy} onChange={(event) => setConfirmationChecked(event.target.checked)} className="mt-1" />
                    我已检查目标地址和完整载荷，确认向该外部系统发送业务数据。
                  </label>
                  <div className="flex gap-2">
                    <Button size="sm" disabled={busy || !confirmationChecked} onClick={() => void confirmAndSend()}>{busy ? "正在发送…" : "确认并发送一次"}</Button>
                    <Button variant="ghost" size="sm" disabled={busy} onClick={resetPreview}>取消预览</Button>
                  </div>
                </>
              )}
              {delivery && (
                <div className={`rounded-lg border p-3 text-sm ${delivery.status === "SUCCEEDED" ? "border-emerald-200 bg-emerald-50 text-emerald-900" : "border-red-200 bg-red-50 text-red-900"}`}>
                  <p className="font-medium">发送状态：{delivery.status}</p>
                  <p className="mt-1">HTTP {delivery.http_status ?? "未获得"} · 尝试 {delivery.attempt_count} 次</p>
                  {delivery.failure_message && <p className="mt-1">{delivery.failure_code}：{delivery.failure_message}</p>}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
