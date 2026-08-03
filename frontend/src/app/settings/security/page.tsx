"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { PageHeader, PageShell } from "@/components/ui/workspace";
import { authenticatedFetch } from "@/lib/auth";

type SecurityConfig = {
  ssrf_block_list: string[];
  allowed_domains: string[];
  block_file_protocol: boolean;
  block_gopher_protocol: boolean;
  block_ftp_protocol: boolean;
  dns_rebinding_protection: boolean;
  max_response_size_mb: number;
  max_redirect_chain: number;
};

const DEFAULTS: SecurityConfig = {
  ssrf_block_list: ["127.0.0.1", "localhost", "0.0.0.0", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "::1", "fc00::/7"],
  allowed_domains: [],
  block_file_protocol: true,
  block_gopher_protocol: true,
  block_ftp_protocol: true,
  dns_rebinding_protection: true,
  max_response_size_mb: 20,
  max_redirect_chain: 10,
};

function apiHeaders(): Record<string, string> {
  return { "Content-Type": "application/json" };
}

export default function SecuritySettingsPage() {
  const { error: toastError, success: toastSuccess } = useToast();

  const [config, setConfig] = useState<SecurityConfig>(DEFAULTS);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [newDomain, setNewDomain] = useState("");
  const [newBlockAddr, setNewBlockAddr] = useState("");

  useEffect(() => {
    authenticatedFetch("/api/config/security")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data) setConfig({ ...DEFAULTS, ...data });
      })
      .catch(() => toastError("加载配置失败"))
      .finally(() => setIsLoading(false));
  }, []);

  const save = async () => {
    setIsSaving(true);
    try {
      const res = await authenticatedFetch("/api/config/security", {
        method: "PUT",
        headers: apiHeaders(),
        body: JSON.stringify(config),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toastSuccess("安全配置已保存");
    } catch (err) {
      toastError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setIsSaving(false);
    }
  };

  const addDomain = () => {
    const d = newDomain.trim();
    if (!d || config.allowed_domains.includes(d)) return;
    setConfig({ ...config, allowed_domains: [...config.allowed_domains, d] });
    setNewDomain("");
  };

  const removeDomain = (domain: string) => {
    setConfig({ ...config, allowed_domains: config.allowed_domains.filter((d) => d !== domain) });
  };

  const addBlockAddr = () => {
    const a = newBlockAddr.trim();
    if (!a || config.ssrf_block_list.includes(a)) return;
    setConfig({ ...config, ssrf_block_list: [...config.ssrf_block_list, a] });
    setNewBlockAddr("");
  };

  const removeBlockAddr = (addr: string) => {
    setConfig({ ...config, ssrf_block_list: config.ssrf_block_list.filter((a) => a !== addr) });
  };

  if (isLoading) {
    return <PageShell><PageHeader title="安全配置" /><p className="text-neutral-500 px-4">加载中...</p></PageShell>;
  }

  return (
    <PageShell>
      <PageHeader title="安全配置" description="SSRF 防护、域名白名单和安全策略" />

      <div className="space-y-6 max-w-2xl">
        {/* SSRF 阻止列表 */}
        <Card variant="bordered" padding="lg">
          <h3 className="font-semibold text-neutral-800 mb-3">SSRF 阻止地址</h3>
          <p className="text-sm text-neutral-500 mb-4">禁止访问的内网地址和特殊协议</p>
          <div className="flex flex-wrap gap-2 mb-4">
            {config.ssrf_block_list.map((addr) => (
              <span key={addr} className="inline-flex items-center gap-1 rounded-full bg-red-50 border border-red-200 px-3 py-1 text-sm text-red-700">
                {addr}
                <button onClick={() => removeBlockAddr(addr)} className="text-red-400 hover:text-red-700">&times;</button>
              </span>
            ))}
          </div>
          <div className="flex gap-2">
            <input type="text" value={newBlockAddr} onChange={(e) => setNewBlockAddr(e.target.value)}
              placeholder="添加阻止地址（如 10.0.0.0/8）"
              className="flex-1 rounded-lg border border-neutral-950/20 bg-white px-4 py-2" />
            <Button variant="secondary" size="sm" onClick={addBlockAddr}>添加</Button>
          </div>
        </Card>

        {/* 放行域名 */}
        <Card variant="bordered" padding="lg">
          <h3 className="font-semibold text-neutral-800 mb-3">放行域名（白名单）</h3>
          <p className="text-sm text-neutral-500 mb-4">高级用户可手动放行特定域名，操作有风险</p>
          <div className="flex flex-wrap gap-2 mb-4">
            {config.allowed_domains.length === 0 && (
              <p className="text-sm text-neutral-400">暂无自定义放行域名</p>
            )}
            {config.allowed_domains.map((d) => (
              <span key={d} className="inline-flex items-center gap-1 rounded-full bg-yellow-50 border border-yellow-200 px-3 py-1 text-sm text-yellow-700">
                {d}
                <button onClick={() => removeDomain(d)} className="text-yellow-400 hover:text-yellow-700">&times;</button>
              </span>
            ))}
          </div>
          <div className="flex gap-2">
            <input type="text" value={newDomain} onChange={(e) => setNewDomain(e.target.value)}
              placeholder="添加放行域名（如 *.example.com）"
              className="flex-1 rounded-lg border border-neutral-950/20 bg-white px-4 py-2" />
            <Button variant="secondary" size="sm" onClick={addDomain}>添加</Button>
          </div>
        </Card>

        {/* 协议阻止 */}
        <Card variant="bordered" padding="lg">
          <h3 className="font-semibold text-neutral-800 mb-3">协议与请求安全</h3>
          <div className="space-y-4">
            {[
              { label: "阻止 file:// 协议", key: "block_file_protocol" as const },
              { label: "阻止 gopher:// 协议", key: "block_gopher_protocol" as const },
              { label: "阻止 ftp:// 协议", key: "block_ftp_protocol" as const },
              { label: "DNS Rebinding 保护", key: "dns_rebinding_protection" as const },
            ].map(({ label, key }) => (
              <label key={key} className="flex items-center justify-between">
                <span className="font-medium text-neutral-800">{label}</span>
                <input type="checkbox" checked={config[key]}
                  onChange={(e) => setConfig({ ...config, [key]: e.target.checked })}
                  className="h-4 w-4 accent-neutral-950" />
              </label>
            ))}
          </div>
        </Card>

        {/* 响应限制 */}
        <Card variant="bordered" padding="lg">
          <h3 className="font-semibold text-neutral-800 mb-3">响应体限制</h3>
          {[
            { label: "最大响应体 (MB)", key: "max_response_size_mb" as const, min: 1, max: 100 },
            { label: "最大重定向链长度", key: "max_redirect_chain" as const, min: 1, max: 30 },
          ].map(({ label, key, min, max }) => (
            <div key={key}>
              <label className="mb-1.5 block text-sm font-medium text-neutral-700">
                {label}: <span className="font-semibold text-neutral-950">{config[key]}</span>
              </label>
              <input type="range" min={min} max={max} value={config[key]}
                onChange={(e) => setConfig({ ...config, [key]: parseInt(e.target.value) })}
                className="w-full accent-neutral-950" />
            </div>
          ))}
        </Card>

        <div className="flex gap-3">
          <Button variant="primary" size="lg" onClick={save} isLoading={isSaving}>
            {isSaving ? "保存中..." : "保存配置"}
          </Button>
        </div>
      </div>
    </PageShell>
  );
}
