// 瓯医数链 - API 客户端层
// 优先调用后端 API，后端不可用时自动降级为模拟数据

import {
  mockCoverageSummary,
  mockHealthProfile,
  mockPreReviewResult,
  mockClaimsPreReview,
  mockPolicyMatch,
  mockSecurityOverview,
  mockChatResponses,
  type CoverageSummary,
  type HealthProfile,
  type HealthAlert,
  type TrendPoint,
  type OCRResult,
  type PreReviewResult,
  type PolicyMatch,
  type SecurityOverview,
  type EEGSession,
  type EEGHistory,
  type EEGRealtimeChunk,
  type EEGMentalState,
  type EEGPolicyLink,
  mockImagingStudy,
  mockImagingStudyTypes,
  mockImagingRecords,
  mockImagingPolicyLinks,
  type ImagingFindingItem,
  type ImagingReportData,
  type ImagingPolicyLink,
  type ImagingStudyResponse,
  type ImagingStudyTypeInfo,
  type ImagingRecordItem,
  type UserInfo,
} from "./mock-data";

// API 基址规则：
//  - 未设置（本地开发）→ 回退 http://localhost:8000
//  - 显式空字符串（魔搭同域部署）→ 相对路径 /api，由 Next rewrites 代理到后端
//  - 绝对 URL（Render/Vercel/docker-compose）→ 直接使用
const _rawApiBase = process.env.NEXT_PUBLIC_API_URL;
export const API_BASE =
  _rawApiBase === undefined || _rawApiBase === ""
    ? _rawApiBase === undefined
      ? "http://localhost:8000"
      : ""
    : _rawApiBase.trim().replace(/\/+$/, "");

// ==================== API 状态检测 ====================

let _apiReachable: boolean | null = null;

/** 检测后端 API 是否可达（超时 60s，兼容 Render 免费套餐冷启动） */
export async function getApiStatus(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/api/health`, {
      method: "GET",
      signal: AbortSignal.timeout(60000),
    });
    _apiReachable = res.ok;
  } catch {
    _apiReachable = false;
  }
  return _apiReachable;
}

/** 获取缓存的 API 状态（同步） */
export function getCachedApiStatus(): boolean | null {
  return _apiReachable;
}

// ==================== 通用请求封装 ====================

async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T | null> {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...options?.headers,
      },
      signal: options?.signal ?? AbortSignal.timeout(90000),
    });
    if (!res.ok) return null;
    // 防止 Render 冷启动返回 HTML 插页导致 JSON 解析失败
    const contentType = res.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

async function apiUpload<T>(
  path: string,
  file: File,
): Promise<T | null> {
  try {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      body: form,
      signal: AbortSignal.timeout(120000),
    });
    if (!res.ok) return null;
    const contentType = res.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

// ==================== 类型 ====================

export interface ChatRequest {
  message: string;
  user_id: string;
  conversation_id?: string;
  /** 最近对话历史（role+content），后端用于上下文连续性与指代消解 */
  history?: Array<{ role: string; content: string }>;
}

export interface ChatResponse {
  agent_type: string;
  response: string;
  data: Record<string, unknown>;
  suggestions: string[];
  conversation_id?: string;
  evidence?: Array<Record<string, unknown>>;
  user_profile?: { name: string; age: number; insurance_type: string; chronic_diseases: string[] } | null;
}

// ==================== API 函数 ====================

/** 发送聊天消息 */
export async function sendChatMessage(
  req: ChatRequest,
): Promise<ChatResponse> {
  const data = await apiFetch<ChatResponse>("/api/agents/chat", {
    method: "POST",
    body: JSON.stringify(req),
  });

  if (data) return data;

  // 降级：使用模拟数据
  const mock = mockChatResponses[req.message];
  return {
    agent_type: mock?.agent || "瓯医数链助手",
    response:
      mock?.content ||
      "收到您的问题，正在为您分析中...我会尽快给出详细解答。",
    data: {},
    suggestions: [],
  };
}

/** 获取权益全景 */
export async function getCoverageSummary(
  userId: string,
): Promise<CoverageSummary> {
  const data = await apiFetch<CoverageSummary & { payment_history_values?: number[] }>(
    `/api/coverage/${userId}`,
  );
  if (data) {
    // 后端真实返回的是对象数组，前端柱状图需要 number[]，做兼容转换
    const ph = data.payment_history as unknown;
    if (Array.isArray(ph) && ph.length > 0 && typeof ph[0] === "object") {
      data.payment_history = (ph as { personal_amount?: number }[]).map(
        (p) => p.personal_amount ?? 0,
      );
    } else if (data.payment_history_values && Array.isArray(data.payment_history_values)) {
      data.payment_history = data.payment_history_values as unknown as number[];
    }
    return data;
  }
  return mockCoverageSummary;
}

/** 获取健康画像 */
export async function getHealthProfile(
  userId: string,
): Promise<HealthProfile> {
  const data = await apiFetch<HealthProfile>(
    `/api/health/${userId}/profile`,
  );
  if (data) return data;
  return mockHealthProfile;
}

/** 获取健康预警 */
export async function getHealthAlerts(
  userId: string,
): Promise<HealthAlert[]> {
  const data = await apiFetch<{ alerts: HealthAlert[] }>(
    `/api/health/${userId}/alerts`,
  );
  if (data?.alerts) return data.alerts;
  return mockHealthProfile.alerts;
}

/** 获取健康趋势 */
export async function getHealthTrends(
  userId: string,
): Promise<TrendPoint[]> {
  const data = await apiFetch<{
    trends?: { monthly_costs: { month: string; amount: number }[] };
    health_trend?: TrendPoint[];
  }>(`/api/health/${userId}/trends`);
  // 优先用后端的 health_trend（真实健康评分趋势）
  if (data?.health_trend && data.health_trend.length > 0) {
    return data.health_trend.map((p) => ({
      month: (p.month || "").slice(5) + "月",
      score: p.score,
    }));
  }
  if (data?.trends?.monthly_costs) {
    return data.trends.monthly_costs.map((p, i) => ({
      month: p.month.slice(5) + "月",
      score: Math.max(50, Math.min(100, 80 - i * 2)),
    }));
  }
  return mockHealthProfile.trend_data;
}

/** 上传发票 OCR 识别 */
export async function uploadReceipt(file: File): Promise<OCRResult | null> {
  const data = await apiUpload<Record<string, unknown>>("/api/claims/ocr", file);
  if (!data) return null;
  // 兼容两种返回格式：后端现状直接返回 OCR 对象；{ ocr_result } 包裹格式作为旧契约兼容
  const wrapped = data.ocr_result as Record<string, unknown> | undefined;
  const r = (wrapped ?? data) as Record<string, unknown>;
  const items = ((r.items as { name: string; amount: number }[]) || []).map((it) => ({
    name: it.name,
    price: Number(it.amount) || 0,
  }));
  // 识别结果无实质内容时返回 null，由调用方决定兜底策略
  if (!r.hospital && items.length === 0 && !r.total_amount) return null;
  return {
    hospital: (r.hospital as string) || "",
    date: (r.date as string) || (r.visit_date as string) || "",
    patient: (r.patient_name as string) || "",
    department: (r.department as string) || (r.diagnosis as string) || "",
    visit_type: (r.visit_type as string) || "",
    items,
    total: Number(r.total_amount) || 0,
    confidence: Number(wrapped ? data.confidence : r.confidence) || 0,
  };
}

/** 报销预审 */
export async function preReviewClaim(
  claimData: Record<string, unknown>,
): Promise<PreReviewResult> {
  const data = await apiFetch<PreReviewResult>("/api/claims/pre-review", {
    method: "POST",
    body: JSON.stringify(claimData),
  });
  if (data) return data;
  return mockPreReviewResult;
}

/** 上传医疗资料给档案管家（图片视觉转录/OCR、PDF 文本层 → 归档回复） */
export interface BodyUploadResult {
  document_id: number | string;
  doc_kind: string;
  filename: string;
  records_added: number;
  agent_response: string;
  disclaimer?: string;
}

export async function uploadBodyDocument(
  userId: string,
  file: File,
): Promise<BodyUploadResult | null> {
  try {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${API_BASE}/api/body/${userId}/upload`, {
      method: "POST",
      body: form,
      // 后端 45s 处理超时 + OCR/视觉转录耗时，预留 120s
      signal: AbortSignal.timeout(120000),
    });
    if (!res.ok) return null;
    const contentType = res.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) return null;
    return (await res.json()) as BodyUploadResult;
  } catch {
    return null;
  }
}

// ==================== 药品卫士（药品拍照识别） ====================

export interface DrugInfo {
  generic_name: string;
  brand_name: string;
  spec: string;
  dosage_form: string;
  manufacturer: string;
  approval_number: string;
  batch_number: string;
  production_date: string;
  expiry_date: string;
  otc_or_rx: string;
  confidence: number;
  notes: string;
}

export interface DrugInteractionWarning {
  level?: string;
  severity?: string;
  icon?: string;
  title?: string;
  description?: string;
  suggestion?: string;
}

export interface DrugScanResult {
  not_a_drug: boolean;
  detected?: string;
  drug?: DrugInfo;
  category?: string;
  expiry?: { status: string; message: string };
  interactions?: DrugInteractionWarning[];
  source: string;
  confirm_prompt?: string;
  chat_response?: string;
}

/** 拍照识别药品（只读，不写库） */
export async function scanDrug(userId: string, file: File): Promise<DrugScanResult | null> {
  try {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(
      `${API_BASE}/api/drugs/scan?user_id=${encodeURIComponent(userId)}`,
      {
        method: "POST",
        body: form,
        // 后端 45s 识别超时 + 视觉模型耗时，预留 120s
        signal: AbortSignal.timeout(120000),
      },
    );
    if (!res.ok) return null;
    const contentType = res.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) return null;
    return (await res.json()) as DrugScanResult;
  } catch {
    return null;
  }
}

/** 用户确认后，把扫描到的药品登记到用药记录 */
export async function registerDrug(
  userId: string,
  drug: DrugInfo,
  category?: string,
): Promise<{ registered: boolean; message: string } | null> {
  return apiFetch<{ registered: boolean; message: string }>("/api/drugs/register", {
    method: "POST",
    body: JSON.stringify({ user_id: userId, drug, category: category || null }),
  });
}

/** 获取政策匹配 */
export async function getPolicyMatches(
  userId: string,
): Promise<PolicyMatch> {
  const data = await apiFetch<PolicyMatch>(
    `/api/policy/match/${userId}`,
  );
  if (data) return data;
  return mockPolicyMatch;
}

/** 搜索政策 */
export async function searchPolicies(query: string): Promise<PolicyMatch> {
  const data = await apiFetch<PolicyMatch>("/api/policy/search", {
    method: "POST",
    body: JSON.stringify({ keyword: query }),
  });
  if (data) return data;
  // 降级：在模拟数据中过滤
  const filtered = mockPolicyMatch.policies.filter(
    (p) =>
      p.title.includes(query) ||
      p.category.includes(query) ||
      p.matchReason.includes(query),
  );
  return { ...mockPolicyMatch, policies: filtered };
}

/** 获取数据授权总览 */
export async function getSecurityOverview(
  userId: string,
): Promise<SecurityOverview> {
  const [authData, auditData] = await Promise.all([
    apiFetch<{ authorizations: { data_type: string; authorized_agent: string; is_active: boolean; expires_at: string }[] }>(
      `/api/security/authorizations/${userId}`,
    ),
    apiFetch<{ logs: { action: string; agent: string; data_type: string; timestamp: string; detail: string }[] }>(
      `/api/security/audit-log/${userId}`,
    ),
  ]);

  if (authData || auditData) {
    // 将后端数据映射到前端结构
    const overview = { ...mockSecurityOverview };

    if (authData?.authorizations) {
      overview.active_authorizations = authData.authorizations.filter(
        (a) => a.is_active,
      ).length;
    }

    if (auditData?.logs) {
      overview.audit_log = auditData.logs.map((log) => ({
        time: log.timestamp.replace("T", " ").slice(0, 16),
        agent: log.agent,
        action: log.detail || log.action,
        dataType: log.data_type,
        status: "allowed" as const,
      }));
      overview.today_accesses = overview.audit_log.length;
    }

    return overview;
  }

  return mockSecurityOverview;
}

/** 更新授权 */
export async function updateAuthorization(
  authData: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const data = await apiFetch<Record<string, unknown>>(
    "/api/security/authorize",
    {
      method: "POST",
      body: JSON.stringify(authData),
    },
  );
  if (data) return data;
  // 降级：返回模拟成功响应
  return { success: true, message: "授权更新成功（模拟）" };
}

// ==================== P2 新增端点 ====================

export interface ProactiveAlert {
  level: "high" | "medium" | "low";
  icon: string;
  title: string;
  description?: string;
  desc?: string;
  suggestion?: string;
  action?: string;
  evidence?: Array<Record<string, unknown>>;
  timestamp?: string;
}

/** 主动健康预警（用户登录触发，体现主动式服务） */
export async function getProactiveAlerts(
  userId: string,
): Promise<ProactiveAlert[]> {
  const data = await apiFetch<{ alerts: ProactiveAlert[]; alert_count: number }>(
    `/api/health/${userId}/proactive-alerts`,
  );
  if (data?.alerts) return data.alerts;
  return [];
}

/** 复合意图对话（多 Agent 协作） */
export async function sendComplexChat(
  req: ChatRequest,
): Promise<ChatResponse & { agents_invoked?: string[]; multi_agent?: boolean; intent_weights?: Array<{ intent: string; weight: number }> }> {
  const data = await apiFetch<
    ChatResponse & { agents_invoked?: string[]; multi_agent?: boolean; intent_weights?: Array<{ intent: string; weight: number }> }
  >("/api/agents/complex-chat", {
    method: "POST",
    body: JSON.stringify(req),
    // 多智能体并行 + 融合在线模式耗时较长（后端总预算 220s），预留 240s 中断时限
    signal: AbortSignal.timeout(240000),
  });
  if (data) return data;
  // 降级到普通 chat
  return sendChatMessage(req);
}

/** 报销流程联合预审：上传完成后 编排智能体 调度 档案管家×报销助手 解读已上传资料 */
export interface PrereviewUploadedResult {
  response: string;
  agents_invoked: string[];
  multi_agent: boolean;
  documents: Array<{ filename: string; archive_kind: string; claim_kind: string; amount: number | null }>;
  total_amount: number | null;
  completeness: Array<{ name: string; status: string }>;
  estimate: Record<string, unknown> | null;
}

export async function prereviewUploaded(userId: string): Promise<PrereviewUploadedResult | null> {
  return apiFetch<PrereviewUploadedResult>(
    `/api/claims/prereview-uploaded?user_id=${encodeURIComponent(userId)}`,
    { method: "POST" },
  );
}

/** 数据安全：获取所有用户（用户切换器用） */
export async function getUsers(): Promise<Array<{ id: number; name: string; age: number; gender: string; city: string; insurance_type: string; employee_status: string }>> {
  const data = await apiFetch<{ users: Array<Record<string, unknown>> }>("/api/users");
  if (data?.users) {
    return data.users as never;
  }
  return [];
}

export type CreateUserRequest = Omit<UserInfo, "id" | "conditions">;

/** 新增用户；成功后可立即用于聊天与数字人体档案。 */
export async function createUser(req: CreateUserRequest): Promise<UserInfo | null> {
  return apiFetch<UserInfo>("/api/users", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

/** 可信数据空间数据流转记录（P2-2 可视化用） */
export async function getDataFlow(userId: string): Promise<{
  user_name: string;
  total_flows: number;
  flows: Array<{
    id: string;
    data_type: string;
    agent: string;
    steps: Array<{ step: string; actor: string; status: string; detail: string; ts: string }>;
  }>;
  principle: string;
} | null> {
  return apiFetch(`/api/security/data-flow/${userId}`);
}

// ==================== EEG 脑电健康（BCI×医保创新） ====================

/** 获取支持的心理状态列表 */
export async function getEEGMentalStates(): Promise<EEGMentalState[]> {
  const data = await apiFetch<{ states: EEGMentalState[] }>(`/api/eeg/states`);
  if (data?.states) return data.states;
  return [
    { key: "relaxed", label: "放松", stress: 20, attention: 50, sleep: 85, cognitive: 30 },
    { key: "focused", label: "专注", stress: 40, attention: 88, sleep: 65, cognitive: 75 },
    { key: "stressed", label: "高压力", stress: 85, attention: 60, sleep: 45, cognitive: 80 },
    { key: "fatigued", label: "疲劳", stress: 50, attention: 30, sleep: 40, cognitive: 35 },
    { key: "sleep_deprived", label: "睡眠不足", stress: 60, attention: 35, sleep: 25, cognitive: 40 },
  ];
}

/** 发起一次 EEG 采集会话 */
export async function createEEGSession(
  userId: string,
  mentalState: string = "auto",
  durationSeconds: number = 4,
): Promise<EEGSession | null> {
  const data = await apiFetch<EEGSession>(
    `/api/eeg/${userId}/session?mental_state=${encodeURIComponent(mentalState)}&duration_seconds=${durationSeconds}`,
    { method: "POST" },
  );
  return data;
}

/** 获取最近一次 EEG 评估 */
export async function getLatestEEG(userId: string): Promise<EEGSession | null> {
  const data = await apiFetch<EEGSession>(`/api/eeg/${userId}/latest`);
  return data;
}

/** 获取 EEG 历史趋势 */
export async function getEEGHistory(userId: string, limit: number = 20): Promise<EEGHistory | null> {
  const data = await apiFetch<EEGHistory>(`/api/eeg/${userId}/history?limit=${limit}`);
  return data;
}

/** 获取实时 EEG 数据块（前端轮询模拟实时采集） */
export async function getEEGRealtime(
  userId: string,
  mentalState: string = "relaxed",
  seed: number = 0,
): Promise<EEGRealtimeChunk | null> {
  const data = await apiFetch<EEGRealtimeChunk>(
    `/api/eeg/${userId}/realtime?mental_state=${encodeURIComponent(mentalState)}&seed=${seed}`,
  );
  return data;
}

/** 获取脑电异常 → 医保政策联动推荐 */
export async function getEEGPolicyLinks(
  userId: string,
): Promise<{ policy_links: EEGPolicyLink[]; summary: string; mental_state_label: string } | null> {
  const data = await apiFetch<{ policy_links: EEGPolicyLink[]; summary: string; mental_state_label: string }>(
    `/api/eeg/${userId}/policy-links`,
  );
  return data;
}

// ---- 真实 EEG 设备接入（LSL / 文件导入） ----

/** LSL EEG 设备连接状态 */
export interface EEGDeviceStatus {
  connected: boolean;
  stream_count: number;
  streams: Array<{
    name: string;
    type: string;
    channel_count: number;
    nominal_srate: number;
    source_id?: string;
  }>;
  pylsl_installed: boolean;
  message: string;
}

/** 检查 LSL EEG 设备连接状态 */
export async function checkEEGDevice(): Promise<EEGDeviceStatus | null> {
  return apiFetch<EEGDeviceStatus>(`/api/eeg/device/check`);
}

/** 从真实 EEG 设备（LSL 流）发起采集会话 */
export async function createEEGSessionFromDevice(
  userId: string,
  durationSeconds: number = 4,
  mentalState: string = "auto",
): Promise<EEGSession | null> {
  const data = await apiFetch<EEGSession>(
    `/api/eeg/${userId}/session-device?duration_seconds=${durationSeconds}&mental_state=${encodeURIComponent(mentalState)}`,
    { method: "POST" },
  );
  return data;
}

/** 导入 EEG 文件（CSV/EDF/TXT）并分析 */
export async function importEEGFile(
  userId: string,
  file: File,
  sampleRate: number = 256,
  mentalState: string = "auto",
): Promise<EEGSession | null> {
  try {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(
      `${API_BASE}/api/eeg/${userId}/import?sample_rate=${sampleRate}&mental_state=${encodeURIComponent(mentalState)}`,
      {
        method: "POST",
        body: form,
        signal: AbortSignal.timeout(120000),
      },
    );
    if (!res.ok) return null;
    const contentType = res.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) return null;
    return (await res.json()) as EEGSession;
  } catch {
    return null;
  }
}

// ==================== 医学影像 AI 标注（影像卫士） ====================

export interface ImagingAnnotation {
  action: "confirm" | "reject" | "add" | "update";
  index?: number;
  finding_type: string;
  x: number;
  y: number;
  w: number;
  h: number;
  confidence: number;
  severity: string;
  evidence?: string;
}

export interface ImagingReviewResult {
  record_id: number;
  final_findings: ImagingFindingItem[];
  report: ImagingReportData;
  policy_links: ImagingPolicyLink[];
}

/** 获取支持的影像检查类型与病灶类别 */
export async function getImagingStudyTypes(): Promise<Record<string, ImagingStudyTypeInfo> | null> {
  const data = await apiFetch<{ study_types: Record<string, ImagingStudyTypeInfo> }>(
    "/api/imaging/study-types",
  );
  if (data?.study_types) return data.study_types;
  return mockImagingStudyTypes;
}

/** 发起一次影像 AI 分析（合成影像 → 病灶检测 → AI 预标注，可选视觉大模型解读） */
export async function analyzeImaging(
  userId: string,
  studyType: string,
  findingsKeys?: string[],
  seed?: number,
  withVision?: boolean,
): Promise<ImagingStudyResponse | null> {
  const data = await apiFetch<ImagingStudyResponse>(`/api/imaging/${userId}/analyze`, {
    method: "POST",
    body: JSON.stringify({
      study_type: studyType,
      findings_keys: findingsKeys,
      seed,
      with_vision: withVision ?? false,
    }),
  });
  if (data) return data;
  // 降级：返回模拟影像（仅当类型匹配时；否则按类型生成简化版）
  if (studyType !== mockImagingStudy.study_type) {
    return {
      ...mockImagingStudy,
      study_type: studyType,
      study_label: mockImagingStudyTypes[studyType]?.label || studyType,
    };
  }
  return mockImagingStudy;
}

/** 医生复核：提交 AI 标注确认/驳回/新增/修正 */
export async function reviewImaging(
  userId: string,
  recordId: number,
  annotations: ImagingAnnotation[],
): Promise<ImagingReviewResult | null> {
  const data = await apiFetch<ImagingReviewResult>(
    `/api/imaging/${userId}/records/${recordId}/review`,
    {
      method: "POST",
      body: JSON.stringify({ annotations }),
    },
  );
  if (data) return data;
  // 降级：基于本地 findings 计算最终结果
  const base = mockImagingStudy;
  const ops = new Map<number, ImagingAnnotation>();
  annotations.forEach((a, i) => ops.set(i, a));
  const finalFindings: ImagingFindingItem[] = [];
  base.findings.forEach((f, i) => {
    if (!ops.has(i) || ops.get(i)!.action === "confirm") {
      finalFindings.push({ ...f, status: "confirmed" });
    }
  });
  for (const a of annotations.filter((x) => x.action === "add")) {
    finalFindings.push({
      finding_type: a.finding_type,
      label: a.finding_type,
      x: a.x,
      y: a.y,
      w: a.w,
      h: a.h,
      confidence: a.confidence,
      severity: (a.severity as "low" | "medium" | "high") || "medium",
      source: "doctor",
      status: "confirmed",
      evidence: a.evidence,
    });
  }
  return {
    record_id: recordId,
    final_findings: finalFindings,
    report: {
      conclusion: `医师复核完成，共确认 ${finalFindings.length} 处发现。`,
      risk_level: finalFindings.some((f) => f.severity === "high") ? "高风险" : "中风险",
      advice: ["请结合临床资料综合评估", "必要时完善进一步检查"],
      confirmed_count: finalFindings.length,
      pending_count: 0,
      rejected_count: annotations.filter((a) => a.action === "reject").length,
      generated_at: new Date().toISOString(),
      disclaimer: base.disclaimer,
    },
    policy_links: mockImagingPolicyLinks,
  };
}

/** 获取用户影像检查历史 */
export async function getImagingRecords(
  userId: string,
  limit: number = 10,
): Promise<ImagingRecordItem[] | null> {
  const data = await apiFetch<{ records: ImagingRecordItem[] }>(
    `/api/imaging/${userId}/records?limit=${limit}`,
  );
  if (data?.records) return data.records;
  return mockImagingRecords;
}

// ==================== 真实公开数据集（科研验证） ====================

/** 真实 EEG 数据集记录概览（PhysioNet eegmmidb 等） */
export interface RealEEGSessionItem {
  record_id: string;
  source: string;
  mental_state: string;
  mental_state_label: string;
  channels: string[];
  origin_sample_rate: number;
  duration_seconds: number;
  metrics: {
    stress_index?: number;
    attention_index?: number;
    sleep_quality?: number;
    cognitive_load?: number;
    cerebrovascular_risk?: number;
    cognitive_decline_risk?: number;
    [k: string]: unknown;
  } | null;
  alerts_count: number;
  dataset_meta: {
    subject?: string;
    run?: number;
    paradigm?: string;
    license?: string;
    synthetic?: boolean;
    expected_state?: string;
  } | null;
  origin_file: string;
}

export interface RealEEGListResponse {
  total: number;
  returned: number;
  datasets: Record<string, { count: number; updated_at?: string }>;
  sessions: RealEEGSessionItem[];
  note: string;
}

/** 真实 EEG 单条详情（含频段功率/预警/政策联动） */
export interface RealEEGDetail extends RealEEGSessionItem {
  session_id: string;
  band_powers: Record<string, Record<string, number>>;
  avg_band_powers: Record<string, number>;
  alerts: Array<{ title?: string; desc?: string; description?: string; suggestion?: string; level?: string }>;
  policy_links: EEGPolicyLink[];
  summary?: string;
  origin_channels?: string[];
}

/** 获取真实公开 EEG 数据集评估列表 */
export async function getRealEEGSessions(
  source?: string,
  limit = 20,
): Promise<RealEEGListResponse | null> {
  const q = source ? `?source=${encodeURIComponent(source)}&limit=${limit}` : `?limit=${limit}`;
  return apiFetch<RealEEGListResponse>(`/api/eeg/real/list${q}`);
}

/** 获取单条真实 EEG 评估详情 */
export async function getRealEEGDetail(recordId: string): Promise<RealEEGDetail | null> {
  return apiFetch<RealEEGDetail>(`/api/eeg/real/${encodeURIComponent(recordId)}`);
}

/** 真实影像数据集研究概览（Montgomery/Shenzhen/demo） */
export interface RealImagingStudyItem {
  study_id: string;
  study_type: string;
  study_label: string;
  source: string;
  origin_file: string;
  detected_count: number;
  gt_count: number;
  metrics: Record<string, number> | null;
}

export interface RealImagingListResponse {
  total: number;
  returned: number;
  datasets: Record<string, { count: number; updated_at?: string }>;
  studies: RealImagingStudyItem[];
  note: string;
}

/** 真实影像单条详情（含 base64 影像 + AI 检测 + GT + 政策联动） */
export interface RealImagingDetail {
  study_id: string;
  study_type: string;
  study_label: string;
  source: string;
  origin_file: string;
  origin_shape?: [number, number];
  image_base64: string;
  detected_findings: Array<{
    finding_type: string;
    x: number;
    y: number;
    w: number;
    h: number;
    confidence: number;
    severity: string;
    evidence?: string;
  }>;
  gt_findings: Array<{ finding_type: string; x: number; y: number; w: number; h: number }> | null;
  metrics: Record<string, number> | null;
  policy_links: ImagingPolicyLink[];
  vision_interpretation?: string | null;
  vision_available?: boolean;
  disclaimer: string;
}

/** 获取真实公开影像数据集列表 */
export async function getRealImagingStudies(
  studyType?: string,
  source?: string,
  limit = 20,
): Promise<RealImagingListResponse | null> {
  const params: string[] = [`limit=${limit}`];
  if (studyType) params.push(`study_type=${encodeURIComponent(studyType)}`);
  if (source) params.push(`source=${encodeURIComponent(source)}`);
  return apiFetch<RealImagingListResponse>(`/api/imaging/real/list?${params.join("&")}`);
}

/** 获取单条真实影像详情（可选视觉大模型解读） */
export async function getRealImagingDetail(
  studyId: string,
  withVision?: boolean,
): Promise<RealImagingDetail | null> {
  const q = withVision ? "?with_vision=true" : "";
  return apiFetch<RealImagingDetail>(`/api/imaging/real/${encodeURIComponent(studyId)}${q}`);
}

// ==================== 瓯医数链 · 联邦学习协作 ====================

export interface FederationHospital {
  site: string;
  total: number;
  train: number;
  test: number;
  prevalence: number;
  mean_age: number;
  missing_ef: number;
}

export interface FederationOverview {
  task: string;
  features: string[];
  n_features: number;
  hospitals: FederationHospital[];
  global_test_n: number;
}

export interface FederationJobResult {
  rounds: number;
  local_epochs: number;
  dp_sigma: number;
  clip_norm: number;
  auc_curve: number[];
  final_auc: number;
  per_site: Record<string, { local: number; federated: number }>;
}

export interface FederationJobSummary {
  id: string;
  task: string;
  rounds: number;
  local_epochs: number;
  dp_sigma: number;
  status: string;
  duration_ms: number | null;
  prev_hash: string | null;
  event_hash: string | null;
  created_at: string | null;
}

export interface FederationJobDetail extends FederationJobSummary {
  result: FederationJobResult;
}

export interface FederationBenchmark {
  local_auc: Record<string, number>;
  fed_auc: number;
  fed_curve: number[];
  per_site: Record<string, { local: number; federated: number }>;
  dp: Record<string, { auc: number; label: string }>;
  pooled_oracle_auc: number;
  dataset_info: FederationOverview;
}

/** 医院数据全景（联邦统计口径） */
export async function getFederationOverview(): Promise<FederationOverview | null> {
  return apiFetch<FederationOverview>("/api/federation/overview");
}

/** 发起联邦训练任务（同步执行，秒级） */
export async function createFederationJob(params: {
  rounds: number;
  local_epochs: number;
  dp_sigma: number;
}): Promise<FederationJobDetail | null> {
  return apiFetch<FederationJobDetail>("/api/federation/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
}

/** 最近联邦任务列表（含存证哈希） */
export async function listFederationJobs(limit = 20): Promise<FederationJobSummary[] | null> {
  const data = await apiFetch<{ 0?: FederationJobSummary } & FederationJobSummary[]>(
    `/api/federation/jobs?limit=${limit}`
  );
  return data as FederationJobSummary[] | null;
}

/** 标准基准实验（首次约1分钟，后端缓存） */
export async function getFederationBenchmark(force = false): Promise<FederationBenchmark | null> {
  return apiFetch<FederationBenchmark>(`/api/federation/benchmark?force=${force}`);
}

// ==================== 瓯医数链 · AI 病历治理 ====================

export interface DeidEntity {
  start: number;
  end: number;
  masked: string;
}

export interface GovernResult {
  deid: {
    masked_text: string;
    entities: DeidEntity[];
    entity_count: number;
  };
  structured: {
    extractor: string;
    patient: { age: number | null; sex: string | null };
    chief_complaint: string | null;
    diagnoses: string[];
    vitals: { bp: string | null; heart_rate: number | null; temperature: number | null };
    medications: Array<{ name: string; dose: string }>;
    history: string[];
  };
  pipeline: string[];
  compliance: string;
}

/** 病历治理流水线：PHI脱敏 + 结构化（本地LLM优先，规则兜底） */
export async function governNote(text: string, useLlm = true): Promise<GovernResult | null> {
  return apiFetch<GovernResult>("/api/governance/govern", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, use_llm: useLlm }),
  });
}

// ==================== 瓯医数链 · 数据要素市场 ====================

export interface DataProductItem {
  id: string;
  name: string;
  provider: string;
  data_type: string;
  description: string;
  sample_count: number;
  price: number;
  price_unit: string;
  privacy_tech: string;
  status: string;
}

export interface DataTransactionItem {
  id: string;
  product_name: string;
  buyer: string;
  amount: number;
  status: string;
  revenue: { provider: number; platform: number; contributor: number };
  purpose: string;
  prev_hash: string | null;
  event_hash: string | null;
  created_at: string | null;
}

export interface RegulatoryView {
  total_transactions: number;
  total_amount: number;
  revenue: { provider: number; platform: number; contributor: number };
  product_count: number;
  products_by_type: Record<string, number>;
  recent_transactions: DataTransactionItem[];
  compliance: { privacy_incidents: number; chain_verified: boolean; note: string };
}

export async function listDataProducts(): Promise<DataProductItem[] | null> {
  return apiFetch<DataProductItem[]>("/api/marketplace/products");
}

export async function purchaseDataProduct(params: {
  product_id: string;
  buyer: string;
  purpose: string;
}): Promise<DataTransactionItem | null> {
  return apiFetch<DataTransactionItem>("/api/marketplace/purchase", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
}

export async function listDataTransactions(limit = 20): Promise<DataTransactionItem[] | null> {
  return apiFetch<DataTransactionItem[]>(`/api/marketplace/transactions?limit=${limit}`);
}

export async function getRegulatoryView(): Promise<RegulatoryView | null> {
  return apiFetch<RegulatoryView>("/api/marketplace/regulatory");
}

// ==================== 泛癌卫士（Oncoformer 泛癌预测） ====================

import type {
  CancerReport,
  CancerStatus,
  CancerCohortPatient,
  CancerCohortDetail,
} from "@/lib/mock-data";
import {
  mockCancerStatus,
  mockCancerReport,
  mockCancerCohort,
  mockCancerCohortDetail,
} from "@/lib/mock-data";

/** 泛癌卫士服务形态（真模型 oncoformer / 预计算 precomputed） */
export async function getCancerStatus(): Promise<CancerStatus | null> {
  const data = await apiFetch<CancerStatus>("/api/cancer/status");
  return data ?? mockCancerStatus;
}

/** 对当前用户做泛癌风险预测（真模型不可用时后端返回队列基线） */
export async function predictCancer(userId: string): Promise<CancerReport | null> {
  const data = await apiFetch<CancerReport>(`/api/cancer/${userId}/predict`, {
    method: "POST",
    body: JSON.stringify({ mode: "ehr_only" }),
  });
  return data ?? { ...mockCancerReport, record_id: 0 };
}

/** 泛癌预测历史 */
export async function getCancerHistory(userId: string, limit = 10) {
  const data = await apiFetch<Array<Record<string, unknown>>>(
    `/api/cancer/records/${userId}?limit=${limit}`,
  );
  return data ?? [];
}

/** COMPASS 示例队列列表（每个模式只含 top3 风险预览） */
export async function getCancerCohort(): Promise<{
  patients: CancerCohortPatient[];
  population: CancerStatus["population"];
} | null> {
  const data = await apiFetch<{
    patients: CancerCohortPatient[];
    population: CancerStatus["population"];
  }>("/api/cancer/cohort/patients");
  return data ?? mockCancerCohort;
}

/** 队列患者多模态预测详情（本地真模型实时 / 云端预计算） */
export async function predictCohortPatient(
  pid: string,
  modes = "fused,ehr_only,img_only",
): Promise<CancerCohortDetail | null> {
  const data = await apiFetch<CancerCohortDetail>(
    `/api/cancer/cohort/${encodeURIComponent(pid)}/predict?modes=${encodeURIComponent(modes)}`,
    { method: "POST" },
  );
  return data ?? { ...mockCancerCohortDetail, pid };
}
