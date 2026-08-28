import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const packageJson = JSON.parse(
  await readFile(new URL("../package.json", import.meta.url), "utf8"),
);
const packageLock = JSON.parse(
  await readFile(new URL("../package-lock.json", import.meta.url), "utf8"),
);

function parseVersion(version) {
  const match = String(version).match(/^(\d+)\.(\d+)\.(\d+)$/);
  assert.ok(match, `依赖必须固定为精确版本，实际为 ${version}`);
  return match.slice(1).map(Number);
}

function atLeast(actual, minimum) {
  const current = parseVersion(actual);
  const required = parseVersion(minimum);
  for (let index = 0; index < current.length; index += 1) {
    if (current[index] !== required[index]) {
      return current[index] > required[index];
    }
  }
  return true;
}

test("production frontend uses a supported, July 2026 patched Next.js line", () => {
  assert.ok(
    atLeast(packageJson.dependencies.next, "15.5.21"),
    `Next.js ${packageJson.dependencies.next} 已停止支持或缺少 2026-07 安全修复`,
  );
  assert.ok(
    atLeast(packageJson.dependencies.react, "19.0.0"),
    "Next.js 15 App Router 必须使用 React 19",
  );
  assert.equal(
    packageLock.packages[""].dependencies.next,
    packageJson.dependencies.next,
    "package-lock.json 与 package.json 中的 Next.js 版本必须一致",
  );
});

test("transitive nanoid dependency uses the patched 3.3.18 release", () => {
  assert.equal(
    packageLock.packages["node_modules/nanoid"].version,
    "3.3.18",
    "nanoid 3.x 低于 3.3.18 会允许 size=0 的自定义生成器无限循环",
  );
});
