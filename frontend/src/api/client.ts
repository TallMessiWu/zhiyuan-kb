// /api/v1 fetch 封装。鉴权 MVP 用 X-User 头（内网单团队，V1.1 换 SSO）。

const BASE = "/api/v1";

/** 当前登录用户。TODO(V1.1): 换成 SSO 登录态，届时删除这个常量。 */
export const CURRENT_USER = "wanglei";

/** 后端统一错误体：{"error":{"code":"NOT_FOUND","message":"..."}} */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

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
    const body: { error?: { code?: string; message?: string } } = await resp
      .json()
      .catch(() => ({}));
    throw new ApiError(
      resp.status,
      body?.error?.code ?? `HTTP_${resp.status}`,
      body?.error?.message ?? `HTTP ${resp.status}`,
    );
  }
  return resp.json() as Promise<T>;
}

export const get = <T>(path: string) => api<T>(path);
export const post = <T>(path: string, body: unknown) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });
