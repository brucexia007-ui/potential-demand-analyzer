# 前端故障排查指南

本文档记录前端开发和部署过程中遇到的常见问题和解决方案。

---

## 问题：登录页面白屏

**症状**: 访问 `http://localhost:3000/login` 页面显示空白

**错误信息**: 
```
Error: Module not found: Can't resolve '@/components/providers/auth-provider'
```

### 原因分析

Next.js 无法解析 `@/` 路径别名，因为 `tsconfig.json` 中缺少 `baseUrl` 和 `paths` 配置。

### 解决方案

1. **检查 tsconfig.json 配置**

确保 `frontend/tsconfig.json` 包含以下配置：

```json
{
  "compilerOptions": {
    "baseUrl": ".",
    "paths": {
      "@/*": ["./src/*"]
    },
    ...
  }
}
```

2. **清理缓存并重启**

```bash
cd frontend
rm -rf .next
npm run dev
```

3. **验证**

访问 `http://localhost:3000/login` 应该能看到完整的登录页面。

---

## 问题：useAuth must be used within an AuthProvider

**症状**: 页面报错，控制台显示 `useAuth must be used within an AuthProvider`

### 原因分析

`useAuth()` hook 必须在 `AuthProvider` 组件内部使用。`AuthProvider` 在 `app/layout.tsx` 中全局包裹。

### 解决方案

1. **确保布局正确配置**

检查 `app/layout.tsx`：

```tsx
import { AuthProvider } from "@/components/providers/auth-provider";

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <AuthProvider>
          {children}
        </AuthProvider>
      </body>
    </html>
  );
}
```

2. **页面组件必须是客户端组件**

如果页面使用 `useAuth()`，需要添加 `'use client'` 指令：

```tsx
'use client';

import { useAuth } from '@/components/providers/auth-provider';

export default function LoginPage() {
  const { user } = useAuth();
  // ...
}
```

---

## 问题：API 请求失败，后端未响应

**症状**: 登录或其他 API 请求失败，错误信息 `Failed to fetch`

### 原因分析

后端服务未运行或地址配置错误。

### 解决方案

1. **检查后端服务状态**

```bash
# Docker 部署
docker compose ps

# 本地运行
curl http://localhost:8000/health
```

2. **检查 API 地址配置**

检查 `src/lib/auth.ts` 中的 `API_BASE` 配置：

```typescript
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';
```

3. **检查 Next.js API 代理**

确保 `next.config.js` 配置了 API 重写规则：

```javascript
module.exports = {
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: 'http://localhost:8000/api/:path*',
      },
    ];
  },
};
```

---

## 问题：Tailwind CSS 样式不生效

**症状**: 页面显示正常但样式丢失

### 原因分析

1. Tailwind 配置错误
2. PostCSS 配置缺失
3. 样式文件未正确导入

### 解决方案

1. **检查配置文件**

确保存在以下文件：
- `tailwind.config.ts`
- `postcss.config.js`
- `app/globals.css`

2. **检查 globals.css 导入**

确保 `app/layout.tsx` 导入了全局样式：

```tsx
import "./globals.css";
```

3. **重启开发服务器**

```bash
rm -rf .next
npm run dev
```

---

## 问题：端口被占用

**症状**: `Port 3000 is in use`

### 解决方案

1. **查找占用端口的进程**

Windows:
```bash
netstat -ano | findstr :3000
taskkill /PID <PID> /F
```

macOS/Linux:
```bash
lsof -ti:3000 | xargs kill -9
```

2. **使用其他端口**

```bash
npx next dev -p 3001
```

---

## 调试技巧

### 查看 Next.js 编译错误

访问 `http://localhost:3000/_next/static/webpack/app-loader.js` 查看编译状态。

### 使用 Next.js 内置调试

在 `next.config.js` 中添加：

```javascript
module.exports = {
  logging: {
    fetches: {
      fullUrl: true,
    },
  },
};
```

### 浏览器调试

1. 打开开发者工具
2. 查看 Console 面板的错误信息
3. 查看 Network 面板的 API 请求

---

---

## 问题：passlib 与 bcrypt 5.0 不兼容

**症状**: 后端登录接口报 500，日志显示 `passlib.handlers.bcrypt` 报错

### 原因分析

`passlib` 库与 `bcrypt` 5.0+ 存在兼容性问题，调用 `pwd_context.hash()` 时会抛出 `AttributeError: module 'bcrypt' has no attribute '__about__'`。

### 解决方案

将 `app/db/auth.py` 中的 `passlib.context.CryptContext` 替换为直接使用 `bcrypt` 库：

```python
# 不推荐 - 使用 passlib
from passlib.context import CryptContext
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain, hashed):
    return pwd_context.verify(plain, hashed)

# 推荐 - 直接使用 bcrypt
import bcrypt

def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))

def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
```

---

## 问题：/api/auth/me 返回 500

**症状**: 登录成功，但调用 `/api/auth/me` 返回 500 错误

### 原因分析

`UserResponse` Pydantic 模型中 `id` 定义为 `str`，但 `User.id` 是 UUID 类型。FastAPI 尝试自动序列化时，UUID 对象无法直接转为字符串。

### 解决方案

返回响应时手动将 UUID 转为字符串：

```python
@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return UserResponse(
        id=str(current_user.id),
        username=current_user.username,
        is_active=current_user.is_active
    )
```

---

## 参考资料

- [Next.js 官方文档](https://nextjs.org/docs)
- [TypeScript 路径映射](https://www.typescriptlang.org/docs/handbook/module-resolution.html#path-mapping)
- [Tailwind CSS 文档](https://tailwindcss.com/docs)
