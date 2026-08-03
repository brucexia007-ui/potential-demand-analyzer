import { test, expect } from '@playwright/test';

const BACKEND_URL = process.env.BACKEND_HEALTH_URL ?? 'http://localhost:8000';

test.describe('API Integration — Health', () => {
  test('health and readiness endpoints are available', async ({ request }) => {
    expect((await request.get(`${BACKEND_URL}/health`)).status()).toBe(200);
    expect((await request.get(`${BACKEND_URL}/ready`)).status()).toBe(200);
  });
});

test.describe('API Integration — HttpOnly Cookie Auth', () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test('login returns session metadata and authenticates subsequent requests', async ({ request }) => {
    const login = await request.post('/api/auth/login', {
      form: { username: 'admin', password: 'admin123' },
    });
    expect(login.status()).toBe(200);
    const body = await login.json();
    expect(body).toEqual({
      username: 'admin',
      access_expires_in_seconds: 1800,
      session_expires_in_seconds: 604800,
    });
    expect(body.access_token).toBeUndefined();
    expect(body.refresh_token).toBeUndefined();
    expect((await request.get('/api/auth/me')).status()).toBe(200);
    expect((await request.get('/api/tasks')).status()).toBe(200);
  });

  test('Bearer token without Cookie is rejected', async ({ request }) => {
    const response = await request.get('/api/auth/me', {
      headers: { Authorization: 'Bearer deliberately-invalid' },
    });
    expect(response.status()).toBe(401);
  });

  test('wrong password is rejected', async ({ request }) => {
    const response = await request.post('/api/auth/login', {
      form: { username: 'admin', password: 'wrong_password_xyz' },
    });
    expect(response.status()).toBe(401);
  });
});
