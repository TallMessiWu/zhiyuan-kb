// /api/v1 fetch 封装。鉴权 MVP 用 X-User 头（内网单团队，V1.1 换 SSO）。

const BASE = "/api/v1";
const CURRENT_USER = "wanglei"; // TODO(M1): 从登录态获取

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-User": CURRENT_USER,
      ...init?.headers,
    },
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body?.error?.message ?? `HTTP ${resp.status}`);
  }
  return resp.json() as Promise<T>;
}

export const get = <T>(path: string) => api<T>(path);
export const post = <T>(path: string, body: unknown) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });
