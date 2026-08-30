"use client";

import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  BookOpen,
  Search,
  TrendingUp,
  CheckCircle2,
  ArrowRight,
  FileCheck,
  Users,
  Clock,
  ChevronRight,
  Star,
  Loader2,
} from "lucide-react";
import { motion } from "framer-motion";
import { getPolicyMatches } from "@/lib/api";
import { useUser } from "@/lib/user-context";
import type { PolicyMatch, MatchedPolicy } from "@/lib/mock-data";
import { ApiStatusIndicator } from "@/components/api-status-indicator";
import { BrandedPageHeader } from "@/components/branded-page-header";

const fadeIn = {
  initial: { opacity: 0, y: 20 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.4 },
};

export default function PolicyPage() {
  const [data, setData] = useState<PolicyMatch | null>(null);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedPolicy, setSelectedPolicy] = useState<MatchedPolicy | null>(null);
  const { userId } = useUser();

  useEffect(() => {
    setLoading(true);
    getPolicyMatches(userId).then((result) => {
      setData(result);
      setLoading(false);
    });
  }, [userId]);

  const policies = data?.policies || [];
  const totalSavings = data?.total_savings || 0;

  const filteredPolicies = policies.filter(
    (p) =>
      p.title.includes(searchQuery) ||
      p.category.includes(searchQuery) ||
      p.matchReason.includes(searchQuery)
  );

  if (loading) {
    return (
      <div className="didayi-page space-y-5">
        <motion.div {...fadeIn}>
          <h1 className="text-2xl font-bold text-foreground">政策匹配</h1>
          <p className="text-sm text-muted-foreground">智能匹配适合您的医保政策与优惠</p>
        </motion.div>
        <Card className="didayi-card">
          <CardContent className="p-6">
            <div className="flex items-center justify-center h-32">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="didayi-page space-y-5">
      {/* Page Header */}
      <motion.div {...fadeIn}>
        <BrandedPageHeader title="政策匹配" description="结合个人参保与健康情况，智能筛选可用政策和优惠权益。" badge="AI 智能匹配" status={<ApiStatusIndicator />} />
      </motion.div>

      {/* User Profile Summary */}
      <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.1 }}>
        <Card className="overflow-hidden border-0 bg-gradient-to-r from-[#0876a8] via-cyan-500 to-sky-400 text-white shadow-[0_16px_38px_rgba(14,149,190,.22)]">
          <CardContent className="p-6">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:gap-6">
              <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-white/20">
                <Users className="h-6 w-6 text-white" />
              </div>
              <div className="flex-1">
                <h3 className="text-lg font-bold">演示用户 · 职工医保</h3>
                <p className="text-sm text-white/80 mt-0.5">
                  糖尿病 · 高血压 | 缴费15年3个月 | 已匹配 {policies.length} 项政策
                </p>
              </div>
              <div className="text-left sm:text-right">
                <p className="text-sm text-white/70">预计可节省</p>
                <p className="text-2xl font-bold">¥{totalSavings.toLocaleString()}<span className="text-sm font-normal text-white/70">/年</span></p>
              </div>
            </div>
          </CardContent>
        </Card>
      </motion.div>

      {/* Search Bar */}
      <motion.div {...fadeIn} transition={{ duration: 0.4, delay: 0.15 }}>
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="搜索政策名称、类别..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-9 h-11 rounded-xl border-gray-200"
          />
        </div>
      </motion.div>

      {/* Policy Cards */}
      <div className="space-y-4">
        {filteredPolicies.map((policy, i) => (
          <motion.div
            key={policy.id}
            initial={{ opacity: 0, y: 15 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, delay: 0.2 + i * 0.05 }}
          >
            <Card className="didayi-card cursor-pointer transition-all hover:-translate-y-0.5 hover:shadow-[0_15px_34px_rgba(30,134,185,.14)] group"
              onClick={() => setSelectedPolicy(policy)}
            >
              <CardContent className="p-6">
                <div className="flex flex-wrap items-start gap-4">
                  {/* Match Score */}
                  <div className="flex flex-col items-center">
                    <div className="flex h-14 w-14 items-center justify-center rounded-xl bg-cyan-50">
                      <span className="text-lg font-bold text-cyan-600">{policy.matchScore}%</span>
                    </div>
                    <span className="text-xs text-muted-foreground mt-1">匹配度</span>
                  </div>

                  {/* Policy Info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <h3 className="text-base font-semibold text-foreground group-hover:text-cyan-600 transition-colors">
                        {policy.title}
                      </h3>
                      <Badge variant="secondary" className="text-xs bg-cyan-50 text-cyan-600">
                        {policy.category}
                      </Badge>
                    </div>
                    <p className="text-sm text-muted-foreground mb-2">{policy.matchReason}</p>
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                      <span className="flex items-center gap-1">
                        <Clock className="h-3 w-3" />
                        截止：{policy.deadline}
                      </span>
                      <span className="flex items-center gap-1">
                        <FileCheck className="h-3 w-3" />
                        需{policy.requirements.length}项材料
                      </span>
                    </div>
                  </div>

                  {/* Savings */}
                  <div className="ml-auto w-full shrink-0 text-right sm:ml-0 sm:w-auto">
                    <p className="text-lg font-bold text-green-600">{policy.savings}</p>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="mt-1 -mr-2 text-cyan-600 hover:text-cyan-700"
                    >
                      查看详情 <ChevronRight className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          </motion.div>
        ))}
      </div>

      {/* Policy Detail Modal */}
      <Dialog open={!!selectedPolicy} onOpenChange={(open) => !open && setSelectedPolicy(null)}>
        <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
          {selectedPolicy && (
            <>
              <DialogHeader>
                <div className="flex flex-wrap items-center gap-2">
                  <DialogTitle className="text-xl">{selectedPolicy.title}</DialogTitle>
                  <Badge className="border-0 bg-cyan-100 text-cyan-700">
                    {selectedPolicy.category}
                  </Badge>
                </div>
                <DialogDescription>{selectedPolicy.matchReason}</DialogDescription>
              </DialogHeader>

              <div className="space-y-6 mt-2">
                {/* Savings Highlight */}
                <div className="p-4 rounded-xl bg-gradient-to-r from-green-50 to-emerald-50 border border-green-100">
                  <div className="flex items-center gap-3">
                    <TrendingUp className="h-5 w-5 text-green-600" />
                    <div>
                      <p className="text-sm text-green-700">预计可节省</p>
                      <p className="text-2xl font-bold text-green-600">{selectedPolicy.savings}</p>
                    </div>
                  </div>
                </div>

                {/* Description */}
                <div>
                  <h4 className="text-sm font-semibold text-foreground mb-2">政策说明</h4>
                  <p className="text-sm text-muted-foreground leading-relaxed">
                    {selectedPolicy.description}
                  </p>
                </div>

                {/* Benefits */}
                <div>
                  <h4 className="text-sm font-semibold text-foreground mb-2">保障内容</h4>
                  <div className="space-y-2">
                    {selectedPolicy.benefits.map((benefit, i) => (
                      <div key={i} className="flex items-start gap-2">
                        <CheckCircle2 className="h-4 w-4 text-green-500 mt-0.5 shrink-0" />
                        <span className="text-sm text-muted-foreground">{benefit}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Requirements */}
                <div>
                  <h4 className="text-sm font-semibold text-foreground mb-2">申请条件</h4>
                  <div className="space-y-2">
                    {selectedPolicy.requirements.map((req, i) => (
                      <div key={i} className="flex items-start gap-2">
                        <div className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-cyan-100 text-xs font-medium text-cyan-600">
                          {i + 1}
                        </div>
                        <span className="text-sm text-muted-foreground">{req}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Deadline */}
                <div className="flex items-center gap-2 p-3 rounded-lg bg-gray-50">
                  <Clock className="h-4 w-4 text-muted-foreground" />
                  <span className="text-sm text-muted-foreground">
                    申请截止日期：<span className="font-medium text-foreground">{selectedPolicy.deadline}</span>
                  </span>
                </div>
              </div>

              <DialogFooter className="mt-4">
                <Button variant="outline" onClick={() => setSelectedPolicy(null)}>
                  关闭
                </Button>
                <Button className="gap-1.5">
                  <Star className="h-4 w-4" />
                  立即申请
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
