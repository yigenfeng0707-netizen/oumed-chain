"use client";

import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/button";
import {
  Lock,
  Shield,
  Eye,
  Database,
  FileText,
  Activity,
  Clock,
  CheckCircle2,
  AlertTriangle,
  UserCheck,
  Key,
  Trash2,
  Download,
  Info,
  Loader2,
} from "lucide-react";
import { motion } from "framer-motion";
import { getSecurityOverview, updateAuthorization } from "@/lib/api";
import { useUser } from "@/lib/user-context";
import type { SecurityOverview } from "@/lib/mock-data";
import { ApiStatusIndicator } from "@/components/api-status-indicator";
import { BrandedPageHeader } from "@/components/branded-page-header";

const fadeIn = {
  initial: { opacity: 0, y: 20 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.4 },
};

const iconComponentMap: Record<string, React.ComponentType<{ className?: string }>> = {
  Shield,
  Activity,
  FileText,
  Database,
};

const rightIconMap: Record<string, React.ComponentType<{ className?: string }>> = {
  Eye,
  UserCheck,
  Trash2,
  Download,
};

function SkeletonCard() {
  return (
    <Card className="didayi-card">
      <CardContent className="p-6">
        <div className="flex items-center justify-center h-32">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      </CardContent>
    </Card>
  );
}

export default function SecurityPage() {
  const [data, setData] = useState<SecurityOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const { userId } = useUser();

  useEffect(() => {
    setLoading(true);
    getSecurityOverview(userId).then((result) => {
      setData(result);
      setLoading(false);
    });
  }, [userId]);

  if (loading) {
    return (
      <div className="didayi-page space-y-5">
        <motion.div {...fadeIn}>
          <h1 className="text-2xl font-bold text-foreground">数据授权</h1>
          <p className="text-sm text-muted-foreground">管理您的数据授权，保护个人隐私</p>
        </motion.div>
        <SkeletonCard />
        <div className="grid gap-5 lg:grid-cols-3">
          <div className="lg:col-span-2"><SkeletonCard /></div>
          <SkeletonCard />
        </div>
      </div>
    );
  }

  const dataTypes = data?.data_types || [];
  const agents = data?.agents || [];
  const authMatrix = data?.authorization_matrix || [];
  const rights = data?.rights || [];
  const activeAuths = data?.active_auths || [];
  const auditLog = data?.audit_log || [];

  // 构建 authorization map 方便查找
  const authMap = new Map<string, { enabled: boolean; expiry: string }>();
  for (const entry of authMatrix) {
    authMap.set(`${entry.data_type}-${entry.agent}`, { enabled: entry.enabled, expiry: entry.expiry });
  }

  const handleToggleAuth = async (dataType: string, agent: string, currentEnabled: boolean) => {
    const key = `${dataType}-${agent}`;
    const current = authMap.get(key);
    if (!current) return;

    // Optimistic update
    const newEnabled = !currentEnabled;
    setData((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        authorization_matrix: prev.authorization_matrix.map((entry) =>
          entry.data_type === dataType && entry.agent === agent
            ? { ...entry, enabled: newEnabled, expiry: newEnabled ? (entry.expiry || "2026-12-31") : "" }
            : entry
        ),
        active_authorizations: newEnabled
          ? prev.active_authorizations + 1
          : Math.max(0, prev.active_authorizations - 1),
      };
    });

    try {
      await updateAuthorization({
        data_type: dataType,
        agent,
        enabled: newEnabled,
      });
    } catch {
      // Revert on failure
      setData((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          authorization_matrix: prev.authorization_matrix.map((entry) =>
            entry.data_type === dataType && entry.agent === agent
              ? { ...entry, enabled: currentEnabled, expiry: current.expiry }
              : entry
          ),
          active_authorizations: currentEnabled
            ? prev.active_authorizations + 1
            : Math.max(0, prev.active_authorizations - 1),
        };
      });
    }
  };

  return (
    <div className="didayi-page space-y-5">
      {/* Page Header */}
      <motion.div {...fadeIn}>
        <BrandedPageHeader title="数据授权" description="清晰管理每一项数据访问权限，所有授权均可查看和撤销。" badge="隐私守护" status={<ApiStatusIndicator />} />
      </motion.div>

      {/* Authorization Overview */}
      <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.1 }}>
        <Card className="overflow-hidden border border-sky-100 bg-gradient-to-r from-cyan-50 via-sky-50 to-white text-slate-700 shadow-[0_14px_34px_rgba(45,145,183,.12)]">
          <CardContent className="p-6">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:gap-6">
              <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl bg-cyan-100">
                <Lock className="h-7 w-7 text-cyan-600" />
              </div>
              <div className="flex-1">
                <h3 className="text-lg font-bold">数据安全总览</h3>
                <p className="mt-0.5 text-sm text-slate-500">
                  您已授权 {agents.length} 个智能体访问 {new Set(authMatrix.filter((a) => a.enabled).map((a) => a.data_type)).size} 类数据，所有授权均可随时撤销
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
                <div className="text-center">
                  <p className="text-2xl font-bold">{data?.active_authorizations || 0}</p>
                  <p className="text-xs text-slate-400">活跃授权</p>
                </div>
                <div className="text-center">
                  <p className="text-2xl font-bold">{data?.anomalies || 0}</p>
                  <p className="text-xs text-slate-400">异常访问</p>
                </div>
                <div className="text-center">
                  <p className="text-2xl font-bold">{data?.today_accesses || 0}</p>
                  <p className="text-xs text-slate-400">今日访问</p>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      </motion.div>

      <div className="grid gap-5 lg:grid-cols-3">
        {/* Authorization Matrix */}
        <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.15 }} className="lg:col-span-2">
          <Card className="didayi-card">
            <CardHeader className="pb-2">
              <CardTitle className="text-base font-semibold flex items-center gap-2">
                <Key className="h-4 w-4 text-gray-500" />
                授权管理
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px]">
                  <thead>
                    <tr className="border-b border-border">
                      <th className="text-left text-xs font-medium text-muted-foreground pb-3 pr-4 w-48">
                        数据类型
                      </th>
                      {agents.map((agent) => (
                        <th key={agent.id} className="text-center text-xs font-medium pb-3 px-2">
                          <Badge variant="secondary" className={`text-xs ${agent.color}`}>
                            {agent.name}
                          </Badge>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {dataTypes.map((dataType, i) => (
                      <tr key={dataType.id} className={i < dataTypes.length - 1 ? "border-b border-border/50" : ""}>
                        <td className="py-4 pr-4">
                          <div className="flex items-center gap-2">
                            {(() => {
                              const IconComp = iconComponentMap[dataType.icon];
                              return IconComp ? <IconComp className="h-4 w-4 text-muted-foreground shrink-0" /> : <Shield className="h-4 w-4 text-muted-foreground shrink-0" />;
                            })()}
                            <div>
                              <p className="text-sm font-medium">{dataType.name}</p>
                              <p className="text-xs text-muted-foreground">{dataType.desc}</p>
                            </div>
                          </div>
                        </td>
                        {agents.map((agent) => {
                          const auth = authMap.get(`${dataType.id}-${agent.id}`);
                          return (
                            <td key={agent.id} className="text-center py-4 px-2">
                              {auth ? (
                                <div className="flex flex-col items-center gap-1">
                                  <Switch checked={auth.enabled} onCheckedChange={() => handleToggleAuth(dataType.id, agent.id, auth.enabled)} />
                                  {auth.enabled && auth.expiry && (
                                    <span className="text-xs text-muted-foreground">
                                      至{auth.expiry.slice(5)}
                                    </span>
                                  )}
                                </div>
                              ) : (
                                <Switch checked={false} onCheckedChange={() => handleToggleAuth(dataType.id, agent.id, false)} />
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </motion.div>

        {/* Data Rights */}
        <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.2 }}>
          <Card className="didayi-card h-full">
            <CardHeader className="pb-2">
              <CardTitle className="text-base font-semibold flex items-center gap-2">
                <Shield className="h-4 w-4 text-gray-500" />
                数据权利
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {rights.map((right, i) => {
                const IconComp = rightIconMap[right.icon];
                const colorClass = right.color;
                return (
                  <div key={i} className={`p-3 rounded-lg ${colorClass}`}>
                    <div className="flex items-center gap-2 mb-1">
                      {IconComp ? <IconComp className="h-4 w-4 text-blue-600" /> : <Eye className="h-4 w-4 text-blue-600" />}
                      <span className="text-sm font-medium">{right.title}</span>
                    </div>
                    <p className="text-xs leading-relaxed opacity-70">
                      {right.desc}
                    </p>
                  </div>
                );
              })}
              <Button variant="outline" className="w-full gap-1.5 text-xs mt-2" size="sm">
                <Download className="h-3.5 w-3.5" />
                导出我的数据
              </Button>
            </CardContent>
          </Card>
        </motion.div>
      </div>

      {/* Active Authorizations + Audit Log */}
      <div className="grid gap-5 lg:grid-cols-2">
        {/* Active Authorizations */}
        <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.25 }}>
          <Card className="didayi-card">
            <CardHeader className="pb-2">
              <CardTitle className="text-base font-semibold flex items-center gap-2">
                <CheckCircle2 className="h-4 w-4 text-gray-500" />
                活跃授权
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                {activeAuths.map((auth, i) => {
                  const IconComp = iconComponentMap[auth.data_type_icon];
                  return (
                    <div
                      key={i}
                      className="flex items-center justify-between p-3 rounded-lg bg-gray-50/80"
                    >
                      <div className="flex items-center gap-3">
                        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-white shadow-sm">
                          {IconComp ? <IconComp className="h-4 w-4 text-muted-foreground" /> : <Shield className="h-4 w-4 text-muted-foreground" />}
                        </div>
                        <div>
                          <p className="text-sm font-medium">
                            {auth.agent_name} → {auth.data_type_name}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            授权读取
                          </p>
                        </div>
                      </div>
                      <div className="text-right">
                        <Badge variant="secondary" className="text-xs bg-green-50 text-green-600">
                          已授权
                        </Badge>
                        {auth.expiry && (
                          <p className="text-xs text-muted-foreground mt-1">
                            有效期至 {auth.expiry}
                          </p>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </CardContent>
          </Card>
        </motion.div>

        {/* Audit Log */}
        <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.3 }}>
          <Card className="didayi-card">
            <CardHeader className="pb-2">
              <CardTitle className="text-base font-semibold flex items-center gap-2">
                <Clock className="h-4 w-4 text-gray-500" />
                访问审计日志
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                {auditLog.map((log, i) => (
                  <div key={i} className="flex items-start gap-3">
                    <div className="flex flex-col items-center">
                      <div
                        className={`flex h-6 w-6 items-center justify-center rounded-full ${
                          log.status === "allowed"
                            ? "bg-green-100"
                            : "bg-red-100"
                        }`}
                      >
                        {log.status === "allowed" ? (
                          <CheckCircle2 className="h-3.5 w-3.5 text-green-600" />
                        ) : (
                          <AlertTriangle className="h-3.5 w-3.5 text-red-600" />
                        )}
                      </div>
                      {i < auditLog.length - 1 && (
                        <div className="w-px h-4 bg-border" />
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-medium">{log.action}</p>
                        <Badge
                          variant="secondary"
                          className={`text-xs ${
                            log.status === "allowed"
                              ? "bg-green-50 text-green-600"
                              : "bg-red-50 text-red-600"
                          }`}
                        >
                          {log.status === "allowed" ? "允许" : "拒绝"}
                        </Badge>
                      </div>
                      <p className="text-xs text-muted-foreground">
                        {log.agent} · {log.dataType}
                      </p>
                      <p className="text-xs text-muted-foreground">{log.time}</p>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </motion.div>
      </div>

      {/* Security Notice */}
      <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.35 }}>
        <Card className="bg-gray-50 border-gray-200">
          <CardContent className="p-4">
            <div className="flex items-start gap-3">
              <Info className="h-4 w-4 text-muted-foreground mt-0.5 shrink-0" />
              <p className="text-xs text-muted-foreground leading-relaxed">
                您的数据受到严格保护，所有授权均可随时撤销。AI 助手仅在您授权的范围内访问数据，不会存储或分享您的个人信息。
                所有数据访问均记录在审计日志中，您可以随时查看。如发现异常访问，请立即撤销相关授权并联系客服。
              </p>
            </div>
          </CardContent>
        </Card>
      </motion.div>
    </div>
  );
}
