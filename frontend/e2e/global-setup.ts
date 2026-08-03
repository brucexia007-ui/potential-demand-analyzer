import { request, type FullConfig } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const BACKEND_URL = process.env.BACKEND_HEALTH_URL ?? 'http://localhost:8000';

async function checkBackend(): Promise<void> {
  const health = await fetch(`${BACKEND_URL}/health`);
  if (!health.ok) throw new Error(`Backend /health returned ${health.status}`);
  const ready = await fetch(`${BACKEND_URL}/ready`);
  if (!ready.ok) throw new Error(`Backend /ready returned ${ready.status}`);
}

export default async function globalSetup(config: FullConfig) {
  console.log('\n[globalSetup] Setting up HttpOnly Cookie session...');
  await checkBackend();

  const frontendUrl = String(config.projects?.[0]?.use?.baseURL ?? 'http://localhost:3001');
  const api = await request.newContext({ baseURL: frontendUrl });
  const login = await api.post('/api/auth/login', {
    form: { username: 'admin', password: 'admin123' },
  });
  if (!login.ok()) {
    throw new Error(`Login failed with status ${login.status()}: ${await login.text()}`);
  }
  const session = await login.json();
  if ('access_token' in session || 'refresh_token' in session) {
    throw new Error('Login response must not expose tokens');
  }

  const statusResponse = await api.get('/api/config/status');
  if (!statusResponse.ok()) {
    throw new Error(`Config status failed with ${statusResponse.status()}: ${await statusResponse.text()}`);
  }
  const status = await statusResponse.json();
  if (!status.llm?.configured) {
    const provider = await api.post('/api/config/providers', {
      data: {
        name: 'E2E Mock LLM',
        provider_type: 'openai_compatible',
        base_url: 'https://e2e.invalid/v1',
        api_key: 'e2e-only-key',
        models: ['e2e-model'],
        default_model: 'e2e-model',
      },
    });
    if (!provider.ok()) {
      throw new Error(`E2E LLM provider setup failed with ${provider.status()}: ${await provider.text()}`);
    }
  }
  if (!status.search?.configured) {
    const provider = await api.post('/api/config/search', {
      data: {
        name: 'E2E DuckDuckGo',
        provider_type: 'duckduckgo',
        enabled: true,
      },
    });
    if (!provider.ok()) {
      throw new Error(`E2E search provider setup failed with ${provider.status()}: ${await provider.text()}`);
    }
  }
  if (!status.setup_completed) {
    const setupComplete = await api.post('/api/config/setup-complete', {
      data: { mode: status.execution_ready ? 'READY' : 'BROWSE_ONLY' },
    });
    if (!setupComplete.ok()) {
      throw new Error(`Setup completion failed with ${setupComplete.status()}: ${await setupComplete.text()}`);
    }
  }

  const me = await api.get('/api/auth/me');
  if (!me.ok()) throw new Error(`Cookie session verification failed: ${me.status()}`);
  const user = await me.json();
  console.log(`  logged in as ${user.username} (id: ${user.id})`);

  const storageDir = path.join(__dirname, '.auth');
  fs.mkdirSync(storageDir, { recursive: true });
  await api.storageState({ path: path.join(storageDir, 'storageState.json') });
  await api.dispose();
  console.log('  HttpOnly Cookie storageState saved\n');
}
