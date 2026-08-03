export type LlmProviderPresetKey = "deepseek" | "kimi_k3" | "custom";

export type LlmProviderPresetValues = {
  name: string;
  provider_type: string;
  base_url: string;
  models: string;
  default_model: string;
  timeout_seconds: number;
  retry_count: number;
};

export const LLM_PROVIDER_PRESETS: Array<{
  key: LlmProviderPresetKey;
  label: string;
  description: string;
  values: LlmProviderPresetValues;
}> = [
  {
    key: "deepseek",
    label: "DeepSeek",
    description: "使用 DeepSeek 官方 OpenAI 兼容接口。",
    values: {
      name: "DeepSeek",
      provider_type: "openai_compatible",
      base_url: "https://api.deepseek.com/v1",
      models: "deepseek-chat, deepseek-reasoner",
      default_model: "deepseek-chat",
      timeout_seconds: 60,
      retry_count: 2,
    },
  },
  {
    key: "kimi_k3",
    label: "KIMI K3（中国站）",
    description:
      "使用 Kimi 开放平台国内端点；API Key 必须来自 platform.kimi.com，K3 始终开启思考模式。",
    values: {
      name: "KIMI K3",
      provider_type: "moonshot",
      base_url: "https://api.moonshot.cn/v1",
      models: "kimi-k3",
      default_model: "kimi-k3",
      timeout_seconds: 180,
      retry_count: 2,
    },
  },
  {
    key: "custom",
    label: "自定义 OpenAI 兼容接口",
    description: "手工配置 OpenAI 兼容的 Base URL 与模型名称。",
    values: {
      name: "",
      provider_type: "openai_compatible",
      base_url: "",
      models: "",
      default_model: "",
      timeout_seconds: 60,
      retry_count: 2,
    },
  },
];

export function getLlmProviderPreset(key: LlmProviderPresetKey) {
  const preset = LLM_PROVIDER_PRESETS.find((item) => item.key === key);
  if (!preset) {
    throw new Error(`未知 LLM Provider 预设: ${key}`);
  }
  return preset;
}

export function inferLlmProviderPreset(
  providerType: string,
  baseUrl: string | null,
  models: string[],
): LlmProviderPresetKey {
  if (
    providerType === "moonshot" &&
    baseUrl === "https://api.moonshot.cn/v1" &&
    models.includes("kimi-k3")
  ) {
    return "kimi_k3";
  }
  if (
    providerType === "openai_compatible" &&
    baseUrl === "https://api.deepseek.com/v1"
  ) {
    return "deepseek";
  }
  return "custom";
}
