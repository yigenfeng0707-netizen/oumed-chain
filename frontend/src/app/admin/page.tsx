"use client";

/**
 * 管理后台（超级管理员）
 *
 * - 登录：账号密码 → X-Admin-Token（存 sessionStorage）
 * - 总览：全用户使用概况 + 全局统计 + 慢病分布（精准推送分群参考）
 * - 画像：单用户健康画像 / 近期对话 / EEG / 影像 / 档案摘要
 *
 * 说明：Demo 阶段无真实登录日志，「使用情况」基于对话/脑电/影像等活动记录统计。
 */

import { useCallback, useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  LayoutDashboard,
  Users,
  MessageSquare,
  Brain,
  ScanLine,
  LogOut,
  Loader2,
  Lock,
  RefreshCw,
  Activity,
  HeartPulse,
  FileText,
  ChevronRight,
  Wallet,
} from "lucide-react";
import { motion } from "framer-motion";
import { API_BASE } from "@/lib/api";

const TOKEN_KEY = "oumed_admin_token";

interface AdminUserRow {
  id: number;
  public_id: string;
  name: string;
  age: number;
  gender: string;
  city: string;
  insurance_type: string;
  employee_status: string;
  conditions: string[];
  usage: {
    conversations: number;
    messages: number;
    eeg_sessions: number;
    imaging_studies: number;
    body_records: number;
    medical_visits: number;
  };
  last_active_at: string | null;
  active_7d: boolean;
}

interface OverviewData {
  generated_at: string;
  global_stats: {
    total_users: number;
    active_users_7d: number;
    total_conversations: number;
    total_messages: number;
    total_eeg_sessions: number;
    total_imaging_studies: number;
    condition_distribution: Record<string, number>;
  };
  users: AdminUserRow[];
}

interface UserProfileData {
  basic: {
    name: string;
    age: number;
    gender: string;
    city: string;
    insurance_type: string;
    employee_status: string;
    registered_at: string | null;
  };
  health_profile: Record<string, unknown>;
  conversations: Array<{
    id: string;
    title: string;
    updated_at: string | null;
    message_count: number;
    last_message: string;
  }>;
  eeg_history: Array<{
    recorded_at: string | null;
    mental_state_label: string;
    alert_count: number;
    policy_link_count: number;
    summary: string;
  }>;
  imaging_history: Array<{
    recorded_at: string | null;
    study_type: string;
    risk_level: string;
    finding_count: number;
    policy_link_count: number;
  }>;
  body_organ_summary: Record<string, { label: string; count: number; latest_event_date: string }>;
}

interface PaymentsData {
  total: number;
  mode: string;
  paid_count: number;
  revenue_cents: number;
  orders: Array<{
    order_no: string;
    kind: string;
    ref_id: string;
    subject: string;
    amount_cents: number;
    status: string;
    gateway: string;
    pay_proof: string | null;
    paid_at: string | null;
    created_at: string | null;
  }>;
}

const fadeIn = {
  initial: { opacity: 0, y: 20 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.4 },
};

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export default function AdminPage() {
  const [token, setToken] = useState<string | null>(null);
  const [booting, setBooting] = useState(true);
  const [data, setData] = useState<OverviewData | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);
  const [profile, setProfile] = useState<UserProfileData | null>(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [payments, setPayments] = useState<PaymentsData | null>(null);
  const [error, setError] = useState("");

  // 登录表单
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loggingIn, setLoggingIn] = useState(false);
  const [loginError, setLoginError] = useState("");

  useEffect(() => {
    setToken(sessionStorage.getItem(TOKEN_KEY));
    setBooting(false);
  }, []);

  const authFetch = useCallback(
    async (path: string): Promise<Response | null> => {
      if (!token) return null;
      try {
        return await fetch(`${API_BASE}${path}`, {
          headers: { "X-Admin-Token": token },
          signal: AbortSignal.timeout(30000),
        });
      } catch {
        return null;
      }
    },
    [token]
  );

  const loadPayments = useCallback(async () => {
    const res = await authFetch("/api/admin/payments?limit=50");
    if (res && res.ok) {
      setPayments(await res.json());
    }
  }, [authFetch]);

  const loadOverview = useCallback(async () => {
    setLoading(true);
    setError("");
    const res = await authFetch("/api/admin/overview");
    if (res && res.ok) {
      setData(await res.json());
    } else if (res && res.status === 401) {
      sessionStorage.removeItem(TOKEN_KEY);
      setToken(null);
      setError("登录已失效，请重新登录");
    } else {
      setError("无法连接后端服务，请确认后端已启动");
    }
    setLoading(false);
  }, [authFetch]);

  useEffect(() => {
    if (token && !data) loadOverview();
    if (token && !payments) loadPayments();
  }, [token, data, payments, loadOverview, loadPayments]);

  const loadProfile = useCallback(
    async (userId: number) => {
      setProfileLoading(true);
      setProfile(null);
      const res = await authFetch(`/api/admin/users/${userId}/profile`);
      if (res && res.ok) {
        setProfile(await res.json());
      } else {
        setError("加载用户画像失败");
      }
      setProfileLoading(false);
    },
    [authFetch]
  );

  const handleLogin = async () => {
    if (!username || !password) {
      setLoginError("请输入账号和密码");
      return;
    }
    setLoggingIn(true);
    setLoginError("");
    try {
      const res = await fetch(`${API_BASE}/api/admin/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
        signal: AbortSignal.timeout(15000),
      });
      if (res.ok) {
        const body = await res.json();
        sessionStorage.setItem(TOKEN_KEY, body.token);
        setToken(body.token);
      } else {
        const body = await res.json().catch(() => ({ detail: "登录失败" }));
        setLoginError(body.detail || "账号或密码错误");
      }
    } catch {
      setLoginError("无法连接后端服务，请确认后端已启动");
    }
    setLoggingIn(false);
  };

  const handleLogout = () => {
    sessionStorage.removeItem(TOKEN_KEY);
    setToken(null);
    setData(null);
    setPayments(null);
  };

  const openProfile = (userId: number) => {
    setSelectedUserId(userId);
    loadProfile(userId);
  };

  // ============ 登录页 ============
  if (!booting && !token) {
    return (
      <div className="flex min-h-[calc(100vh-3.5rem)] items-center justify-center bg-gradient-to-b from-background to-background/80 p-4">
        <motion.div {...fadeIn} className="w-full max-w-sm">
          <Card className="border shadow-lg">
            <CardContent className="p-6 sm:p-8">
              <div className="mb-6 flex flex-col items-center">
                <div className="mb-3 flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-slate-700 to-slate-900 shadow-lg">
                  <Lock className="h-7 w-7 text-white" />
                </div>
                <h1 className="text-xl font-bold text-foreground">OuMedTrust 管理后台</h1>
                <p className="mt-1 text-xs text-muted-foreground">
                  超级管理员登录 · 全用户使用概况与画像分析
                </p>
              </div>
              <div className="space-y-4">
                <div>
                  <label className="mb-1 block text-xs font-medium text-muted-foreground">账号</label>
                  <Input
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder="admin"
                    onKeyDown={(e) => e.key === "Enter" && handleLogin()}
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-muted-foreground">密码</label>
                  <Input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="••••••••"
                    onKeyDown={(e) => e.key === "Enter" && handleLogin()}
                  />
                </div>
                {loginError && (
                  <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">{loginError}</p>
                )}
                <Button className="w-full gap-2" onClick={handleLogin} disabled={loggingIn}>
                  {loggingIn ? <Loader2 className="h-4 w-4 animate-spin" /> : <LayoutDashboard className="h-4 w-4" />}
                  {loggingIn ? "登录中…" : "登录管理后台"}
                </Button>
                <p className="text-center text-[11px] text-muted-foreground">
                  管理员账号由部署环境变量 ADMIN_USERNAME / ADMIN_PASSWORD 配置
                </p>
              </div>
            </CardContent>
          </Card>
        </motion.div>
      </div>
    );
  }

  // ============ 控制台 ============
  const stats = data?.global_stats;

  return (
    <div className="p-4 space-y-6 sm:p-6">
      {/* 页头 */}
      <motion.div {...fadeIn}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h1 className="flex items-center gap-2 text-2xl font-bold text-foreground">
              <LayoutDashboard className="h-6 w-6 text-slate-600" />
              管理后台
            </h1>
            <p className="text-sm text-muted-foreground">
              全用户使用概况 · 画像分析 · 精准推送参考
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => { loadOverview(); loadPayments(); }} disabled={loading} className="gap-1.5">
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
              刷新
            </Button>
            <Button variant="ghost" size="sm" onClick={handleLogout} className="gap-1.5 text-muted-foreground">
              <LogOut className="h-3.5 w-3.5" />
              退出
            </Button>
          </div>
        </div>
      </motion.div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">
          {error}
        </div>
      )}

      {loading && !data && (
        <div className="flex h-40 items-center justify-center text-muted-foreground">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" /> 加载使用概况…
        </div>
      )}

      {stats && (
        <>
          {/* 全局统计 */}
          <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-6">
            <StatCard icon={Users} label="总用户数" value={stats.total_users} delay={0.05} />
            <StatCard icon={Activity} label="7日活跃" value={stats.active_users_7d} delay={0.08} />
            <StatCard icon={MessageSquare} label="对话总数" value={stats.total_conversations} delay={0.11} />
            <StatCard icon={FileText} label="消息总数" value={stats.total_messages} delay={0.14} />
            <StatCard icon={Brain} label="脑电会话" value={stats.total_eeg_sessions} delay={0.17} />
            <StatCard icon={ScanLine} label="影像分析" value={stats.total_imaging_studies} delay={0.2} />
          </div>

          {/* 慢病分布（精准推送分群参考） */}
          {Object.keys(stats.condition_distribution).length > 0 && (
            <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.25 }}>
              <Card className="rounded-xl border border-gray-100 bg-white shadow-sm">
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center gap-2 text-base font-semibold">
                    <HeartPulse className="h-4 w-4 text-rose-500" />
                    慢病人群分布
                    <Badge variant="secondary" className="text-xs">精准推送分群参考</Badge>
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
                    {Object.entries(stats.condition_distribution).map(([name, count]) => (
                      <div key={name} className="rounded-lg border border-rose-100 bg-rose-50/50 p-3">
                        <p className="text-sm font-medium text-foreground">{name}</p>
                        <p className="mt-1 text-2xl font-bold text-rose-600">{count}<span className="text-xs font-normal text-muted-foreground"> 人</span></p>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            </motion.div>
          )}

          {/* 用户列表 */}
          <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.3 }}>
            <Card className="rounded-xl border border-gray-100 bg-white shadow-sm">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-base font-semibold">
                  <Users className="h-4 w-4 text-slate-500" />
                  用户使用概况
                  <Badge variant="secondary" className="text-xs">{stats.total_users} 人</Badge>
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[860px]">
                    <thead>
                      <tr className="border-b border-border text-xs text-muted-foreground">
                        <th className="pb-3 pr-4 text-left font-medium">用户</th>
                        <th className="px-2 pb-3 text-center font-medium">慢病标签</th>
                        <th className="px-2 pb-3 text-center font-medium">对话</th>
                        <th className="px-2 pb-3 text-center font-medium">消息</th>
                        <th className="px-2 pb-3 text-center font-medium">脑电</th>
                        <th className="px-2 pb-3 text-center font-medium">影像</th>
                        <th className="px-2 pb-3 text-center font-medium">档案</th>
                        <th className="px-2 pb-3 text-center font-medium">最近活跃</th>
                        <th className="pb-3 pl-2 text-right font-medium">操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data?.users.map((u) => (
                        <tr key={u.id} className="border-b border-border/50 last:border-0">
                          <td className="py-3 pr-4">
                            <div>
                              <p className="text-sm font-medium">{u.name}</p>
                              <p className="text-xs text-muted-foreground">
                                {u.age}岁 · {u.city} · {u.insurance_type}
                              </p>
                            </div>
                          </td>
                          <td className="px-2 py-3 text-center">
                            <div className="flex flex-wrap justify-center gap-1">
                              {u.conditions.length === 0 ? (
                                <span className="text-xs text-muted-foreground">—</span>
                              ) : (
                                u.conditions.map((c) => (
                                  <Badge key={c} variant="secondary" className="bg-rose-50 text-xs text-rose-600">
                                    {c}
                                  </Badge>
                                ))
                              )}
                            </div>
                          </td>
                          <td className="px-2 py-3 text-center text-sm">{u.usage.conversations}</td>
                          <td className="px-2 py-3 text-center text-sm">{u.usage.messages}</td>
                          <td className="px-2 py-3 text-center text-sm">{u.usage.eeg_sessions}</td>
                          <td className="px-2 py-3 text-center text-sm">{u.usage.imaging_studies}</td>
                          <td className="px-2 py-3 text-center text-sm">{u.usage.body_records}</td>
                          <td className="px-2 py-3 text-center">
                            <span className={`text-xs ${u.active_7d ? "text-green-600" : "text-muted-foreground"}`}>
                              {u.active_7d && <span className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-green-500 align-middle" />}
                              {fmtTime(u.last_active_at)}
                            </span>
                          </td>
                          <td className="py-3 pl-2 text-right">
                            <Button
                              variant="ghost"
                              size="sm"
                              className="gap-0.5 text-primary"
                              onClick={() => openProfile(u.id)}
                            >
                              画像 <ChevronRight className="h-3.5 w-3.5" />
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          </motion.div>

          {/* 支付对账（支付宝当面付：Agent 微支付 / 数据产品结算） */}
          {payments && (
            <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.35 }}>
              <Card className="rounded-xl border border-gray-100 bg-white shadow-sm">
                <CardHeader className="pb-2">
                  <CardTitle className="flex flex-wrap items-center gap-2 text-base font-semibold">
                    <Wallet className="h-4 w-4 text-cyan-600" />
                    支付对账（支付宝当面付）
                    <Badge variant="secondary" className="text-xs">
                      {payments.mode === "sandbox" ? "沙箱模式" : "真实收款"}
                    </Badge>
                    <Badge variant="secondary" className="text-xs">已收 ¥{(payments.revenue_cents / 100).toLocaleString("zh-CN", { minimumFractionDigits: 2 })}</Badge>
                    <Badge variant="secondary" className="text-xs">{payments.paid_count}/{payments.total} 笔已付</Badge>
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {payments.orders.length === 0 ? (
                    <p className="text-xs text-muted-foreground">暂无支付订单（在数据要素市场「扫码购买」即可产生订单）</p>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full min-w-[720px]">
                        <thead>
                          <tr className="border-b border-border text-xs text-muted-foreground">
                            <th className="pb-2 pr-4 text-left font-medium">订单号</th>
                            <th className="px-2 pb-2 text-left font-medium">商品/服务</th>
                            <th className="px-2 pb-2 text-center font-medium">类型</th>
                            <th className="px-2 pb-2 text-right font-medium">金额</th>
                            <th className="px-2 pb-2 text-center font-medium">状态</th>
                            <th className="pb-2 pl-2 text-right font-medium">支付时间/凭证</th>
                          </tr>
                        </thead>
                        <tbody>
                          {payments.orders.map((o) => (
                            <tr key={o.order_no} className="border-b border-border/50 last:border-0">
                              <td className="py-2.5 pr-4 font-mono text-xs">{o.order_no}</td>
                              <td className="max-w-[260px] truncate px-2 py-2.5 text-sm">{o.subject}</td>
                              <td className="px-2 py-2.5 text-center">
                                <Badge variant="secondary" className="text-[10px]">
                                  {o.kind === "marketplace" ? "数据产品" : "Agent 服务"}
                                </Badge>
                              </td>
                              <td className="px-2 py-2.5 text-right text-sm font-semibold text-cyan-700">
                                ¥{(o.amount_cents / 100).toLocaleString("zh-CN", { minimumFractionDigits: 2 })}
                              </td>
                              <td className="px-2 py-2.5 text-center">
                                <Badge className={o.status === "paid" ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"}>
                                  {o.status === "paid" ? "已支付" : "待支付"}
                                </Badge>
                              </td>
                              <td className="py-2.5 pl-2 text-right text-[11px] text-muted-foreground">
                                {o.status === "paid" ? (
                                  <>
                                    {fmtTime(o.paid_at)}
                                    {o.pay_proof && <div className="font-mono text-[9px]">凭证 {o.pay_proof}</div>}
                                  </>
                                ) : (
                                  fmtTime(o.created_at)
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </CardContent>
              </Card>
            </motion.div>
          )}

          <p className="text-xs text-muted-foreground">
            数据更新于 {fmtTime(data?.generated_at)} · 管理后台仅限授权人员使用，涉及个人信息请遵守《个人信息保护法》《数据安全法》及医疗数据合规要求
          </p>
        </>
      )}

      {/* 用户画像弹窗 */}
      <Dialog open={selectedUserId !== null} onOpenChange={(open) => !open && setSelectedUserId(null)}>
        <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
          {profileLoading && (
            <div className="flex h-40 items-center justify-center text-muted-foreground">
              <Loader2 className="mr-2 h-5 w-5 animate-spin" /> 加载用户画像…
            </div>
          )}
          {profile && (
            <>
              <DialogHeader>
                <DialogTitle className="flex flex-wrap items-center gap-2 text-xl">
                  {profile.basic.name} 的画像分析
                  <Badge variant="secondary" className="text-xs">
                    {profile.basic.age}岁 · {profile.basic.gender} · {profile.basic.city}
                  </Badge>
                </DialogTitle>
                <DialogDescription>
                  {profile.basic.insurance_type} · {profile.basic.employee_status} · 注册于 {fmtTime(profile.basic.registered_at)}
                </DialogDescription>
              </DialogHeader>

              <div className="mt-2 space-y-5">
                {/* 慢病与用药 */}
                <Section title="慢病与用药画像" icon={HeartPulse}>
                  <div className="mb-2 flex flex-wrap gap-1">
                    {(profile.health_profile.chronic_diseases as string[] | undefined)?.length ? (
                      (profile.health_profile.chronic_diseases as string[]).map((c) => (
                        <Badge key={c} className="bg-rose-100 text-rose-700">{c}</Badge>
                      ))
                    ) : (
                      <span className="text-xs text-muted-foreground">未检出慢病标签</span>
                    )}
                  </div>
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                    <MiniStat label="就诊次数" value={String(profile.health_profile.recent_visits ?? 0)} />
                    <MiniStat label="近6月就诊" value={String(profile.health_profile.visit_count_6m ?? 0)} />
                    <MiniStat label="年医疗支出" value={`¥${Number(profile.health_profile.annual_medical_cost ?? 0).toLocaleString()}`} />
                    <MiniStat label="年用药支出" value={`¥${Number(profile.health_profile.annual_medication_cost ?? 0).toLocaleString()}`} />
                  </div>
                  {(profile.health_profile.diagnoses as string[] | undefined)?.length ? (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {(profile.health_profile.diagnoses as string[]).slice(0, 8).map((d) => (
                        <Badge key={d} variant="outline" className="text-xs">{d}</Badge>
                      ))}
                    </div>
                  ) : null}
                </Section>

                {/* 近期对话 */}
                <Section title="近期对话记录" icon={MessageSquare}>
                  {profile.conversations.length === 0 ? (
                    <p className="text-xs text-muted-foreground">暂无对话记录</p>
                  ) : (
                    <div className="space-y-2">
                      {profile.conversations.map((c) => (
                        <div key={c.id} className="rounded-lg bg-gray-50 px-3 py-2">
                          <div className="flex flex-wrap items-center justify-between gap-1">
                            <p className="text-sm font-medium">{c.title}</p>
                            <span className="text-xs text-muted-foreground">
                              {c.message_count} 条 · {fmtTime(c.updated_at)}
                            </span>
                          </div>
                          {c.last_message && (
                            <p className="mt-0.5 truncate text-xs text-muted-foreground">{c.last_message}</p>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </Section>

                {/* 脑电历史 */}
                <Section title="脑电健康历史" icon={Brain}>
                  {profile.eeg_history.length === 0 ? (
                    <p className="text-xs text-muted-foreground">暂无脑电记录</p>
                  ) : (
                    <div className="space-y-2">
                      {profile.eeg_history.map((e, i) => (
                        <div key={i} className="flex flex-wrap items-center justify-between gap-1 rounded-lg bg-purple-50/60 px-3 py-2 text-xs">
                          <span className="font-medium text-purple-700">{e.mental_state_label}</span>
                          <span className="text-muted-foreground">{fmtTime(e.recorded_at)}</span>
                          <span className="flex items-center gap-2">
                            {e.alert_count > 0 && <Badge className="bg-red-100 text-xs text-red-600">预警 {e.alert_count}</Badge>}
                            {e.policy_link_count > 0 && <Badge variant="secondary" className="text-xs">政策联动 {e.policy_link_count}</Badge>}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </Section>

                {/* 影像历史 */}
                <Section title="影像分析历史" icon={ScanLine}>
                  {profile.imaging_history.length === 0 ? (
                    <p className="text-xs text-muted-foreground">暂无影像记录</p>
                  ) : (
                    <div className="space-y-2">
                      {profile.imaging_history.map((s, i) => (
                        <div key={i} className="flex flex-wrap items-center justify-between gap-1 rounded-lg bg-violet-50/60 px-3 py-2 text-xs">
                          <span className="font-medium text-violet-700">{s.study_type}</span>
                          <span className="text-muted-foreground">{fmtTime(s.recorded_at)}</span>
                          <span className="flex items-center gap-2">
                            <Badge variant="secondary" className="text-xs">发现 {s.finding_count} 项</Badge>
                            <Badge
                              className={`text-xs ${
                                s.risk_level === "高"
                                  ? "bg-red-100 text-red-600"
                                  : s.risk_level === "中"
                                    ? "bg-amber-100 text-amber-600"
                                    : "bg-green-100 text-green-600"
                              }`}
                            >
                              {s.risk_level}
                            </Badge>
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </Section>

                {/* 人体档案 */}
                <Section title="数字人体档案" icon={Activity}>
                  {Object.keys(profile.body_organ_summary).length === 0 ? (
                    <p className="text-xs text-muted-foreground">暂无档案记录</p>
                  ) : (
                    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                      {Object.values(profile.body_organ_summary).map((o) => (
                        <div key={o.label} className="rounded-lg bg-cyan-50/60 px-3 py-2 text-xs">
                          <p className="font-medium text-cyan-700">{o.label}</p>
                          <p className="mt-0.5 text-muted-foreground">
                            {o.count} 条记录{o.latest_event_date ? ` · 最近 ${o.latest_event_date}` : ""}
                          </p>
                        </div>
                      ))}
                    </div>
                  )}
                </Section>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
  delay,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: number;
  delay: number;
}) {
  return (
    <motion.div {...fadeIn} transition={{ duration: 0.4, delay }}>
      <Card className="rounded-xl border border-gray-100 bg-white shadow-sm">
        <CardContent className="p-4">
          <div className="mb-2 flex h-9 w-9 items-center justify-center rounded-lg bg-slate-100">
            <Icon className="h-5 w-5 text-slate-600" />
          </div>
          <p className="text-xs text-muted-foreground">{label}</p>
          <p className="text-2xl font-bold text-foreground">{value.toLocaleString()}</p>
        </CardContent>
      </Card>
    </motion.div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-gray-50 px-3 py-2">
      <p className="text-[11px] text-muted-foreground">{label}</p>
      <p className="text-sm font-semibold text-foreground">{value}</p>
    </div>
  );
}

function Section({
  title,
  icon: Icon,
  children,
}: {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h4 className="mb-2 flex items-center gap-2 text-sm font-semibold text-foreground">
        <Icon className="h-4 w-4 text-slate-500" />
        {title}
      </h4>
      {children}
    </div>
  );
}
