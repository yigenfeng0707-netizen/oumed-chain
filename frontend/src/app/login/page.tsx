"use client";

/**
 * 用户登录/注册页（邮箱 + 密码）。
 *
 * - 登录成功 → loginUser() 全站切换为登录用户（数据联动）→ 跳回首页
 * - 注册即登录（暂不做邮箱验证码）
 * - 演示用户体系（user-switcher）不受影响，两套并存
 */

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import { Loader2, LogIn, Mail, UserPlus, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DidaYiLogo } from "@/components/didayi-logo";
import { useUser } from "@/lib/user-context";
import { loginByEmail, registerByEmail } from "@/lib/auth";
import { cn } from "@/lib/utils";

type Mode = "login" | "register";

export default function LoginPage() {
  const router = useRouter();
  const { loginUser } = useUser();
  const [mode, setMode] = useState<Mode>("login");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ email: "", password: "", name: "" });

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const email = form.email.trim();
      const session =
        mode === "login"
          ? await loginByEmail(email, form.password)
          : await registerByEmail(email, form.password, form.name);
      loginUser(session);
      router.push("/");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "请求失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-50 via-cyan-50/40 to-slate-100 p-4">
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35 }}
        className="w-full max-w-md"
      >
        <div className="rounded-2xl border border-slate-200 bg-white p-8 shadow-xl">
          {/* 品牌区 */}
          <div className="mb-6 flex flex-col items-center gap-3 text-center">
            <div className="h-14 w-14 overflow-hidden rounded-2xl bg-cyan-50">
              <DidaYiLogo compact />
            </div>
            <div>
              <h1 className="text-xl font-bold text-slate-900">瓯医数链 · OuMedTrust</h1>
              <p className="mt-1 text-xs text-slate-500">
                注册后您的健康档案、医保分析与用药记录都将归属此账号
              </p>
            </div>
          </div>

          {/* 模式切换 */}
          <div className="mb-5 grid grid-cols-2 gap-1 rounded-lg bg-slate-100 p-1 text-sm font-medium">
            <button
              type="button"
              onClick={() => { setMode("login"); setError(""); }}
              className={cn(
                "flex items-center justify-center gap-1.5 rounded-md py-2 transition",
                mode === "login" ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700",
              )}
            >
              <LogIn className="h-4 w-4" /> 登录
            </button>
            <button
              type="button"
              onClick={() => { setMode("register"); setError(""); }}
              className={cn(
                "flex items-center justify-center gap-1.5 rounded-md py-2 transition",
                mode === "register" ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700",
              )}
            >
              <UserPlus className="h-4 w-4" /> 注册
            </button>
          </div>

          <form className="space-y-4" onSubmit={handleSubmit}>
            <label className="block space-y-1 text-sm">
              <span className="font-medium text-slate-700">邮箱</span>
              <div className="relative">
                <Mail className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                <Input
                  required
                  type="email"
                  autoComplete="email"
                  className="pl-9"
                  placeholder="you@example.com"
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                />
              </div>
            </label>

            {mode === "register" && (
              <label className="block space-y-1 text-sm">
                <span className="font-medium text-slate-700">
                  姓名 <span className="font-normal text-slate-400">（可选）</span>
                </span>
                <Input
                  maxLength={50}
                  autoComplete="name"
                  placeholder="不填则使用邮箱前缀"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </label>
            )}

            <label className="block space-y-1 text-sm">
              <span className="font-medium text-slate-700">密码</span>
              <Input
                required
                type="password"
                minLength={mode === "register" ? 8 : undefined}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                placeholder={mode === "register" ? "至少 8 位" : "请输入密码"}
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
              />
            </label>

            {error && (
              <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">{error}</p>
            )}

            <Button type="submit" className="w-full gap-2" disabled={busy}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : (mode === "login" ? <LogIn className="h-4 w-4" /> : <UserPlus className="h-4 w-4" />)}
              {busy ? "处理中…" : mode === "login" ? "登录" : "注册并登录"}
            </Button>
          </form>

          <div className="mt-6 flex items-center gap-2 rounded-lg bg-cyan-50/60 px-3 py-2.5 text-[11px] leading-relaxed text-cyan-800">
            <ShieldCheck className="h-4 w-4 shrink-0" />
            <span>
              密码使用 PBKDF2-SHA256 加盐哈希存储，登录凭证为 HMAC 签名 token（24h 有效）。
              也可以不注册，通过顶部「演示用户」直接体验全部功能。
            </span>
          </div>
        </div>
      </motion.div>
    </div>
  );
}
