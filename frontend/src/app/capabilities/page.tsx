"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { PageHeader, PageShell, StatusBadge } from "@/components/ui/workspace";
import {
  archiveCapabilityProfile,
  createCapabilityProfile,
  listCapabilityProfiles,
  setDefaultCapabilityProfile,
  type CapabilityProfile,
} from "@/lib/capabilities";

export default function CapabilitiesPage() {
  const [profiles, setProfiles] = useState<CapabilityProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [archiveTarget, setArchiveTarget] = useState<CapabilityProfile | null>(null);
  const [replacementId, setReplacementId] = useState("");
  const [name, setName] = useState("");
  const [legalEntityName, setLegalEntityName] = useState("");
  const [description, setDescription] = useState("");
  const [isDefault, setIsDefault] = useState(false);
  const { error: toastError, success: toastSuccess } = useToast();

  const load = async () => {
    setLoading(true);
    try {
      setProfiles(await listCapabilityProfiles());
    } catch (error) {
      toastError(error instanceof Error ? error.message : "能力档案加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    try {
      await createCapabilityProfile({
        name: name.trim(),
        legal_entity_name: legalEntityName.trim() || undefined,
        description: description.trim(),
        is_default: isDefault,
      });
      setName("");
      setLegalEntityName("");
      setDescription("");
      setIsDefault(false);
      setShowForm(false);
      toastSuccess("能力档案已创建");
      await load();
    } catch (error) {
      toastError(error instanceof Error ? error.message : "创建失败");
    } finally {
      setSaving(false);
    }
  };

  const makeDefault = async (profile: CapabilityProfile) => {
    setSaving(true);
    try {
      await setDefaultCapabilityProfile(profile.id);
      toastSuccess(`“${profile.name}”已设为默认档案`);
      await load();
    } catch (error) {
      toastError(error instanceof Error ? error.message : "默认档案切换失败");
    } finally {
      setSaving(false);
    }
  };

  const requestArchive = (profile: CapabilityProfile) => {
    const alternatives = profiles.filter((item) => item.id !== profile.id);
    setArchiveTarget(profile);
    setReplacementId(profile.is_default && alternatives.length > 0 ? alternatives[0].id : "");
  };

  const confirmArchive = async () => {
    if (!archiveTarget) return;
    const needsReplacement = archiveTarget.is_default && profiles.some((item) => item.id !== archiveTarget.id);
    if (needsReplacement && !replacementId) return;
    setSaving(true);
    try {
      await archiveCapabilityProfile(archiveTarget.id, replacementId || null);
      toastSuccess(`“${archiveTarget.name}”已归档`);
      setArchiveTarget(null);
      setReplacementId("");
      await load();
    } catch (error) {
      toastError(error instanceof Error ? error.message : "归档失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <PageShell>
      <PageHeader
        eyebrow="CAPABILITY CENTER"
        title="企业能力中心"
        description="按业务主体维护多套能力档案与多个产品版本。客户事实与我方能力分别取证，避免把宣传材料当成客户需求。"
        action={<Button variant="primary" onClick={() => setShowForm(true)}>新建能力档案</Button>}
      />

      {showForm && (
        <Card variant="bordered" padding="lg" className="mb-6">
          <form className="space-y-4" onSubmit={submit}>
            <h2 className="text-base font-medium text-neutral-950">新建能力档案</h2>
            <div className="grid gap-4 md:grid-cols-2">
              <Input label="档案名称 *" value={name} onChange={(event) => setName(event.target.value)} maxLength={255} />
              <Input label="企业法定名称" value={legalEntityName} onChange={(event) => setLegalEntityName(event.target.value)} maxLength={500} />
            </div>
            <label className="block text-sm font-medium text-neutral-700">
              档案说明
              <textarea
                className="mt-2 min-h-28 w-full rounded-lg border border-neutral-950/20 bg-white/90 px-4 py-3 text-neutral-950 focus:border-neutral-950 focus:outline-none focus:ring-2 focus:ring-neutral-950/20"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                maxLength={10000}
              />
            </label>
            <label className="flex items-center gap-2 text-sm text-neutral-700">
              <input type="checkbox" checked={isDefault} onChange={(event) => setIsDefault(event.target.checked)} className="h-4 w-4 accent-neutral-950" />
              设为默认能力档案
            </label>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="secondary" onClick={() => setShowForm(false)}>取消</Button>
              <Button type="submit" variant="primary" disabled={saving || !name.trim()}>{saving ? "保存中…" : "创建"}</Button>
            </div>
          </form>
        </Card>
      )}

      {loading ? (
        <div className="py-16 text-center text-sm text-neutral-600">正在加载能力档案…</div>
      ) : profiles.length === 0 ? (
        <Card variant="bordered" padding="lg" className="py-16 text-center">
          <h2 className="text-lg font-medium text-neutral-950">还没有企业能力档案</h2>
          <p className="mt-2 text-sm text-neutral-600">先创建档案，再录入产品、方案、案例与资质。</p>
          <Button variant="primary" className="mt-5" onClick={() => setShowForm(true)}>创建第一个档案</Button>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {profiles.map((profile) => (
            <Card key={profile.id} variant="bordered" padding="md" className="flex min-h-56 flex-col">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="truncate text-base font-medium text-neutral-950">{profile.name}</h2>
                  <p className="mt-1 truncate text-sm text-neutral-500">{profile.legal_entity_name || "未填写法定主体"}</p>
                </div>
                <StatusBadge status={profile.is_default ? "ACTIVE" : "DRAFT"} label={profile.is_default ? "默认" : "启用"} />
              </div>
              <p className="mt-4 line-clamp-3 flex-1 text-sm leading-6 text-neutral-600">{profile.description || "暂无档案说明"}</p>
              <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-neutral-950/10 pt-4">
                <Link href={`/capabilities/${profile.id}`} className="rounded-full bg-neutral-950 px-4 py-2 text-sm font-medium text-white">管理产品</Link>
                {!profile.is_default && <Button size="sm" variant="secondary" disabled={saving} onClick={() => makeDefault(profile)}>设为默认</Button>}
                <Button size="sm" variant="ghost" disabled={saving} onClick={() => requestArchive(profile)}>归档</Button>
              </div>
            </Card>
          ))}
        </div>
      )}

      {archiveTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center px-4">
          <div className="absolute inset-0 bg-black/30 backdrop-blur-sm" onClick={() => setArchiveTarget(null)} />
          <Card variant="bordered" padding="lg" className="relative w-full max-w-md">
            <h2 className="text-lg font-semibold text-neutral-950">归档能力档案</h2>
            <p className="mt-2 text-sm text-neutral-600">归档“{archiveTarget.name}”后，其产品也会停止参与新的自动匹配。</p>
            {archiveTarget.is_default && profiles.some((item) => item.id !== archiveTarget.id) && (
              <label className="mt-4 block text-sm font-medium text-neutral-700">
                选择新的默认档案 *
                <select
                  value={replacementId}
                  onChange={(event) => setReplacementId(event.target.value)}
                  className="mt-2 w-full rounded-lg border border-neutral-950/20 bg-white px-4 py-2.5"
                >
                  {profiles.filter((item) => item.id !== archiveTarget.id).map((item) => (
                    <option key={item.id} value={item.id}>{item.name}</option>
                  ))}
                </select>
              </label>
            )}
            <div className="mt-6 flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setArchiveTarget(null)}>取消</Button>
              <Button variant="danger" disabled={saving} onClick={confirmArchive}>{saving ? "归档中…" : "确认归档"}</Button>
            </div>
          </Card>
        </div>
      )}
    </PageShell>
  );
}
