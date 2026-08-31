// 医保智脑 - 模拟数据（后端不可用时作为降级数据源）

// ==================== 类型定义 ====================

export interface UserInfo {
  id: number;
  name: string;
  age: number;
  gender: string;
  city: string;
  insurance_type: string;
  employee_status: string;
  conditions: string[];
  /** 邮箱注册用户才有，演示用户为空 */
  email?: string | null;
}

export interface PaymentRecord {
  year: number;
  month: number;
  personal_amount: number;
  company_amount: number;
}

export interface Activity {
  date: string;
  type: string;
  desc: string;
  amount: string;
}

export interface CoverageSummary {
  user: UserInfo;
  payment_years: string;
  account_balance: number;
  outpatient_ratio: number;
  inpatient_ratio: number;
  payment_history: number[];
  recent_activities: Activity[];
}

export interface RadarDimension {
  name: string;
  value: number;
  target: number;
}

export interface HealthAlert {
  severity: "high" | "medium" | "low";
  icon: string;
  title: string;
  desc: string;
  action: string;
}

export interface Medication {
  name: string;
  dosage: string;
  frequency: string;
  status: string;
  statusColor: string;
}

export interface TrendPoint {
  month: string;
  score: number;
}

export interface HealthSuggestion {
  icon: string;
  title: string;
  desc: string;
  description?: string;
  color: string;
}

export interface HealthProfile {
  health_score: number;
  score_label: string;
  radar_data: RadarDimension[];
  alerts: HealthAlert[];
  medications: Medication[];
  trend_data: TrendPoint[];
  suggestions: HealthSuggestion[];
}

export interface OCRItem {
  name: string;
  price: number;
}

export interface OCRResult {
  hospital: string;
  date: string;
  patient: string;
  department: string;
  visit_type?: string;
  items: OCRItem[];
  total: number;
  confidence: number;
}

export interface PreReviewResult {
  review_result: string;
  reimbursable: boolean;
  estimated_reimbursement: number;
  reimbursement_rate: number;
  issues: string[];
  suggestions: string[];
}

export interface DocumentCheck {
  name: string;
  status: "uploaded" | "missing";
}

export interface ClaimStatus {
  date: string;
  title: string;
  desc: string;
  status: "completed" | "pending";
}

export interface ClaimsPreReview {
  ocr_result: OCRResult;
  pre_review: PreReviewResult;
  required_docs: DocumentCheck[];
  claim_status: ClaimStatus[];
}

export interface MatchedPolicy {
  id: string;
  title: string;
  savings: string;
  savingsAmount: number;
  matchReason: string;
  matchScore: number;
  category: string;
  description: string;
  requirements: string[];
  benefits: string[];
  deadline: string;
}

export interface PolicyMatch {
  total_savings: number;
  policies: MatchedPolicy[];
}

export interface AuthEntry {
  enabled: boolean;
  expiry: string;
}

export interface DataType {
  id: string;
  name: string;
  icon: string;
  desc: string;
}

export interface Agent {
  id: string;
  name: string;
  color: string;
}

export interface AuthMatrix {
  data_type: string;
  agent: string;
  enabled: boolean;
  expiry: string;
}

export interface DataRight {
  icon: string;
  title: string;
  desc: string;
  color: string;
}

export interface ActiveAuth {
  agent_name: string;
  data_type_name: string;
  data_type_icon: string;
  expiry: string;
}

export interface AuditEntry {
  time: string;
  agent: string;
  action: string;
  dataType: string;
  status: "allowed" | "denied";
}

export interface SecurityOverview {
  active_authorizations: number;
  anomalies: number;
  today_accesses: number;
  data_types: DataType[];
  agents: Agent[];
  authorization_matrix: AuthMatrix[];
  rights: DataRight[];
  active_auths: ActiveAuth[];
  audit_log: AuditEntry[];
}

// ==================== EEG 脑电健康类型（BCI×医保创新） ====================

export interface EEGWaveformPoint {
  i: number;
  v: number;
}

export interface EEGChannelWaveform {
  channel: string;
  data: EEGWaveformPoint[];
}

export interface EEGMetrics {
  stress_index: number;
  attention_index: number;
  sleep_quality: number;
  cognitive_load: number;
  emotion: {
    valence: number;
    arousal: number;
    label: string;
  };
  ratios?: {
    alpha_beta: number;
    theta_beta: number;
    slow_wave_ratio: number;
    fast_wave_ratio: number;
    theta_alpha?: number;
    delta_ratio?: number;
  };
  // ⭐ 赛道7核心新增指标
  cerebrovascular_risk?: number;
  cognitive_decline_risk?: number;
  mental_health?: {
    anxiety_score: number;
    depression_score: number;
    overall_risk: number;
    screening_label: string;
  };
}

export interface EEGAlert {
  level: "high" | "medium" | "low";
  severity?: string;
  icon: string;
  title: string;
  description?: string;
  desc?: string;
  suggestion?: string;
  action?: string;
  timestamp?: string;
  evidence?: Array<Record<string, unknown>>;
}

export interface EEGPolicyLink {
  trigger: string;
  title: string;
  policy_hint: string;
  description: string;
  suggestion: string;
  related_policies: string[];
  evidence?: Array<Record<string, unknown>>;
}

export interface EEGSession {
  session_id: string;
  user_id: string;
  timestamp: string;
  duration_seconds: number;
  channels: string[];
  sample_rate: number;
  mental_state: string;
  mental_state_label: string;
  band_powers: Record<string, Record<string, number>>;
  avg_band_powers: Record<string, number>;
  metrics: EEGMetrics;
  alerts: EEGAlert[];
  policy_links: EEGPolicyLink[];
  summary: string;
  waveform: EEGChannelWaveform[];
  from_history?: boolean;
}

export interface EEGHistoryItem {
  id: number;
  user_id: number;
  session_id: string;
  recorded_at: string;
  duration_seconds: number;
  mental_state: string;
  mental_state_label: string;
  avg_band_powers: Record<string, number>;
  metrics: EEGMetrics;
  alert_count: number;
  policy_link_count: number;
  summary: string;
}

export interface EEGTrendPoint {
  timestamp: string;
  mental_state: string;
  mental_state_label: string;
  stress_index: number;
  attention_index: number;
  sleep_quality: number;
  cognitive_load: number;
}

export interface EEGHistory {
  user_id: string;
  user_name: string;
  total_sessions: number;
  history: EEGHistoryItem[];
  trend: EEGTrendPoint[];
}

export interface EEGRealtimeChunk {
  channel: string;
  waveform: EEGWaveformPoint[];
  band_powers: Record<string, number>;
  metrics_snapshot: {
    stress_index: number;
    attention_index: number;
  };
  timestamp: string;
}

export interface EEGMentalState {
  key: string;
  label: string;
  stress: number;
  attention: number;
  sleep: number;
  cognitive: number;
}

// ==================== 用户数据 ====================

export const mockUsers: UserInfo[] = [
  { id: 1, name: "张阿姨", age: 58, gender: "女", city: "南京", insurance_type: "职工医保", employee_status: "退休", conditions: ["糖尿病", "高血压"] },
  { id: 2, name: "李大爷", age: 72, gender: "男", city: "苏州", insurance_type: "居民医保", employee_status: "退休", conditions: ["冠心病"] },
  { id: 3, name: "王先生", age: 35, gender: "男", city: "南京", insurance_type: "职工医保", employee_status: "在职", conditions: [] },
  { id: 4, name: "赵女士", age: 42, gender: "女", city: "无锡", insurance_type: "职工医保", employee_status: "在职", conditions: ["甲状腺结节"] },
  { id: 5, name: "陈同学", age: 22, gender: "男", city: "南京", insurance_type: "居民医保", employee_status: "学生", conditions: [] },
  { id: 6, name: "刘阿姨", age: 65, gender: "女", city: "常州", insurance_type: "职工医保", employee_status: "退休", conditions: ["糖尿病", "骨质疏松"] },
  { id: 7, name: "周先生", age: 50, gender: "男", city: "南京", insurance_type: "职工医保", employee_status: "在职", conditions: ["胃病"] },
  { id: 8, name: "吴女士", age: 28, gender: "女", city: "苏州", insurance_type: "职工医保", employee_status: "在职", conditions: [] },
  { id: 9, name: "孙大爷", age: 78, gender: "男", city: "南通", insurance_type: "居民医保", employee_status: "退休", conditions: ["高血压", "关节炎", "白内障"] },
  { id: 10, name: "郑先生", age: 45, gender: "男", city: "南京", insurance_type: "灵活就业医保", employee_status: "灵活就业", conditions: ["腰椎间盘突出"] },
];

// ==================== 权益全景 ====================

export const mockCoverageSummary: CoverageSummary = {
  user: { id: 1, name: "张明", age: 45, gender: "男", city: "南京市", insurance_type: "职工医保", employee_status: "在职", conditions: ["糖尿病", "高血压"] },
  payment_years: "15年3个月",
  account_balance: 8562.30,
  outpatient_ratio: 0.85,
  inpatient_ratio: 0.90,
  payment_history: [320, 280, 350, 310, 290, 340, 360, 330, 300, 350, 380, 340],
  recent_activities: [
    { date: "2025-06-15", type: "缴费", desc: "6月医保缴费", amount: "+¥340.00" },
    { date: "2025-06-10", type: "报销", desc: "门诊费用报销", amount: "-¥256.50" },
    { date: "2025-06-05", type: "缴费", desc: "6月医保缴费", amount: "+¥340.00" },
    { date: "2025-05-28", type: "报销", desc: "药品费用报销", amount: "-¥189.00" },
    { date: "2025-05-15", type: "缴费", desc: "5月医保缴费", amount: "+¥340.00" },
    { date: "2025-05-08", type: "报销", desc: "检查费用报销", amount: "-¥425.00" },
  ],
};

// ==================== 健康画像 ====================

export const mockHealthProfile: HealthProfile = {
  health_score: 72,
  score_label: "良好",
  radar_data: [
    { name: "慢病管理", value: 58, target: 75 },
    { name: "用药规范", value: 72, target: 80 },
    { name: "就医频率", value: 65, target: 70 },
    { name: "健康指标", value: 68, target: 78 },
    { name: "生活方式", value: 75, target: 82 },
  ],
  alerts: [
    {
      severity: "high",
      icon: "🔴",
      title: "糖尿病管理评分下降",
      desc: "您的糖尿病管理评分下降至58分，建议尽快复查糖化血红蛋白",
      action: "立即预约",
    },
    {
      severity: "medium",
      icon: "🟡",
      title: "用药交互提醒",
      desc: "检测到您同时服用缬沙坦和氨氯地平，建议关注血压监测频率",
      action: "查看详情",
    },
    {
      severity: "low",
      icon: "🟢",
      title: "体检提醒",
      desc: "您已超过6个月未进行常规体检，建议近期安排",
      action: "预约体检",
    },
  ],
  medications: [
    { name: "二甲双胍", dosage: "500mg", frequency: "每日2次", status: "正常", statusColor: "text-green-600 bg-green-50" },
    { name: "缬沙坦", dosage: "80mg", frequency: "每日1次", status: "注意", statusColor: "text-yellow-600 bg-yellow-50" },
    { name: "氨氯地平", dosage: "5mg", frequency: "每日1次", status: "注意", statusColor: "text-yellow-600 bg-yellow-50" },
    { name: "阿司匹林", dosage: "100mg", frequency: "每日1次", status: "正常", statusColor: "text-green-600 bg-green-50" },
  ],
  trend_data: [
    { month: "8月", score: 78 },
    { month: "9月", score: 76 },
    { month: "10月", score: 74 },
    { month: "11月", score: 73 },
    { month: "12月", score: 71 },
    { month: "1月", score: 72 },
  ],
  suggestions: [
    { icon: "Apple", title: "调整饮食结构", desc: "建议减少精制碳水摄入，增加膳食纤维，控制每日糖分摄入在25g以内", color: "bg-green-50 text-green-600" },
    { icon: "Footprints", title: "增加有氧运动", desc: "建议每周进行150分钟中等强度有氧运动，如快走、游泳等", color: "bg-blue-50 text-blue-600" },
    { icon: "Droplets", title: "规律监测血糖", desc: "建议每日监测空腹及餐后2小时血糖，记录变化趋势", color: "bg-purple-50 text-purple-600" },
    { icon: "Moon", title: "改善睡眠质量", desc: "保证每晚7-8小时睡眠，避免熬夜，有助于血糖控制", color: "bg-indigo-50 text-indigo-600" },
    { icon: "Activity", title: "定期复查指标", desc: "建议每3个月复查糖化血红蛋白，每6个月检查肝肾功能", color: "bg-orange-50 text-orange-600" },
  ],
};

// ==================== 报销预审 ====================

export const mockOCRResult: OCRResult = {
  hospital: "南京市第一人民医院",
  date: "2025-06-10",
  patient: "张明",
  department: "内分泌科",
  items: [
    { name: "挂号费", price: 25.00 },
    { name: "糖化血红蛋白检测", price: 85.00 },
    { name: "空腹血糖检测", price: 35.00 },
    { name: "二甲双胍 500mg×60", price: 42.50 },
    { name: "缬沙坦 80mg×28", price: 68.00 },
  ],
  total: 255.50,
  confidence: 0.92,
};

export const mockPreReviewResult: PreReviewResult = {
  review_result: "通过预审",
  reimbursable: true,
  estimated_reimbursement: 217.18,
  reimbursement_rate: 0.85,
  issues: [],
  suggestions: [
    "请确保发票原件完整",
    "慢性病用药需附处方复印件",
  ],
};

export const mockClaimsPreReview: ClaimsPreReview = {
  ocr_result: mockOCRResult,
  pre_review: mockPreReviewResult,
  required_docs: [
    { name: "医疗费用发票原件", status: "uploaded" },
    { name: "费用明细清单", status: "uploaded" },
    { name: "门诊病历复印件", status: "uploaded" },
    { name: "医保卡复印件", status: "missing" },
    { name: "银行卡复印件", status: "missing" },
  ],
  claim_status: [
    { date: "2025-06-05", title: "提交报销申请", desc: "南京市第一人民医院门诊费用", status: "completed" },
    { date: "2025-06-06", title: "材料审核通过", desc: "所有材料齐全，审核通过", status: "completed" },
    { date: "2025-06-08", title: "报销款已到账", desc: "报销金额 ¥256.50 已转入您的银行账户", status: "completed" },
  ],
};

// ==================== 政策匹配 ====================

export const mockPolicyMatch: PolicyMatch = {
  total_savings: 7600,
  policies: [
    {
      id: "1",
      title: "门诊慢病待遇",
      savings: "预计每年节省 ¥3,600",
      savingsAmount: 3600,
      matchReason: "您符合糖尿病门诊慢病认定条件",
      matchScore: 95,
      category: "门诊保障",
      description: "门诊慢病待遇是指将部分慢性病、特殊疾病的门诊治疗费用纳入医保统筹基金支付范围的政策。糖尿病患者经认定后，门诊治疗费用可按住院标准报销。",
      requirements: [
        "已参加南京市职工基本医疗保险",
        "经二级以上医院确诊为糖尿病",
        "近6个月内有规律治疗记录",
        "糖化血红蛋白检测报告",
      ],
      benefits: [
        "门诊费用按住院比例报销（90%）",
        "年度报销限额提高至8万元",
        "指定药品享受零差价",
        "免起付线，直接按比例报销",
      ],
      deadline: "2026-03-31",
    },
    {
      id: "2",
      title: "高血压门诊用药保障",
      savings: "预计每年节省 ¥1,200",
      savingsAmount: 1200,
      matchReason: "您正在服用高血压相关药物，符合保障条件",
      matchScore: 88,
      category: "用药保障",
      description: "高血压门诊用药保障政策为高血压患者提供门诊用药费用报销，降低长期用药经济负担。",
      requirements: [
        "已参加南京市职工基本医疗保险",
        "经确诊为高血压（I级及以上）",
        "正在使用降压药物治疗",
      ],
      benefits: [
        "指定降压药品报销比例提高至85%",
        "年度用药限额1.5万元",
        "可享受长处方（最长12周）",
      ],
      deadline: "2026-06-30",
    },
    {
      id: "3",
      title: "异地就医直接结算",
      savings: "预计每年节省 ¥800",
      savingsAmount: 800,
      matchReason: "您有异地就医需求，可享受直接结算便利",
      matchScore: 72,
      category: "异地就医",
      description: "异地就医直接结算政策允许参保人员在异地就医时直接刷卡结算，无需垫付费用后回参保地报销。",
      requirements: [
        "已参加南京市职工基本医疗保险",
        "已办理异地就医备案",
        "就医医院已开通异地结算",
      ],
      benefits: [
        "异地就医直接刷卡结算",
        "无需垫付大额医疗费用",
        "报销比例与参保地一致",
      ],
      deadline: "长期有效",
    },
    {
      id: "4",
      title: "大病保险补充保障",
      savings: "预计每年节省 ¥2,000",
      savingsAmount: 2000,
      matchReason: "您的医疗费用已接近大病保险起付线",
      matchScore: 65,
      category: "大病保障",
      description: "大病保险是对基本医保的补充，当个人年度自付医疗费用超过起付线后，超出部分由大病保险按比例报销。",
      requirements: [
        "已参加南京市职工基本医疗保险",
        "年度自付费用超过起付线（1.5万元）",
      ],
      benefits: [
        "起付线以上部分按60%-80%报销",
        "年度报销限额40万元",
        "特殊药品纳入保障范围",
      ],
      deadline: "长期有效",
    },
  ],
};

// ==================== 数据授权 ====================

export const mockSecurityOverview: SecurityOverview = {
  active_authorizations: 10,
  anomalies: 0,
  today_accesses: 6,
  data_types: [
    { id: "account", name: "医保账户信息", icon: "Shield", desc: "参保类型、缴费记录、账户余额" },
    { id: "medical", name: "就诊记录", icon: "Activity", desc: "门诊、住院、检查检验记录" },
    { id: "medication", name: "用药记录", icon: "FileText", desc: "处方信息、药品使用记录" },
    { id: "claims", name: "报销记录", icon: "Database", desc: "报销申请、审批、支付记录" },
  ],
  agents: [
    { id: "equity", name: "权益管家", color: "bg-blue-100 text-blue-700" },
    { id: "health", name: "健康卫士", color: "bg-green-100 text-green-700" },
    { id: "claims", name: "报销助手", color: "bg-orange-100 text-orange-700" },
    { id: "policy", name: "政策顾问", color: "bg-purple-100 text-purple-700" },
  ],
  authorization_matrix: [
    { data_type: "account", agent: "equity", enabled: true, expiry: "2026-12-31" },
    { data_type: "account", agent: "health", enabled: true, expiry: "2026-12-31" },
    { data_type: "account", agent: "claims", enabled: true, expiry: "2026-06-30" },
    { data_type: "account", agent: "policy", enabled: false, expiry: "" },
    { data_type: "medical", agent: "equity", enabled: false, expiry: "" },
    { data_type: "medical", agent: "health", enabled: true, expiry: "2026-12-31" },
    { data_type: "medical", agent: "claims", enabled: true, expiry: "2026-06-30" },
    { data_type: "medical", agent: "policy", enabled: false, expiry: "" },
    { data_type: "medication", agent: "equity", enabled: false, expiry: "" },
    { data_type: "medication", agent: "health", enabled: true, expiry: "2026-12-31" },
    { data_type: "medication", agent: "claims", enabled: false, expiry: "" },
    { data_type: "medication", agent: "policy", enabled: true, expiry: "2026-12-31" },
    { data_type: "claims", agent: "equity", enabled: true, expiry: "2026-06-30" },
    { data_type: "claims", agent: "health", enabled: false, expiry: "" },
    { data_type: "claims", agent: "claims", enabled: true, expiry: "2026-12-31" },
    { data_type: "claims", agent: "policy", enabled: true, expiry: "2026-12-31" },
  ],
  rights: [
    { icon: "Eye", title: "知情权", desc: "您有权了解哪些数据被收集、如何使用以及被谁访问", color: "bg-blue-50/80" },
    { icon: "UserCheck", title: "更正权", desc: "您有权要求更正不准确的个人数据", color: "bg-green-50/80" },
    { icon: "Trash2", title: "删除权", desc: "您有权要求删除您的个人数据，法律另有规定的除外", color: "bg-red-50/80" },
    { icon: "Download", title: "可携带权", desc: "您有权以通用格式导出您的个人数据", color: "bg-purple-50/80" },
  ],
  active_auths: [
    { agent_name: "权益管家", data_type_name: "医保账户信息", data_type_icon: "Shield", expiry: "2026-12-31" },
    { agent_name: "健康卫士", data_type_name: "医保账户信息", data_type_icon: "Shield", expiry: "2026-12-31" },
    { agent_name: "报销助手", data_type_name: "医保账户信息", data_type_icon: "Shield", expiry: "2026-06-30" },
    { agent_name: "健康卫士", data_type_name: "就诊记录", data_type_icon: "Activity", expiry: "2026-12-31" },
    { agent_name: "报销助手", data_type_name: "就诊记录", data_type_icon: "Activity", expiry: "2026-06-30" },
    { agent_name: "健康卫士", data_type_name: "用药记录", data_type_icon: "FileText", expiry: "2026-12-31" },
    { agent_name: "政策顾问", data_type_name: "用药记录", data_type_icon: "FileText", expiry: "2026-12-31" },
    { agent_name: "权益管家", data_type_name: "报销记录", data_type_icon: "Database", expiry: "2026-06-30" },
    { agent_name: "报销助手", data_type_name: "报销记录", data_type_icon: "Database", expiry: "2026-12-31" },
    { agent_name: "政策顾问", data_type_name: "报销记录", data_type_icon: "Database", expiry: "2026-12-31" },
  ],
  audit_log: [
    { time: "2025-06-12 10:32", agent: "健康卫士", action: "读取用药记录", dataType: "用药记录", status: "allowed" },
    { time: "2025-06-12 10:31", agent: "权益管家", action: "查询账户余额", dataType: "医保账户信息", status: "allowed" },
    { time: "2025-06-11 15:20", agent: "报销助手", action: "读取就诊记录", dataType: "就诊记录", status: "allowed" },
    { time: "2025-06-11 14:10", agent: "政策顾问", action: "读取报销记录", dataType: "报销记录", status: "allowed" },
    { time: "2025-06-10 09:45", agent: "健康卫士", action: "读取就诊记录", dataType: "就诊记录", status: "denied" },
    { time: "2025-06-09 16:30", agent: "权益管家", action: "查询缴费记录", dataType: "医保账户信息", status: "allowed" },
  ],
};

// ==================== 聊天模拟 ====================

export interface ChatMockResponse {
  content: string;
  agent: string;
  agentColor: string;
}

export const mockChatResponses: Record<string, ChatMockResponse> = {
  "帮我查看我的医保权益": {
    content: "正在为您查询医保权益信息...\n\n📋 **参保类型**：南京市职工基本医疗保险\n💰 **账户余额**：¥8,562.30\n📅 **缴费年限**：15年3个月\n🏥 **门诊报销比例**：85%\n🏥 **住院报销比例**：90%\n\n您的医保权益状态良好，已满足门诊统筹和大病保险的享受条件。如需了解更详细的信息，可以前往「权益全景」页面查看。",
    agent: "权益管家",
    agentColor: "bg-blue-100 text-blue-700",
  },
  "帮我预审报销材料": {
    content: "好的，我来帮您预审报销材料。\n\n请上传您的医疗费用发票或报销单据，我将为您：\n1️⃣ 识别发票信息（OCR）\n2️⃣ 计算预估报销金额\n3️⃣ 检查材料完整性\n\n您也可以前往「报销预审」页面上传材料，获得更详细的预审结果。",
    agent: "报销助手",
    agentColor: "bg-orange-100 text-orange-700",
  },
  "帮我做健康风险评估": {
    content: "正在为您进行健康风险评估...\n\n⚠️ **健康评分**：72分（良好，但有改善空间）\n🔴 **重点关注**：糖尿病管理评分下降至58分\n🟡 **用药提醒**：同时服用缬沙坦和氨氯地平，建议关注血压监测\n\n建议您尽快复查糖化血红蛋白，并定期监测血压。详细健康画像请前往「健康画像」页面查看。",
    agent: "健康卫士",
    agentColor: "bg-green-100 text-green-700",
  },
  "帮我查询匹配的医保政策": {
    content: "正在为您匹配适合的医保政策...\n\n✅ **门诊慢病待遇** - 匹配度95%\n预计每年可节省 ¥3,600\n您符合糖尿病门诊慢病认定条件\n\n✅ **高血压门诊用药保障** - 匹配度88%\n预计每年可节省 ¥1,200\n\n✅ **异地就医直接结算** - 匹配度72%\n\n更多政策详情请前往「政策匹配」页面查看。",
    agent: "政策顾问",
    agentColor: "bg-purple-100 text-purple-700",
  },
};

// ==================== 医学影像 AI 标注 ====================

export interface ImagingFindingItem {
  finding_type: string;
  label: string;
  x: number;
  y: number;
  w: number;
  h: number;
  confidence: number;
  severity: "low" | "medium" | "high";
  source: "ai" | "doctor";
  status: "pending" | "confirmed" | "rejected";
  evidence?: string;
}

export interface ImagingReportData {
  conclusion: string;
  risk_level: string;
  advice: string[];
  confirmed_count: number;
  pending_count: number;
  rejected_count: number;
  generated_at: string;
  disclaimer: string;
}

export interface ImagingPolicyLink {
  trigger: string;
  title: string;
  policy_hint: string;
  description: string;
  suggestion: string;
  related_policies: string[];
}

export interface ImagingStudyResponse {
  record_id?: number;
  study_id: string;
  study_type: string;
  study_label: string;
  seed: number;
  image_base64?: string;
  findings: ImagingFindingItem[];
  report: ImagingReportData;
  policy_links: ImagingPolicyLink[];
  vision_interpretation?: string | null;
  vision_available?: boolean;
  disclaimer: string;
}

export interface ImagingStudyTypeInfo {
  label: string;
  short_label: string;
  findings: { key: string; label: string; severity: string; desc: string }[];
}

export interface ImagingRecordItem {
  id: number;
  user_id: string;
  study_id: string;
  study_type: string;
  study_label: string;
  seed: number;
  created_at: string;
  risk_level: string;
  finding_count: number;
  policy_link_count: number;
  report?: ImagingReportData;
}

// 三张示例影像（内联 SVG data URI，模拟不同检查类型的灰度影像）
const svgDataUri = (svg: string) =>
  `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;

const MOCK_CHEST_XRAY_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">
<rect width="512" height="512" fill="#14171c"/>
<g opacity="0.25">
<circle cx="60" cy="80" r="14" fill="#9aa4af"/><circle cx="420" cy="60" r="10" fill="#9aa4af"/>
<circle cx="200" cy="40" r="8" fill="#9aa4af"/><circle cx="350" cy="120" r="12" fill="#9aa4af"/>
<circle cx="80" cy="300" r="9" fill="#9aa4af"/><circle cx="440" cy="330" r="11" fill="#9aa4af"/>
</g>
<ellipse cx="160" cy="270" rx="105" ry="150" fill="#262c34" stroke="#4b5661" stroke-width="4"/>
<ellipse cx="352" cy="270" rx="105" ry="150" fill="#262c34" stroke="#4b5661" stroke-width="4"/>
<g opacity="0.12" stroke="#8b98a5">
<path d="M90 240 q70 20 140 0" fill="none"/><path d="M90 280 q70 20 140 0" fill="none"/>
<path d="M90 320 q70 20 140 0" fill="none"/><path d="M282 240 q70 20 140 0" fill="none"/>
<path d="M282 280 q70 20 140 0" fill="none"/><path d="M282 320 q70 20 140 0" fill="none"/>
</g>
<path d="M150 96 L160 96 L160 66 L150 66 Z" fill="#2c333b" opacity="0.8"/>
<ellipse cx="256" cy="180" rx="52" ry="62" fill="#30373f" stroke="#4b5661" stroke-width="3"/>
<path d="M256 240 L256 430 M256 320 q40 20 46 60" stroke="#4b5661" stroke-width="4" fill="none"/>
<ellipse cx="256" cy="118" rx="40" ry="26" fill="#2a3038" stroke="#4b5661" stroke-width="3"/>
<ellipse cx="256" cy="452" rx="120" ry="40" fill="#1d232b" stroke="#4b5661" stroke-width="3"/>
</svg>`;

const MOCK_LUNG_CT_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">
<rect width="512" height="512" fill="#101418"/>
<ellipse cx="256" cy="256" rx="230" ry="230" fill="#161b21" stroke="#39424c" stroke-width="4"/>
<circle cx="256" cy="256" r="92" fill="#1e242c" stroke="#2f3842" stroke-width="3"/>
<circle cx="256" cy="256" r="46" fill="#252c35" stroke="#2f3842" stroke-width="3"/>
<g opacity="0.35" stroke="#2f3842">
<circle cx="256" cy="256" r="150" fill="none" stroke-dasharray="4 6"/>
<circle cx="256" cy="256" r="185" fill="none" stroke-dasharray="4 6"/>
</g>
<g opacity="0.2" fill="#8b98a5">
<circle cx="120" cy="140" r="6"/><circle cx="380" cy="120" r="5"/><circle cx="180" cy="370" r="7"/>
<circle cx="360" cy="350" r="5"/><circle cx="300" cy="110" r="4"/><circle cx="110" cy="300" r="4"/>
</g>
</svg>`;

const MOCK_BRAIN_MRI_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">
<rect width="512" height="512" fill="#101418"/>
<ellipse cx="256" cy="260" rx="175" ry="210" fill="#1e252e" stroke="#3d4853" stroke-width="4"/>
<ellipse cx="256" cy="300" rx="95" ry="120" fill="#232b35" stroke="#3d4853" stroke-width="2"/>
<path d="M256 150 q40 60 40 120 q0 90 -40 130 q-40 -40 -40 -130 q0 -60 40 -120 Z" fill="#252e39" opacity="0.9"/>
<path d="M256 210 q-55 40 -95 90" stroke="#3d4853" stroke-width="3" fill="none" opacity="0.6"/>
<path d="M256 210 q55 40 95 90" stroke="#3d4853" stroke-width="3" fill="none" opacity="0.6"/>
<path d="M256 250 q-30 60 -30 110" stroke="#3d4853" stroke-width="2" fill="none" opacity="0.5"/>
<path d="M256 250 q30 60 30 110" stroke="#3d4853" stroke-width="2" fill="none" opacity="0.5"/>
<g opacity="0.3" stroke="#8b98a5"><path d="M120 120 q70 40 140 0" fill="none"/><path d="M130 400 q70 -40 140 0" fill="none"/></g>
<g opacity="0.2" fill="#9aa4af"><circle cx="150" cy="180" r="4"/><circle cx="370" cy="210" r="5"/><circle cx="330" cy="380" r="4"/></g>
</svg>`;

export const mockImagingStudyTypes: Record<string, ImagingStudyTypeInfo> = {
  chest_xray: {
    label: "胸部 X 光",
    short_label: "胸片",
    findings: [
      { key: "nodule", label: "肺结节", severity: "medium", desc: "肺部孤立性圆形阴影" },
      { key: "opacity", label: "磨玻璃影", severity: "medium", desc: "局限性密度增高区域" },
      { key: "effusion", label: "胸腔积液", severity: "medium", desc: "肋膈角钝化/积液征象" },
      { key: "pneumothorax", label: "气胸", severity: "high", desc: "肺野边缘透亮带" },
      { key: "cardiomegaly", label: "心脏增大", severity: "medium", desc: "心胸比增大" },
    ],
  },
  lung_ct: {
    label: "肺部 CT",
    short_label: "肺CT",
    findings: [
      { key: "nodule", label: "肺结节", severity: "medium", desc: "肺窗下结节样密度影" },
      { key: "opacity", label: "磨玻璃影", severity: "medium", desc: "云雾状密度增高" },
      { key: "consolidation", label: "实变", severity: "medium", desc: "肺叶实变密度影" },
      { key: "emphysema", label: "肺气肿", severity: "medium", desc: "肺组织破坏性气肿" },
      { key: "mass", label: "占位性病变", severity: "high", desc: "较大软组织密度肿块" },
    ],
  },
  brain_mri: {
    label: "头颅 MRI",
    short_label: "脑MRI",
    findings: [
      { key: "hemorrhage", label: "脑出血", severity: "high", desc: "高信号出血灶" },
      { key: "ischemia", label: "脑缺血灶", severity: "high", desc: "弥散受限信号区" },
      { key: "tumor", label: "脑肿瘤", severity: "high", desc: "异常占位强化灶" },
      { key: "infarction", label: "脑梗死", severity: "high", desc: "梗死低信号区" },
      { key: "atrophy", label: "脑萎缩", severity: "low", desc: "脑沟脑回增宽" },
    ],
  },
};

export const mockImagingPolicyLinks: ImagingPolicyLink[] = [
  {
    trigger: "高危异常",
    title: "住院结算即时推送",
    policy_hint: "双通道药品 / 住院按病种分值付费",
    description: "发现高危异常时，通过医保结算系统即时推送住院报销预估与慢病认定入口。",
    suggestion: "建议同步推送影像报告至接诊医师，并提示符合的门诊慢病待遇认定条件。",
    related_policies: ["门诊慢特病待遇", "住院按病种分值付费", "大病保险二次报销"],
  },
  {
    trigger: "肺结节随访",
    title: "肺结节随访管理",
    policy_hint: "门诊慢病认定 / 定期复查支付",
    description: "肺结节需定期随访，医保对符合条件的门诊复查给予统筹支付支持。",
    suggestion: "建议纳入门诊慢病随访管理，按指南推荐 3-6 个月复查一次低剂量 CT。",
    related_policies: ["门诊慢特病待遇", "门诊统筹支付"],
  },
];

export const mockImagingStudy: ImagingStudyResponse = {
  record_id: 101,
  study_id: "MOCK-STUDY-001",
  study_type: "chest_xray",
  study_label: "胸部 X 光",
  seed: 20240613,
  image_base64: svgDataUri(MOCK_CHEST_XRAY_SVG),
  findings: [
    { finding_type: "nodule", label: "肺结节", x: 0.31, y: 0.44, w: 0.1, h: 0.12, confidence: 0.92, severity: "medium", source: "ai", status: "pending" },
    { finding_type: "nodule", label: "肺结节", x: 0.62, y: 0.38, w: 0.09, h: 0.11, confidence: 0.87, severity: "medium", source: "ai", status: "pending" },
    { finding_type: "cardiomegaly", label: "心脏增大", x: 0.5, y: 0.63, w: 0.22, h: 0.26, confidence: 0.78, severity: "medium", source: "ai", status: "pending" },
    { finding_type: "opacity", label: "磨玻璃影", x: 0.72, y: 0.55, w: 0.12, h: 0.1, confidence: 0.81, severity: "medium", source: "ai", status: "pending" },
  ],
  report: {
    conclusion: "AI 检测到 4 处可疑发现：右肺上叶及左肺中野小结节、心影增大、右下肺磨玻璃样密度影。",
    risk_level: "待复核",
    advice: ["建议胸外科门诊就诊，完善低剂量胸部 CT 随访", "肺结节建议 3-6 个月复查", "结合临床评估心影增大的病因"],
    confirmed_count: 0,
    pending_count: 4,
    rejected_count: 0,
    generated_at: new Date().toISOString(),
    disclaimer: "本结果由 AI 辅助生成，仅供筛查参考，最终诊断须由持证医师复核确认。",
  },
  policy_links: mockImagingPolicyLinks,
  disclaimer: "本结果由 AI 辅助生成，仅供筛查参考，最终诊断须由持证医师复核确认。",
};

export const mockImagingRecords: ImagingRecordItem[] = [
  { id: 101, user_id: "demo-user", study_id: "MOCK-STUDY-001", study_type: "chest_xray", study_label: "胸部 X 光", seed: 20240613, created_at: "2026-08-20 09:32", risk_level: "待复核", finding_count: 4, policy_link_count: 2 },
  { id: 100, user_id: "demo-user", study_id: "MOCK-STUDY-002", study_type: "brain_mri", study_label: "头颅 MRI", seed: 20240611, created_at: "2026-08-12 14:05", risk_level: "高风险", finding_count: 3, policy_link_count: 3 },
  { id: 99, user_id: "demo-user", study_id: "MOCK-STUDY-003", study_type: "lung_ct", study_label: "肺部 CT", seed: 20240608, created_at: "2026-07-30 10:18", risk_level: "中风险", finding_count: 5, policy_link_count: 2 },
];

// ==================== 泛癌卫士（Oncoformer 泛癌预测） ====================

export interface CancerRiskItem {
  cancer: string;
  cancer_zh: string;
  prob: number;
  level: string;
}

export interface CancerReport {
  engine: string;
  mode: string;
  source: string;
  note: string;
  n_visits: number | null;
  risks: { concurrent: CancerRiskItem[]; future: CancerRiskItem[] };
  top_risk: CancerRiskItem | null;
  pred_age: number | null;
  profile_age: number | null;
  disclaimer: string;
  record_id?: number;
}

export interface CancerStatus {
  agent: string;
  model: string;
  engine: string;
  model_loaded: boolean;
  cohort_precomputed: boolean;
  cohort_patients: number;
  population?: { total: number; prevalence: Record<string, number> } | null;
  disclaimer: string;
}

export interface CancerCohortPatient {
  pid: string;
  meta: { cancers_present: string[]; cancer_stage: string; has_image: boolean };
  modes: Record<
    string,
    {
      top: Record<string, Array<{ cancer: string; prob: number }>>;
      pred_age: number | null;
      n_visits: number | null;
    }
  >;
}

export interface CancerCohortDetail {
  pid: string;
  engine: string;
  modes: Record<
    string,
    {
      scores: Record<string, Record<string, number>>;
      pred_age: number | null;
      n_visits: number | null;
    }
  >;
  meta: { cancers_present: string[]; cancer_stage: string; has_image: boolean };
}

export const mockCancerStatus: CancerStatus = {
  agent: "泛癌卫士",
  model: "Oncoformer (demo ckpt, 上游 Apache-2.0)",
  engine: "precomputed",
  model_loaded: false,
  cohort_precomputed: true,
  cohort_patients: 2,
  population: { total: 790, prevalence: { "Lung cancer": 118, "Colorectal cancer": 96 } },
  disclaimer: "基于 Oncoformer 研究模型的演示输出（温附医团队，Cell 2026），非临床诊断依据。",
};

export const mockCancerReport: CancerReport = {
  engine: "precomputed",
  mode: "cohort_fallback",
  source: "compass_cohort",
  note: "当前部署未加载真模型权重，以下为 790 例真实脱敏队列的患癌人群占比基线",
  n_visits: null,
  risks: {
    concurrent: [
      { cancer: "Lung cancer", cancer_zh: "肺癌", prob: 0.149, level: "队列基线" },
      { cancer: "Colorectal cancer", cancer_zh: "结直肠癌", prob: 0.122, level: "队列基线" },
    ],
    future: [],
  },
  top_risk: { cancer: "Lung cancer", cancer_zh: "肺癌", prob: 0.149, level: "队列基线" },
  pred_age: null,
  profile_age: 56,
  disclaimer: "基于 Oncoformer 研究模型的演示输出（温附医团队，Cell 2026），非临床诊断依据。",
};

export const mockCancerCohort: { patients: CancerCohortPatient[]; population: CancerStatus["population"] } = {
  patients: [
    {
      pid: "COMPASS-0001",
      meta: { cancers_present: ["Lung cancer"], cancer_stage: "II", has_image: true },
      modes: {
        fused: { top: { concurrent: [{ cancer: "Lung cancer", prob: 0.91 }], future: [] }, pred_age: 63.2, n_visits: 14 },
        ehr_only: { top: { concurrent: [{ cancer: "Lung cancer", prob: 0.74 }], future: [] }, pred_age: 62.8, n_visits: 14 },
        img_only: { top: { concurrent: [{ cancer: "Lung cancer", prob: 0.55 }], future: [] }, pred_age: null, n_visits: 14 },
      },
    },
    {
      pid: "COMPASS-0002",
      meta: { cancers_present: [], cancer_stage: "NA", has_image: true },
      modes: {
        fused: { top: { concurrent: [{ cancer: "Colorectal cancer", prob: 0.08 }], future: [] }, pred_age: 58.4, n_visits: 11 },
        ehr_only: { top: { concurrent: [{ cancer: "Colorectal cancer", prob: 0.07 }], future: [] }, pred_age: 58.1, n_visits: 11 },
        img_only: { top: { concurrent: [{ cancer: "Lung cancer", prob: 0.06 }], future: [] }, pred_age: null, n_visits: 11 },
      },
    },
  ],
  population: mockCancerStatus.population,
};

export const mockCancerCohortDetail: CancerCohortDetail = {
  pid: "COMPASS-0001",
  engine: "oncoformer-precomputed",
  modes: {
    fused: {
      scores: {
        concurrent: { "Lung cancer": 0.91, "Colorectal cancer": 0.12, "Gastric cancer": 0.09, "Liver cancer": 0.07, "Breast cancer": 0.05, "Ovarian/Cervical cancer": 0.04, "Prostate cancer": 0.06 },
        future: { "Lung cancer": 0.18, "Colorectal cancer": 0.11, "Gastric cancer": 0.08, "Liver cancer": 0.06, "Breast cancer": 0.05, "Ovarian/Cervical cancer": 0.04, "Prostate cancer": 0.05 },
      },
      pred_age: 63.2,
      n_visits: 14,
    },
    ehr_only: {
      scores: {
        concurrent: { "Lung cancer": 0.74, "Colorectal cancer": 0.11, "Gastric cancer": 0.08, "Liver cancer": 0.07, "Breast cancer": 0.05, "Ovarian/Cervical cancer": 0.04, "Prostate cancer": 0.05 },
        future: { "Lung cancer": 0.15, "Colorectal cancer": 0.1, "Gastric cancer": 0.07, "Liver cancer": 0.06, "Breast cancer": 0.04, "Ovarian/Cervical cancer": 0.04, "Prostate cancer": 0.05 },
      },
      pred_age: 62.8,
      n_visits: 14,
    },
    img_only: {
      scores: {
        concurrent: { "Lung cancer": 0.55, "Colorectal cancer": 0.06, "Gastric cancer": 0.05, "Liver cancer": 0.05, "Breast cancer": 0.04, "Ovarian/Cervical cancer": 0.04, "Prostate cancer": 0.04 },
        future: { "Lung cancer": 0.12, "Colorectal cancer": 0.05, "Gastric cancer": 0.05, "Liver cancer": 0.04, "Breast cancer": 0.04, "Ovarian/Cervical cancer": 0.03, "Prostate cancer": 0.04 },
      },
      pred_age: null,
      n_visits: 14,
    },
  },
  meta: { cancers_present: ["Lung cancer"], cancer_stage: "II", has_image: true },
};
