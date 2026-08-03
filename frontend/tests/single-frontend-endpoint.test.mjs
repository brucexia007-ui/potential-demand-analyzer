import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

const testDir = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(testDir, "..", "..");

function readProjectFile(name) {
  return readFileSync(path.join(projectRoot, name), "utf8");
}

test("Docker 部署只发布唯一前端地址 https://127.0.0.1:10443", () => {
  const baseCompose = readProjectFile("docker-compose.yml");
  const productionCompose = readProjectFile("docker-compose.prod.yml");
  const verificationCompose = readProjectFile("docker-compose.verify.yml");
  const productionEnvExample = readProjectFile(".env.production.example");
  const developmentEnvExample = readProjectFile(".env.example");

  assert.doesNotMatch(baseCompose, /["']3001:3000["']/);
  assert.doesNotMatch(baseCompose, /["']80:80["']/);

  assert.match(
    productionCompose,
    /["']\$\{APP_HTTPS_PORT:-10443\}:443["']/,
  );
  assert.doesNotMatch(productionCompose, /["']80:80["']/);

  assert.match(
    verificationCompose,
    /["']\$\{VERIFY_HTTPS_PORT:-10443\}:443["']/,
  );
  assert.doesNotMatch(verificationCompose, /VERIFY_HTTP_PORT/);

  assert.match(productionEnvExample, /^APP_HTTPS_PORT=10443$/m);
  assert.match(
    developmentEnvExample,
    /^# CORS_ALLOW_ORIGINS=https:\/\/127\.0\.0\.1:10443$/m,
  );
});
