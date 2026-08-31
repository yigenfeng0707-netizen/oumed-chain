"""Unified Oncoformer model architecture for pretraining and finetuning.

Implements the multimodal time-series transformer described in the manuscript:
- visit-level multimodal examination encoder (tabular + optional CXR);
- days-since-first-visit temporal positional embedding;
- auto-regressive (causal) GPT-2 temporal transformer;
- task-specific heads for downstream prediction or masked-feature pretraining;
- adversarial de-biasing via a Gradient Reversal Layer feeding a cohort
  discriminator and a missingness discriminator (DANN-style).
"""
import torch
import torch.nn as nn
import torch.nn.functional as F
import timm
from einops import rearrange
from transformers import BertConfig
from transformers import GPT2Model, BertModel


# ---------------------------------------------------------------------------
# Gradient reversal layer (DANN, Ganin & Lempitsky 2015)
# ---------------------------------------------------------------------------

class _GradReverse(torch.autograd.Function):
    @staticmethod
    def forward(ctx, x, lambda_):
        ctx.lambda_ = float(lambda_)
        return x.view_as(x)

    @staticmethod
    def backward(ctx, grad_output):
        return grad_output.neg() * ctx.lambda_, None


def grad_reverse(x, lambda_):
    """Identity in the forward pass; negate-and-scale gradient in backward."""
    return _GradReverse.apply(x, lambda_)


# ---------------------------------------------------------------------------
# Discriminator heads for adversarial de-biasing
# ---------------------------------------------------------------------------

class CohortDiscriminator(nn.Module):
    """Patient-level classifier over cohort labels.

    Operates on the pooled temporal representation; trained adversarially via
    the GRL to suppress cohort-specific features in the encoder output.
    """

    def __init__(self, hidden_dim, n_cohorts, dropout=0.1):
        super().__init__()
        self.classifier = nn.Sequential(
            nn.Linear(hidden_dim, 256),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(256, n_cohorts),
        )

    def forward(self, x):
        return self.classifier(x)


class MissingnessDiscriminator(nn.Module):
    """Per-visit, per-feature missingness classifier.

    Outputs a single logit per (visit, feature) pair predicting whether the
    raw feature value was missing. Adversarial training forces the visit
    encoder to learn missingness-invariant representations.
    """

    def __init__(self, hidden_dim, n_features, dropout=0.1):
        super().__init__()
        self.classifier = nn.Sequential(
            nn.Linear(hidden_dim, 256),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(256, n_features),
        )

    def forward(self, x):
        return self.classifier(x)


# ---------------------------------------------------------------------------
# CXR image encoder
# ---------------------------------------------------------------------------

class CXRFeatureExtractor(nn.Module):
    """Pre-trained ViT-Large DINOv2 backbone projected to the transformer dim.

    With ``freeze_backbone=True`` the ViT-L weights are non-trainable and the
    forward call runs the backbone under ``torch.no_grad()``. This is the
    "linear probe" configuration described in the manuscript: DINOv2 acts as
    a fixed feature extractor and only ``self.projection`` learns. It cuts
    per-step time ~3–4× (no ViT-L backward) and saves ~3 GB / GPU of
    optimizer state.
    """

    def __init__(self, output_dim=768, pretrained=True, freeze_backbone=False,
                 img_size=518, grad_checkpoint=False, compile_backbone=False,
                 compile_mode='default', compile_dynamic=True):
        super().__init__()
        # `img_size` must match what the dataset feeds in: timm's PatchEmbed
        # asserts on the input H/W and won't auto-resize. DINOv2 was
        # pretrained at 518; passing any other value here makes timm
        # interpolate the positional embedding to match the new patch grid.
        self.backbone = timm.create_model(
            'vit_large_patch14_dinov2.lvd142m',
            pretrained=pretrained,
            num_classes=0,
            img_size=img_size,
        )
        backbone_dim = self.backbone.num_features
        self.projection = nn.Linear(backbone_dim, output_dim)
        self.freeze_backbone = freeze_backbone
        if freeze_backbone:
            for p in self.backbone.parameters():
                p.requires_grad_(False)
            self.backbone.eval()
        elif grad_checkpoint:
            # ~50% activation-memory reduction for ~10-20% per-step recompute
            # cost. Only meaningful when the backbone is trainable.
            if hasattr(self.backbone, 'set_grad_checkpointing'):
                self.backbone.set_grad_checkpointing(enable=True)
            else:
                raise RuntimeError(
                    "timm backbone does not expose set_grad_checkpointing; "
                    "upgrade timm or set cxr_grad_checkpoint=false."
                )

        # torch.compile pass on the ViT — typical 1.1-1.8x speedup at bf16.
        # `dynamic=True` is the default: subset-forward feeds DINOv2 with a
        # varying batch dim (only the CXR-bearing samples per batch), so a
        # shape-specialized (dynamic=False) graph would hit dynamo's recompile
        # limit (8) and silently fall back to eager. The shape-polymorphic
        # graph trades some kernel specialization for "compile once, run on
        # any batch size".
        if compile_backbone:
            self.backbone = torch.compile(
                self.backbone, mode=compile_mode, dynamic=compile_dynamic,
            )

    def train(self, mode: bool = True):
        super().train(mode)
        # Keep the backbone in eval mode (no dropout) even when the parent
        # module is set to train — required for stable linear-probe training.
        if self.freeze_backbone:
            self.backbone.eval()
        return self

    def forward(self, x):
        # x: (B, 3, H, W) — H = W = config['img_size'] (default 518)
        if self.freeze_backbone:
            with torch.no_grad():
                feats = self.backbone(x)
        else:
            feats = self.backbone(x)
        return self.projection(feats)


# ---------------------------------------------------------------------------
# VAE-style multimodal fusion utilities
# ---------------------------------------------------------------------------

class VAEEncoder(nn.Module):
    """Outputs mean and log-variance for a diagonal-Gaussian latent."""

    def __init__(self, input_dim, latent_dim):
        super().__init__()
        self.mean_projection = nn.Linear(input_dim, latent_dim)
        self.logvar_projection = nn.Linear(input_dim, latent_dim)

    def forward(self, x):
        return self.mean_projection(x), self.logvar_projection(x)


def reparameterize(mean, logvar):
    std = torch.exp(0.5 * logvar)
    return mean + torch.randn_like(std) * std


def kl_divergence(mean, logvar):
    return -0.5 * torch.sum(1 + logvar - mean.pow(2) - logvar.exp(), dim=-1)


def symmetric_kl_divergence(mean1, logvar1, mean2, logvar2):
    var1 = torch.exp(logvar1)
    var2 = torch.exp(logvar2)
    kl_12 = 0.5 * torch.sum(
        logvar2 - logvar1 + (var1 + (mean1 - mean2).pow(2)) / var2 - 1, dim=-1,
    )
    kl_21 = 0.5 * torch.sum(
        logvar1 - logvar2 + (var2 + (mean2 - mean1).pow(2)) / var1 - 1, dim=-1,
    )
    return kl_12 + kl_21


# ---------------------------------------------------------------------------
# Temporal positional embedding
# ---------------------------------------------------------------------------

class TemporalPositionalEmbedding(nn.Module):
    """Learned embedding indexed by days since the patient's first visit."""

    def __init__(self, max_time_days=3650, embedding_dim=768):
        super().__init__()
        self.max_time_days = max_time_days
        self.temporal_embedding = nn.Embedding(max_time_days + 1, embedding_dim)

    def forward(self, time_days):
        time_days = torch.clamp(time_days, 0, self.max_time_days)
        return self.temporal_embedding(time_days)


# ---------------------------------------------------------------------------
# Multimodal examination encoder
# ---------------------------------------------------------------------------

class MultimodalExaminationEncoder(nn.Module):
    """Per-visit BERT encoder fusing discretized tabular features with CXR.

    A per-sample `cxr_present` mask gates the image contribution so patients
    without an image receive no spurious image bias.
    """

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.use_vae = config.get('use_vae', False)
        self.latent_dim = config.get('latent_dim', 256)

        hidden_size = config['transformer'].hidden_size

        # NOTE: the BertModel below carries its own word and token-type
        # embeddings (initialized from vocab_size / type_vocab_size). The
        # earlier code also defined a standalone self.token_embedding /
        # self.type_embedding pair — they were never referenced in forward
        # and showed up as "unused parameters" in DDP, forcing the
        # find_unused_parameters trace at every step. Removed.

        self.use_img_model = config.get('use_img_model', False)
        if self.use_img_model:
            self.cxr_extractor = CXRFeatureExtractor(
                output_dim=hidden_size,
                pretrained=config.get('img_pretrained', True),
                freeze_backbone=config.get('freeze_cxr_backbone', False),
                img_size=config.get('img_size', 518),
                grad_checkpoint=config.get('cxr_grad_checkpoint', False),
                compile_backbone=config.get('compile_cxr_backbone', False),
                compile_mode=config.get('cxr_compile_mode', 'default'),
                compile_dynamic=config.get('cxr_compile_dynamic', True),
            )
            self.cxr_projection = nn.Linear(hidden_size, hidden_size)

        # `add_pooling_layer=False` and `max_position_embeddings=1` together
        # strip out two boilerplate modules BertModel would otherwise create:
        # the pooler (we don't call it; we read [CLS] directly) and the 8192-
        # position absolute embedding table (position_embedding_type='none'
        # already disables its use, but the parameter still gets allocated).
        # Removing them eliminates ~3M parameters that would otherwise be
        # flagged as "unused" by DDP's gradient sync and force the expensive
        # find_unused_parameters trace on every step.
        self.visit_encoder = BertModel(BertConfig(
            vocab_size=config['n_category_values'] + config['n_float_values'] + 2,
            hidden_size=hidden_size,
            num_hidden_layers=config.get('visit_encoder_layers', 2),
            num_attention_heads=12,
            intermediate_size=hidden_size * 4,
            hidden_act="gelu",
            hidden_dropout_prob=0.1,
            attention_probs_dropout_prob=0.1,
            max_position_embeddings=1,
            type_vocab_size=config['n_category_feats'] + config['n_float_feats'] + 1,
            initializer_range=0.02,
            layer_norm_eps=1e-12,
            pad_token_id=1,
            position_embedding_type="none",
            use_cache=True,
            classifier_dropout=None,
        ), add_pooling_layer=False)

        if self.use_vae:
            self.labtest_vae_encoder = VAEEncoder(hidden_size, self.latent_dim)
            if self.use_img_model:
                self.cxr_vae_encoder = VAEEncoder(hidden_size, self.latent_dim)
            self.latent_decoder = nn.Linear(self.latent_dim, hidden_size)

        self.register_buffer(
            "token_type_ids",
            torch.arange(config['n_category_feats'] + config['n_float_feats'] + 1),
            persistent=False,
        )
        self.register_buffer("one", torch.ones(1, dtype=torch.long), persistent=False)
        self.register_buffer("zero", torch.zeros(1, dtype=torch.long), persistent=False)

    def _apply_cxr(self, tabular_features, cxr_images, cxr_present):
        """CXR contribution gated by the per-sample `cxr_present` mask.

        Only the subset of samples with cxr_present=True are pushed through
        DINOv2 (which is the bulk of the per-step cost); the rest stay as
        zeros. This is ~6× faster than the all-batch forward when CXR
        prevalence is ~15% as in COMPASS.

        Returns ``None`` when no sample in the batch has an image, letting
        the caller skip the projection step entirely.
        """
        if not (self.use_img_model and hasattr(self, 'cxr_extractor')):
            return None
        if cxr_images is None:
            return None
        if cxr_present is not None and not cxr_present.any():
            return None

        B, L, D = tabular_features.shape
        if cxr_present is not None:
            idx = cxr_present.nonzero(as_tuple=True)[0]
            present_feats = self.cxr_extractor(cxr_images[idx])  # (k, D)
            full = torch.zeros(
                B, present_feats.shape[-1],
                device=present_feats.device, dtype=present_feats.dtype,
            )
            full[idx] = present_feats
        else:
            full = self.cxr_extractor(cxr_images)  # (B, D)

        cxr_features = full.unsqueeze(1).expand(-1, L, -1)  # (B, L, D)
        return cxr_features

    def forward(self, cat_feats, float_feats, cxr_images=None,
                cxr_present=None, ehr_present=None):
        B = cat_feats.shape[0]
        L = cat_feats.shape[2]
        hidden_size = self.config['transformer'].hidden_size

        # Fast path for image-only inference: when ehr_present is False for
        # every sample in the batch, the tabular encoder's output gets gated
        # to zero downstream anyway, so we skip the (expensive) BertModel
        # forward and just emit a zero placeholder. This is the dominant
        # cost of the img_only ablation pass at ~3-4x faster vs running BERT
        # and throwing the result away.
        if ehr_present is not None and not ehr_present.any():
            ref_dtype = next(self.visit_encoder.parameters()).dtype
            tabular_features = torch.zeros(
                B, L, hidden_size,
                device=cat_feats.device, dtype=ref_dtype,
            )
            vae_losses = {}
            cxr_features = self._apply_cxr(tabular_features, cxr_images, cxr_present)
            if cxr_features is not None:
                cxr_proj = self.cxr_projection(cxr_features)
                if cxr_present is not None:
                    gate = cxr_present.to(cxr_proj.dtype).view(B, 1, 1)
                    cxr_proj = cxr_proj * gate
                visit_embeddings = cxr_proj
            else:
                visit_embeddings = tabular_features
            return visit_embeddings, vae_losses

        cat_feats_mask = cat_feats == -1
        float_feats_mask = float_feats == -1
        attention_mask = torch.cat([
            self.one.unsqueeze(1).unsqueeze(0).expand(B, -1, L),
            ~cat_feats_mask,
            ~float_feats_mask,
        ], dim=1)
        attention_mask = rearrange(attention_mask, 'b n l -> (b l) n')

        cat_feats = cat_feats + 2
        cat_feats[cat_feats_mask] = 1
        float_feats = float_feats + 2 + self.config['n_category_values']
        float_feats[float_feats_mask] = 1
        input_ids = torch.cat([
            self.zero.unsqueeze(1).unsqueeze(0).expand(B, -1, L),
            cat_feats,
            float_feats,
        ], dim=1)
        input_ids = rearrange(input_ids, 'b n l -> (b l) n')

        BL = input_ids.shape[0]
        token_type_ids = self.token_type_ids.unsqueeze(0).expand(BL, -1)

        tabular_output = self.visit_encoder(
            input_ids=input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
        ).last_hidden_state

        tabular_features = tabular_output[:, 0, :]
        tabular_features = rearrange(tabular_features, '(b l) d -> b l d', b=B)

        # Gate EHR contribution by per-sample `ehr_present` mask. With
        # modality-dropout training and inference-time mode flags, this is
        # how the model is told "treat this sample's EHR as missing" —
        # critical for imaging-only inference. Default (None) = always on.
        if ehr_present is not None:
            ehr_gate = ehr_present.to(tabular_features.dtype).view(B, 1, 1)
            tabular_features = tabular_features * ehr_gate

        vae_losses = {}
        cxr_features = self._apply_cxr(tabular_features, cxr_images, cxr_present)

        if not self.use_vae:
            if cxr_features is not None:
                cxr_proj = self.cxr_projection(cxr_features)
                if cxr_present is not None:
                    gate = cxr_present.to(cxr_proj.dtype).view(B, 1, 1)
                    cxr_proj = cxr_proj * gate
                # Average per-(visit, sample) L2 magnitude of the projection
                # output. A healthy image-side path keeps this non-zero and
                # roughly stable across epochs once trained.
                vae_losses['cxr_contribution_norm'] = (
                    cxr_proj.detach().float().norm(dim=-1).mean()
                )
                visit_embeddings = tabular_features + cxr_proj
            else:
                visit_embeddings = tabular_features
            return visit_embeddings, vae_losses

        # VAE branch
        labtest_mean, labtest_logvar = self.labtest_vae_encoder(tabular_features)
        labtest_z = reparameterize(labtest_mean, labtest_logvar)
        vae_losses['labtest_kl'] = kl_divergence(labtest_mean, labtest_logvar).mean()

        if cxr_features is not None:
            cxr_mean, cxr_logvar = self.cxr_vae_encoder(cxr_features)
            cxr_z = reparameterize(cxr_mean, cxr_logvar)
            vae_losses['cxr_kl'] = kl_divergence(cxr_mean, cxr_logvar).mean()
            vae_losses['symmetric_kl'] = symmetric_kl_divergence(
                labtest_mean, labtest_logvar, cxr_mean, cxr_logvar,
            ).mean()
            # When CXR is absent for a sample, fall back to the lab-only latent
            # for that row instead of averaging with a zero placeholder.
            if cxr_present is not None:
                gate = cxr_present.to(cxr_z.dtype).view(B, 1, 1)
                fused_z = labtest_z + (cxr_z - labtest_z) * 0.5 * gate
            else:
                fused_z = (labtest_z + cxr_z) / 2
        else:
            fused_z = labtest_z
            vae_losses['cxr_kl'] = torch.tensor(0.0, device=labtest_z.device)
            vae_losses['symmetric_kl'] = torch.tensor(0.0, device=labtest_z.device)

        visit_embeddings = self.latent_decoder(fused_z)
        return visit_embeddings, vae_losses


# ---------------------------------------------------------------------------
# Task heads
# ---------------------------------------------------------------------------

class FinetuneTaskHeads(nn.Module):
    """Per-task prediction heads for downstream clinical objectives."""

    def __init__(self, config):
        super().__init__()
        self.n_cls = config['n_cls']
        hidden_dim = config['transformer'].hidden_size

        self.heads = nn.ModuleList([
            nn.Sequential(
                nn.Linear(hidden_dim, 256),
                nn.ReLU(),
                nn.Dropout(0.1),
                nn.Linear(256, n),
            )
            for n in self.n_cls
        ])

    def forward(self, x):
        return [head(x) for head in self.heads]


class PretrainTaskHeads(nn.Module):
    """Masked-feature reconstruction heads.

    With ``predict_next_visit=True`` (manuscript default) the head additionally
    reconstructs the masked features at visit t+1, matching the MLM objective
    described in the Methods.
    """

    def __init__(self, config):
        super().__init__()
        hidden_dim = config['transformer'].hidden_size
        n_cls = len(config.get('cls_label_names', []))
        n_reg = len(config.get('reg_label_names', []))
        self.predict_next_visit = config.get('predict_next_visit', True)

        def _cls_head():
            return nn.Sequential(
                nn.Linear(hidden_dim, 256), nn.ReLU(), nn.Linear(256, 2),
            )

        def _reg_head():
            return nn.Sequential(
                nn.Linear(hidden_dim, 256), nn.ReLU(), nn.Linear(256, 1),
            )

        self.cls_heads = nn.ModuleList([_cls_head() for _ in range(n_cls)])
        self.reg_heads = nn.ModuleList([_reg_head() for _ in range(n_reg)])

        if self.predict_next_visit:
            self.next_cls_heads = nn.ModuleList([_cls_head() for _ in range(n_cls)])
            self.next_reg_heads = nn.ModuleList([_reg_head() for _ in range(n_reg)])

    def forward(self, hidden_states, cat_feats=None, float_feats=None, valid_mask=None):
        cls_predictions = [head(hidden_states) for head in self.cls_heads]
        reg_predictions = [head(hidden_states).squeeze(-1) for head in self.reg_heads]

        next_cls_predictions = []
        next_reg_predictions = []
        if self.predict_next_visit:
            next_cls_predictions = [head(hidden_states) for head in self.next_cls_heads]
            next_reg_predictions = [head(hidden_states).squeeze(-1) for head in self.next_reg_heads]

        return {
            'cls_current': cls_predictions,
            'reg_current': reg_predictions,
            'cls_next': next_cls_predictions,
            'reg_next': next_reg_predictions,
        }


# ---------------------------------------------------------------------------
# Top-level Oncoformer
# ---------------------------------------------------------------------------

class Oncoformer(nn.Module):
    """Unified Oncoformer model.

    Architecture:
      1. MultimodalExaminationEncoder — visit-level BERT fusing tabular EHR
         and (optionally, gated by `cxr_present`) CXR features.
      2. TemporalPositionalEmbedding — days-since-first-visit encoding.
      3. GPT2Model — causal temporal transformer across the visit sequence.
      4. Task heads — finetune (classification/regression) or pretrain (MLM
         reconstruction of current and, optionally, next visit).
      5. Adversarial heads (optional) — MissingnessDiscriminator on the
         per-visit encoder output and CohortDiscriminator on the pooled
         temporal representation, each fed via a Gradient Reversal Layer.

    Forward returns ``(predictions, aux)`` where ``aux`` is a dict with keys
    ``vae`` (dict of KL losses), ``cohort_logits`` (or None), and
    ``missingness_logits`` (or None).
    """

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.mode = config.get('mode', 'finetune')

        hidden_dim = config['transformer'].hidden_size

        self.multimodal_encoder = MultimodalExaminationEncoder(config)

        self.temporal_embedding = TemporalPositionalEmbedding(
            max_time_days=config.get('max_time_days', 3650),
            embedding_dim=hidden_dim,
        )

        # GPT2Model carries a (vocab_size, hidden) word-token embedding (wte).
        # We always pass `inputs_embeds=...` so wte is never used. Shrinking
        # vocab_size to 1 drops a ~12M-parameter unused tensor that would
        # otherwise force DDP into find_unused_parameters_true mode.
        tr_cfg = config['transformer']
        tr_cfg.vocab_size = 1
        self.temporal_transformer = GPT2Model(tr_cfg)

        # The visit encoder's position_embedding and the temporal transformer's
        # wte are tiny (1, hidden) tensors that are never used (their
        # respective modules are configured with position_embedding_type='none'
        # and inputs_embeds=). DDP otherwise flags them as unused and forces
        # the find_unused_parameters trace on every step. Freezing them puts
        # them in the "no gradient expected" set instead.
        self.multimodal_encoder.visit_encoder.embeddings.position_embeddings.weight.requires_grad_(False)
        self.temporal_transformer.wte.weight.requires_grad_(False)

        if self.mode == 'finetune':
            self.task_heads = FinetuneTaskHeads(config)
        else:
            self.pretrain_head = PretrainTaskHeads(config)

        # --- Adversarial de-biasing heads (optional) ---
        self.use_cohort_adv = config.get('use_cohort_adv', False)
        self.use_missingness_adv = config.get('use_missingness_adv', False)
        self.n_cohorts = int(config.get('n_cohorts', 1))
        self.n_features_total = (
            config['n_category_feats'] + config['n_float_feats']
        )

        if self.use_cohort_adv and self.n_cohorts >= 2:
            self.cohort_disc = CohortDiscriminator(
                hidden_dim=hidden_dim, n_cohorts=self.n_cohorts,
            )
        else:
            self.cohort_disc = None

        if self.use_missingness_adv:
            self.missingness_disc = MissingnessDiscriminator(
                hidden_dim=hidden_dim, n_features=self.n_features_total,
            )
        else:
            self.missingness_disc = None

        # GRL strength — updated by the Lightning module on each epoch.
        self.grl_lambda = 0.0

    def set_grl_lambda(self, value):
        self.grl_lambda = float(value)

    def forward(self, cat_feats, float_feats, valid_mask, time_index,
                cxr_images=None, cxr_present=None, ehr_present=None, **kwargs):
        visit_embeddings, vae_losses = self.multimodal_encoder(
            cat_feats, float_feats, cxr_images, cxr_present, ehr_present=ehr_present,
        )

        # Missingness discriminator on the visit-level encoder output.
        miss_logits = None
        if self.missingness_disc is not None:
            adv_features = grad_reverse(visit_embeddings, self.grl_lambda)
            miss_logits = self.missingness_disc(adv_features)

        temporal_emb = self.temporal_embedding(time_index)
        embeddings_with_time = visit_embeddings + temporal_emb

        # Temporal information enters via `temporal_embedding`; GPT-2's own
        # positional table only knows positions 0..n_positions-1, so we let
        # it default to sequential ids rather than passing absolute days.
        temporal_output = self.temporal_transformer(
            inputs_embeds=embeddings_with_time,
            attention_mask=valid_mask,
        ).last_hidden_state

        # Cohort discriminator on a valid-mask-weighted pool of the temporal output.
        cohort_logits = None
        if self.cohort_disc is not None:
            mask_f = valid_mask.to(temporal_output.dtype).unsqueeze(-1)
            pooled = (temporal_output * mask_f).sum(dim=1) / mask_f.sum(dim=1).clamp(min=1)
            cohort_features = grad_reverse(pooled, self.grl_lambda)
            cohort_logits = self.cohort_disc(cohort_features)

        aux = {
            'vae': vae_losses,
            'cohort_logits': cohort_logits,
            'missingness_logits': miss_logits,
        }

        if self.mode == 'finetune':
            return self.task_heads(temporal_output), aux

        return self.pretrain_head(
            temporal_output, cat_feats, float_feats, valid_mask,
        ), aux
