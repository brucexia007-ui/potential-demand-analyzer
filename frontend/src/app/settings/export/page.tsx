"use client";

import { useState } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { PageHeader, PageShell } from "@/components/ui/workspace";
import { authenticatedFetch } from "@/lib/auth";

function apiHeaders(): Record<string, string> {
  return { "Content-Type": "application/json" };
}

export default function ExportSettingsPage() {
  const { error: toastError, success: toastSuccess } = useToast();

  const [isExporting, setIsExporting] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importPreview, setImportPreview] = useState<string | null>(null);

  const handleExport = async () => {
    setIsExporting(true);
    try {
      const res = await authenticatedFetch("/api/config/export");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `config-export-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      toastSuccess("配置已导出");
    } catch (err) {
      toastError(err instanceof Error ? err.message : "导出失败");
    } finally {
      setIsExporting(false);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setImportFile(file);
    const reader = new FileReader();
    reader.onload = (ev) => {
      try {
        const text = ev.target?.result as string;
        JSON.parse(text); // 校验 JSON 格式
        setImportPreview(text.slice(0, 2000));
      } catch {
        setImportPreview(null);
        toastError("文件不是有效的 JSON");
      }
    };
    reader.readAsText(file);
  };

  const handleImport = async () => {
    if (!importFile || !importPreview) return;
    setIsImporting(true);
    try {
      const data = JSON.parse(importPreview.length < importFile.size ? await importFile.text() : importPreview);
      const res = await authenticatedFetch("/api/config/import", {
        method: "POST",
        headers: apiHeaders(),
        body: JSON.stringify(data),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const result = await res.json();
      toastSuccess(`已导入: ${(result.imported || []).join(", ") || "无变更"}`);
      setImportFile(null);
      setImportPreview(null);
    } catch (err) {
      toastError(err instanceof Error ? err.message : "导入失败");
    } finally {
      setIsImporting(false);
    }
  };

  return (
    <PageShell>
      <PageHeader title="配置导入/导出" description="导出不含密钥的配置快照，或从文件恢复配置" />

      <div className="space-y-6 max-w-2xl">
        {/* 导出 */}
        <Card variant="bordered" padding="lg">
          <h3 className="font-semibold text-neutral-800 mb-2">导出配置</h3>
          <p className="text-sm text-neutral-500 mb-4">
            导出预算、抓取、数据保留和安全配置。API Key 等密钥不会被导出。
          </p>
          <Button variant="primary" onClick={handleExport} isLoading={isExporting}>
            {isExporting ? "导出中..." : "导出配置 (JSON)"}
          </Button>
        </Card>

        {/* 导入 */}
        <Card variant="bordered" padding="lg">
          <h3 className="font-semibold text-neutral-800 mb-2">导入配置</h3>
          <p className="text-sm text-neutral-500 mb-4">
            选择之前导出的 JSON 配置文件。导入后不会覆盖密钥配置。不支持的文件不会生效。
          </p>
          <div className="mb-4">
            <input type="file" accept=".json" onChange={handleFileChange}
              className="block w-full text-sm text-neutral-600 file:mr-4 file:rounded-lg file:border-0 file:bg-neutral-950 file:px-4 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-neutral-800" />
          </div>

          {importPreview && (
            <div className="mb-4 rounded-lg border border-neutral-950/10 bg-neutral-50 p-4 max-h-48 overflow-auto">
              <pre className="text-xs text-neutral-600 whitespace-pre-wrap">{importPreview}</pre>
            </div>
          )}

          <Button variant="primary" onClick={handleImport} isLoading={isImporting}
            disabled={!importFile || !importPreview}>
            {isImporting ? "导入中..." : "导入配置"}
          </Button>
        </Card>
      </div>
    </PageShell>
  );
}
