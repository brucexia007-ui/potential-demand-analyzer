# 前端开发指南

潜在需求分析系统的 Web 前端，基于 Next.js App Router 构建。

## 技术栈

- Next.js 15.5
- React 19.2
- TypeScript 5.5
- Tailwind CSS 3.4
- react-markdown + remark-gfm
- Playwright 真实栈 E2E

依赖的准确版本以 [package.json](package.json) 和 `package-lock.json` 为准。

## 本地开发

```bash
npm ci
npm run dev
```

开发服务器默认运行在 <http://localhost:3000>。浏览器请求使用同源 `/api/*`；本地代理目标由 `next.config.js` 与相关环境变量控制。

生产构建：

```bash
npm run build
npm run start
```

## 页面范围

```text
src/app/
  page.tsx                    # 自然语言建任务与研究计划预览
  setup/page.tsx              # 首次设置向导
  login/page.tsx              # 登录
  tasks/[id]/page.tsx         # 执行状态、澄清、报告与导出
  history/page.tsx            # 历史任务
  batches/                    # 批量任务
  capabilities/              # 能力中心
  customers/                 # 目标客户
  opportunities/             # 商机工作台
  dashboard/page.tsx         # 经营看板
  settings/                   # Provider、搜索、预算、抓取与安全配置
```

业务组件集中在 `src/app/components/`，认证、配置状态和公共布局位于 `src/components/`。

## 认证与首次设置

- 认证状态由 `AuthProvider` 管理，使用 HttpOnly Cookie 会话。
- 未登录访问受保护页面时跳转到 `/login`，登录后回到原页面。
- 系统尚未完成配置时进入 `/setup`；向导会测试 LLM、搜索、抓取与预算配置。
- “完成配置并开始使用”成功后进入新建任务页。

不要在前端环境变量、测试脚本或浏览器产物中保存真实 API Key。

## 路径别名

`@/` 指向 `src/`：

```json
{
  "compilerOptions": {
    "baseUrl": ".",
    "paths": {
      "@/*": ["./src/*"]
    }
  }
}
```

## 验证

构建与依赖安全回归：

```bash
node --test tests/security-dependencies.test.mjs
npm run build
```

真实候选栈 E2E：

```bash
npm run test:e2e:real
```

常用变量：

- `REAL_BASE_URL`：候选栈地址
- `REAL_USERNAME` / `REAL_PASSWORD`：本地测试账号
- `REAL_COMPANY_NAME`、`REAL_DEMAND_DIRECTION`：研究样本
- `REAL_RESEARCH_DEPTH`：`quick` 或 `standard`
- `REAL_EXISTING_TASK_ID`：从已完成任务继续验证报告、导出和历史回查

测试截图、下载文件、认证状态和报告产物位于忽略目录，不应提交到 Git。
