"""泛癌卫士（Oncoformer 泛癌预测）服务包。

- model_provider: Oncoformer 真模型懒加载与单患者推理
- visit_synthesizer: 用户画像 → 模拟就诊序列（17 列 parquet schema）
- cohort: COMPASS 示例队列（真模型实时 / 预计算 JSON 两种形态）
- engine: 面向路由层与 REST 的风险报告编排
"""
