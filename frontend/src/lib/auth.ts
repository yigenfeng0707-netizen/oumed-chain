"use client";

/**
 * 用户认证（邮箱注册/登录）：token 存取 + API 封装。
 *
 * - token 存 localStorage（oumed_user_token），24h 有效（后端 HMAC 签发）
 * - 登录用户画像存 oumed_user_profile，用于启动时恢复会话
 * - 演示用户（user-switcher）不走此模块，两套体系并存
 */

import { API_BASE } from "./api";
import type { UserInfo } from "./mock-data";

const TOKEN_KEY = "oumed_user_token";
const PROFILE_KEY = "oumed_user_profile";

export interface AuthUser extends UserInfo {
  email?: string | null;
  registered?: boolean;
}

export interface AuthSession {
  token: string;
  user: AuthUser;
  expires_in: number;
}

function authHeaders(): Record<string, string> {
  const token = getAuthToken();
  return token ? { "X-User-Token": token } : {};
}

export function getAuthToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function getStoredUser(): AuthUser | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(PROFILE_KEY);
    return raw ? (JSON.parse(raw) as AuthUser) : null;
  } catch {
    return null;
  }
}

export function saveAuthSession(session: AuthSession) {
  window.localStorage.setItem(TOKEN_KEY, session.token);
  window.localStorage.setItem(PROFILE_KEY, JSON.stringify(session.user));
}

export function clearAuthSession() {
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(PROFILE_KEY);
}

async function authFetch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(30000),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(
      (data as { detail?: string }).detail ||
        (res.status === 401 ? "邮箱或密码错误" : "请求失败，请稍后重试"),
    );
  }
  return data as T;
}

export async function loginByEmail(email: string, password: string): Promise<AuthSession> {
  return authFetch<AuthSession>("/api/auth/login", { email, password });
}

export async function registerByEmail(
  email: string,
  password: string,
  name?: string,
): Promise<AuthSession> {
  return authFetch<AuthSession>("/api/auth/register", { email, password, name: name ?? "" });
}

/** 携带 token 校验当前会话（GET /api/auth/me），失败返回 null */
export async function fetchCurrentUser(): Promise<AuthUser | null> {
  const token = getAuthToken();
  if (!token) return null;
  try {
    const res = await fetch(`${API_BASE}/api/auth/me`, {
      headers: authHeaders(),
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as { user: AuthUser };
    return data.user;
  } catch {
    return null;
  }
}
