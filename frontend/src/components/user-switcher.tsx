"use client";

/**
 * 用户切换器：路演 Demo 神器
 * 顶部下拉，一键切换 10 个用户画像，全站数据联动。
 * 使用原生 details/summary 实现下拉，避免引入新依赖。
 */

import { useState, useEffect, useRef, type FormEvent } from "react";
import Link from "next/link";
import { Users, ChevronDown, Check, Plus, Loader2, LogIn, LogOut, Mail } from "lucide-react";
import { useUser } from "@/lib/user-context";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function UserSwitcher() {
  const { currentUser, users, setUserId, addUser, loggedIn, logoutUser } = useUser();
  const [open, setOpen] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [form, setForm] = useState({
    name: "",
    age: "",
    gender: "女",
    city: "",
    insurance_type: "职工医保",
    employee_status: "在职",
  });
  const ref = useRef<HTMLDivElement>(null);

  // 点击外部关闭
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  async function handleAddUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      await addUser({
        name: form.name.trim(),
        age: Number(form.age),
        gender: form.gender,
        city: form.city.trim(),
        insurance_type: form.insurance_type,
        employee_status: form.employee_status,
      });
      setAddOpen(false);
      setOpen(false);
      setForm({
        name: "",
        age: "",
        gender: "女",
        city: "",
        insurance_type: "职工医保",
        employee_status: "在职",
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "新增用户失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium shadow-sm transition hover:bg-slate-50"
        aria-label="切换演示用户"
      >
        <Users className="h-4 w-4 text-cyan-600" />
        <span className="hidden sm:inline">
          <span className="text-slate-500">{loggedIn ? "已登录：" : "演示用户："}</span>
          <span className="font-semibold text-slate-900">{currentUser.name}</span>
        </span>
        <span className="sm:hidden font-semibold">{currentUser.name}</span>
        <ChevronDown
          className={cn(
            "h-3.5 w-3.5 text-slate-400 transition-transform",
            open && "rotate-180"
          )}
        />
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-2 w-80 max-h-[70vh] overflow-y-auto rounded-xl border border-slate-200 bg-white p-1.5 shadow-xl">
          <div className="flex items-center gap-2 px-3 py-2 text-sm font-semibold text-slate-700 border-b border-slate-100 mb-1">
            <Users className="h-4 w-4 text-cyan-600" />
            切换演示用户
          </div>
          {/* 邮箱登录体系：登录入口 / 登录态 */}
          {loggedIn ? (
            <div className="mb-1 rounded-lg bg-cyan-50/70 px-3 py-2.5 text-sm">
              <div className="flex items-center gap-2 font-medium text-cyan-800">
                <Mail className="h-4 w-4" />
                <span className="truncate">{currentUser.email}</span>
              </div>
              <button
                onClick={() => {
                  logoutUser();
                  setOpen(false);
                }}
                className="mt-2 flex items-center gap-1.5 text-xs font-medium text-slate-500 transition hover:text-red-600"
              >
                <LogOut className="h-3.5 w-3.5" />
                退出登录（回到演示用户）
              </button>
            </div>
          ) : (
            <Link
              href="/login"
              onClick={() => setOpen(false)}
              className="mb-1 flex w-full items-center gap-2 rounded-lg border border-dashed border-cyan-200 px-3 py-2.5 text-sm font-medium text-cyan-700 transition hover:bg-cyan-50"
            >
              <LogIn className="h-4 w-4" />
              邮箱登录 / 注册
            </Link>
          )}
          <button
            onClick={() => {
              setAddOpen(true);
              setOpen(false);
            }}
            className="mb-1 flex w-full items-center gap-2 rounded-lg border border-dashed border-cyan-200 px-3 py-2.5 text-sm font-medium text-cyan-700 transition hover:bg-cyan-50"
          >
            <Plus className="h-4 w-4" />
            添加新用户
          </button>
          {users.map((u) => (
            <button
              key={u.id}
              onClick={() => {
                setUserId(u.id);
                setOpen(false);
              }}
              className={cn(
                "flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left transition hover:bg-slate-50",
                u.id === currentUser.id && "bg-cyan-50"
              )}
            >
              <div
                className={cn(
                  "flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-bold",
                  u.gender === "女"
                    ? "bg-pink-100 text-pink-700"
                    : "bg-cyan-100 text-cyan-700"
                )}
              >
                {u.name.slice(-1)}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-slate-900">{u.name}</span>
                  <span className="text-xs text-slate-400">
                    {u.age}岁 · {u.city}
                  </span>
                </div>
                <div className="text-xs text-slate-500 truncate">
                  {u.insurance_type} · {u.employee_status}
                  {u.conditions.length > 0 && (
                    <span className="ml-1 text-orange-600">
                      · {u.conditions.join("、")}
                    </span>
                  )}
                </div>
              </div>
              {u.id === currentUser.id && (
                <Check className="h-4 w-4 shrink-0 text-cyan-600" />
              )}
            </button>
          ))}
        </div>
      )}
      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>添加用户</DialogTitle>
            <DialogDescription>
              新用户保存到数据库后，会自动成为当前用户，并同步到聊天与数字人体档案。
            </DialogDescription>
          </DialogHeader>
          <form className="space-y-4" onSubmit={handleAddUser}>
            <div className="grid grid-cols-2 gap-3">
              <label className="space-y-1 text-sm">
                <span>姓名</span>
                <Input required maxLength={50} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="例如：林女士" />
              </label>
              <label className="space-y-1 text-sm">
                <span>年龄</span>
                <Input required type="number" min={0} max={130} value={form.age} onChange={(e) => setForm({ ...form, age: e.target.value })} />
              </label>
              <label className="space-y-1 text-sm">
                <span>性别</span>
                <select className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm" value={form.gender} onChange={(e) => setForm({ ...form, gender: e.target.value })}>
                  <option>女</option><option>男</option><option>其他</option>
                </select>
              </label>
              <label className="space-y-1 text-sm">
                <span>城市</span>
                <Input required maxLength={50} value={form.city} onChange={(e) => setForm({ ...form, city: e.target.value })} placeholder="例如：杭州" />
              </label>
              <label className="space-y-1 text-sm">
                <span>参保类型</span>
                <select className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm" value={form.insurance_type} onChange={(e) => setForm({ ...form, insurance_type: e.target.value })}>
                  <option>职工医保</option><option>居民医保</option><option>灵活就业医保</option><option>未参保</option>
                </select>
              </label>
              <label className="space-y-1 text-sm">
                <span>就业状态</span>
                <select className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm" value={form.employee_status} onChange={(e) => setForm({ ...form, employee_status: e.target.value })}>
                  <option>在职</option><option>退休</option><option>学生</option><option>灵活就业</option><option>未就业</option>
                </select>
              </label>
            </div>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setAddOpen(false)} disabled={saving}>取消</Button>
              <Button type="submit" disabled={saving}>
                {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                保存并切换
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
