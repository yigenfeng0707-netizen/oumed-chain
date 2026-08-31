"""Oncoformer 真模型懒加载提供者。

权重与代码均来自上游 demo 发布物（Apache-2.0，kaiwang13/Oncoformer）。
- torch/timm 等重依赖全部在本模块内部 import：轻量部署（魔搭创空间）不装
  torch 时 `available()` 返回 False，上层自动降级到预计算队列模式。
- 单次加载约 1.3GB 权重（峰值内存 ~3GB），进程内缓存，线程安全。
- 推理约定与上游 demo_inference.py 一致：逐 visit softmax 取正类概率，
  患者级分数 = 有效 visit 内的最大值（max pooling）。
"""

from __future__ import annotations

import copy
import logging
import threading
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import settings

logger = logging.getLogger(__name__)

# 上游 diag_cols 顺序即任务头顺序（0-6 即时 / 7-13 未来），不可改动
CANCER_COLS = [
    "Lung cancer",
    "Colorectal cancer",
    "Gastric cancer",
    "Liver cancer",
    "Breast cancer",
    "Ovarian/Cervical cancer",
    "Prostate cancer",
]
CANCER_ZH = {
    "Lung cancer": "肺癌",
    "Colorectal cancer": "结直肠癌",
    "Gastric cancer": "胃癌",
    "Liver cancer": "肝癌",
    "Breast cancer": "乳腺癌",
    "Ovarian/Cervical cancer": "卵巢/宫颈癌",
    "Prostate cancer": "前列腺癌",
}
# 年龄回归头的 (均值, 标准差)，来自 demo 配置 reg_label_info
AGE_REG_INFO = (60.0, 15.0)


class ModelUnavailableError(RuntimeError):
    """torch 依赖或模型权重不可用（轻量部署环境）。"""


def _lib_dir() -> Path:
    return Path(__file__).resolve().parent / "oncoformer_lib"


class OncoformerProvider:
    """进程内单例：懒加载 Oncoformer demo 权重并提供单患者推理。"""

    def __init__(self) -> None:
        self._model = None
        self._config: dict | None = None
        self._loaded = False
        self._lock = threading.Lock()

    def _base_config(self) -> dict:
        """读取 vendored demo 配置，构建全新 GPT2Config。

        Oncoformer.__init__ 会改写 config['transformer']（vocab_size 置 1），
        因此 transformer 必须用新建的 GPT2Config，且每次 dataset 构建
        都基于本方法的干净副本。
        """
        import json

        from transformers import GPT2Config

        if self._config is None:
            cfg = json.loads((_lib_dir() / "demo_config.json").read_text(encoding="utf-8"))
            tr = cfg.get("transformer", {})
            cfg["transformer"] = GPT2Config(
                vocab_size=tr.get("vocab_size", 32000),
                n_embd=tr.get("hidden_size", 768),
                n_layer=tr.get("num_hidden_layers", 12),
                n_head=tr.get("num_attention_heads", 12),
                n_inner=tr.get("intermediate_size", 3072),
                activation_function=tr.get("hidden_act", "gelu"),
                resid_pdrop=tr.get("hidden_dropout_prob", 0.1),
                attn_pdrop=tr.get("attention_probs_dropout_prob", 0.1),
                n_positions=cfg["seq_max_len"],
                layer_norm_eps=tr.get("layer_norm_eps", 1e-12),
            )
            self._config = cfg
        return self._config

    # ------------------------------------------------------------------
    # 可用性
    # ------------------------------------------------------------------

    def _ckpt_path(self) -> Path | None:
        p = (settings.ONCOFORMER_CKPT_PATH or "").strip()
        return Path(p) if p else None

    def available(self) -> bool:
        """真模型推理是否可用（依赖齐 + 权重文件存在）。"""
        ckpt = self._ckpt_path()
        if ckpt is None or not ckpt.exists():
            return False
        try:
            import timm  # noqa: F401
            import einops  # noqa: F401
            import pytorch_lightning  # noqa: F401
            import torchvision  # noqa: F401
            import transformers  # noqa: F401
            import torch  # noqa: F401
        except ImportError:
            return False
        return True

    def is_loaded(self) -> bool:
        return self._loaded

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------

    def _ensure_loaded(self, cfg_for_model: dict):
        """首次调用时构建模型。

        必须在 ChunkedOncoDataset 构建之后调用：dataset 的 _setup_config
        会把 n_cls / cls_label_names 写进 config，Oncoformer.__init__ 依赖它们。
        传入前会深拷贝，因为 Oncoformer.__init__ 会改写 config['transformer']。
        """
        with self._lock:
            if self._loaded:
                return
            if not self.available():
                raise ModelUnavailableError("Oncoformer 真模型不可用（缺 torch 依赖或权重文件）")

            import torch  # noqa: F401  (载入权重需要)
            from .oncoformer_lib.Utils import load_ckpt
            from .oncoformer_lib.oncoformer import Oncoformer

            model = Oncoformer(copy.deepcopy(cfg_for_model))
            ckpt = load_ckpt(str(self._ckpt_path()), map_location="cpu")
            state = {k[len("model."):]: v for k, v in ckpt["state_dict"].items()
                     if k.startswith("model.")}
            missing, unexpected = model.load_state_dict(state, strict=False)
            real_missing = [k for k in missing if ".proj" not in k and "adv" not in k]
            if real_missing:
                logger.warning("Oncoformer 权重缺失 %d 项（示例: %s）",
                               len(real_missing), real_missing[:3])
            if unexpected:
                logger.warning("Oncoformer 权重多余 %d 项（忽略）", len(unexpected))
            model.eval()
            self._model = model
            self._loaded = True
            logger.info("Oncoformer 真模型加载完成 (ckpt=%s)", self._ckpt_path())

    # ------------------------------------------------------------------
    # 推理
    # ------------------------------------------------------------------

    def predict_df(
        self,
        df: pd.DataFrame,
        mode: str = "ehr_only",
        image_dir: str | None = None,
        seed: int = 0,
    ) -> dict[str, Any]:
        """对单个患者（1 行 parquet-schema DataFrame）做一次前向。

        mode: fused / ehr_only / img_only（与上游 demo 的三模态一致）。
        返回: {"scores": {horizon: {cancer: prob}}, "timeline": [...],
               "pred_age": float, "n_visits": int}
        """
        import torch

        from .oncoformer_lib.onco_dataset_chunk import ChunkedOncoDataset

        if mode not in ("fused", "ehr_only", "img_only"):
            raise ValueError(f"未知推理模式: {mode}")
        if not self.available():
            raise ModelUnavailableError("Oncoformer 真模型不可用（缺 torch 依赖或权重文件）")

        # 先建 dataset（_setup_config 会把 n_cls/cls_label_names 写入 config），
        # 再据此构建模型 —— 与上游 demo_inference 的时序一致
        cfg = copy.deepcopy(self._base_config())
        if image_dir:
            cfg["img_data_dir"] = str(image_dir)
        ds = ChunkedOncoDataset([df], "test", cfg)
        self._ensure_loaded(cfg)
        item = ds[0]
        data = item["data"]

        # 单样本 → 批维度 B=1（CPU 上绝不能批到 demo 的 batch_size=16/518px）
        cat = data["cat_feats"].unsqueeze(0)                    # (1,1,L)
        flt = data["float_feats"].unsqueeze(0)                  # (1,28,L)
        vm = data["valid_mask"].unsqueeze(0)                    # (1,L)
        ti = data["time_index"].unsqueeze(0).long()             # (1,L)
        ep = data["ehr_present"].unsqueeze(0)                   # (1,)
        cxr = data.get("cxr_images")
        cp = data.get("cxr_present")
        if cxr is not None:
            cxr = cxr.unsqueeze(0)
            cp = cp.unsqueeze(0)
        if mode == "ehr_only" and cp is not None:
            cp = torch.zeros_like(cp)                           # 门控掉影像贡献
        elif mode == "img_only":
            ep = torch.zeros_like(ep)                           # 门控掉 EHR 贡献

        # VAE 重参数化含随机采样，固定种子保证同一患者分数可复现
        torch.manual_seed(seed)
        with torch.no_grad():
            y_cls, _ = self._model(cat, flt, vm, ti,
                                   cxr_images=cxr, cxr_present=cp, ehr_present=ep)

        valid = vm[0].to(torch.bool)
        n_visits = int(valid.sum().item())
        times = ti[0][valid].tolist()

        # y_cls[i<14]: (1,L,2) logits → 正类概率；14: 年龄回归 (1,L,1)
        def head_probs(i: int) -> list[float]:
            probs = torch.softmax(y_cls[i][0].float(), dim=-1)[..., 1]
            return probs[valid].tolist()

        scores: dict[str, dict[str, float]] = {"concurrent": {}, "future": {}}
        for i, name in enumerate(CANCER_COLS):
            probs_c = head_probs(i)
            probs_f = head_probs(i + 7)
            scores["concurrent"][name] = max(probs_c) if probs_c else 0.0
            scores["future"][name] = max(probs_f) if probs_f else 0.0

        age_head = y_cls[14][0].float()[..., 0][valid]
        mean, std = AGE_REG_INFO
        pred_age = float((age_head[-1].item() * std + mean)) if n_visits else None

        timeline = [
            {
                "visit": k + 1,
                "day": int(times[k]),
                "probs": {name: round(head_probs(i)[k], 4)
                          for i, name in enumerate(CANCER_COLS)},
            }
            for k in range(n_visits)
        ]
        return {
            "scores": scores,
            "timeline": timeline,
            "pred_age": pred_age,
            "n_visits": n_visits,
            "pid": item.get("pid"),
        }


_provider: OncoformerProvider | None = None


def get_cancer_model() -> OncoformerProvider:
    global _provider
    if _provider is None:
        _provider = OncoformerProvider()
    return _provider


def cohort_data_dir() -> Path | None:
    """COMPASS 队列数据目录（metadata.parquet + cxr_images），本地部署设置。"""
    p = (settings.ONCOFORMER_DATA_DIR or "").strip()
    return Path(p) if p else None


def cancer_names_zh() -> dict[str, str]:
    return dict(CANCER_ZH)


__all__ = [
    "ModelUnavailableError",
    "OncoformerProvider",
    "CANCER_COLS",
    "CANCER_ZH",
    "get_cancer_model",
    "cohort_data_dir",
    "cancer_names_zh",
]
