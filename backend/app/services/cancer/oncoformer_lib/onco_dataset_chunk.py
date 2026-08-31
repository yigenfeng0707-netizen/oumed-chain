import gc
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytorch_lightning as pl
import torch
import torchvision.transforms as transforms
from PIL import Image
from torch.utils.data import DataLoader, Dataset

from .Utils import load_parquet

_MP_CONTEXT = None if sys.platform == 'win32' else 'fork'


def optimize_dtypes(df):
    for col in df.columns:
        if pd.api.types.is_integer_dtype(df[col]):
            col_min, col_max = df[col].min(), df[col].max()
            
            if col_min >= 0:
                if col_max < 2**8:
                    df[col] = df[col].astype(np.uint8)
                elif col_max < 2**16:
                    df[col] = df[col].astype(np.uint16)
                elif col_max < 2**32:
                    df[col] = df[col].astype(np.uint32)
            else:
                if col_min > -2**7 and col_max < 2**7:
                    df[col] = df[col].astype(np.int8)
                elif col_min > -2**15 and col_max < 2**15:
                    df[col] = df[col].astype(np.int16)
                elif col_min > -2**31 and col_max < 2**31:
                    df[col] = df[col].astype(np.int32)
        
        elif pd.api.types.is_float_dtype(df[col]):
            df[col] = df[col].astype(np.float32)
            
        elif pd.api.types.is_string_dtype(df[col]) and df[col].nunique() / len(df[col]) < 0.5:
            df[col] = df[col].astype('category')
            
    return df

def sample_subset(mask, prob):
    """Sample subset of valid indices for masking, following original logic"""
    valid_indices = np.where(mask)[0]
    if len(valid_indices) == 0:
        return np.array([], dtype=int), np.array([], dtype=int)
    
    n_total = len(valid_indices)
    n_output = max(1, int(n_total * prob))
    
    # Randomly select indices for output (masked prediction)
    output_indices = np.random.choice(valid_indices, size=n_output, replace=False)
    
    # Remaining indices are for input
    input_indices = np.setdiff1d(valid_indices, output_indices)
    
    return input_indices, output_indices

class CXRImageProcessor:
    """Stateless CXR loader returning a fixed-shape (tensor, present) pair.

    The dataframe carries the absolute path to each patient's CXR (column
    ``xray_path``); if the path is empty or unreadable we emit zeros plus
    ``present=False`` so the model can gate the image contribution. There is
    NO in-process cache — at COMPASS scale the cache would dwarf RAM, and
    the OS page cache already handles repeated reads of the same file.

    When ``train=True`` we apply mild geometric and photometric augmentation
    (the rotations / flips / brightness jitter described in the manuscript
    Methods); validation and test passes always use the deterministic
    Resize + Normalize pipeline so val metrics are reproducible.
    """

    def __init__(self, img_size=518, img_data_dir=None, train=False,
                 rotation_deg=10.0, brightness=0.2, contrast=0.2,
                 hflip_prob=0.5):
        self.img_size = img_size
        self.train = bool(train)
        # Optional prefix prepended to relative xray_path values.
        self.img_data_dir = Path(img_data_dir) if img_data_dir else None
        normalize = transforms.Normalize(
            mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
        resize = transforms.Resize((img_size, img_size))
        if self.train:
            self.transform = transforms.Compose([
                resize,
                transforms.RandomHorizontalFlip(p=hflip_prob),
                transforms.RandomRotation(
                    degrees=rotation_deg,
                    interpolation=transforms.InterpolationMode.BILINEAR,
                ),
                transforms.ColorJitter(brightness=brightness, contrast=contrast),
                transforms.ToTensor(),
                normalize,
            ])
        else:
            self.transform = transforms.Compose([
                resize, transforms.ToTensor(), normalize,
            ])
        self._zero = torch.zeros(3, img_size, img_size, dtype=torch.float32)

    def _resolve(self, path):
        if not path:
            return None
        p = Path(path)
        if not p.is_absolute() and self.img_data_dir is not None:
            p = self.img_data_dir / p
        return p

    def load_image_with_presence(self, path):
        resolved = self._resolve(path)
        if resolved is None or not resolved.exists():
            return self._zero, False
        try:
            image = Image.open(resolved).convert('RGB')
            return self.transform(image), True
        except Exception as e:
            print(f"[CXR] failed to load {resolved}: {e}")
            return self._zero, False

# Columns that store per-patient arrays (1-D or 2-D). At setup time we
# pre-convert each of these from the parquet's nested-list-of-arrays
# representation into a single flat ndarray indexed by patient row, so
# __getitem__ is a plain array slice — no per-row np.asarray() on the hot
# path. This is the biggest single win for DataLoader throughput.
_ARRAY_COLS_2D = (
    'tokenized_category_feats', 'tokenized_float_feats',
    'category_feats', 'float_feats',
    'c_cls_labels', 'f_cls_labels', 'c_reg_labels',
)
_ARRAY_COLS_1D = ('valid_mask', 'time_index')


def _preload_arrays(df, cols_2d=_ARRAY_COLS_2D, cols_1d=_ARRAY_COLS_1D):
    """Materialize 1-D / 2-D array columns into contiguous ndarrays.

    The parquet round-trip stores each cell as an object-ndarray of inner
    ndarrays. For a 2-D column the cell shape is (n_feat,), where each
    element is a 1-D ndarray of length seq_len. We flatten that into a single
    (N_rows, n_feat, seq_len) ndarray once at setup so per-sample reads are
    O(1) slice views — eliminating ~6 np.asarray calls from every
    __getitem__ on the hot DataLoader path.
    """
    out = {}
    for c in cols_2d:
        if c not in df.columns:
            continue
        first = df[c].iloc[0]
        n_feat = len(first)
        seq_len = len(first[0])
        # Determine dtype from the first inner array; parquet preserves it.
        dtype = np.asarray(first[0]).dtype
        # Two-pass build via np.stack: first stack each row's inner arrays
        # (cheap C op on n_feat=~28 elements), then stack all rows.
        rows = [np.stack(r, axis=0) for r in df[c].values]
        out[c] = np.stack(rows, axis=0).astype(dtype, copy=False)
        assert out[c].shape == (len(df), n_feat, seq_len)
    for c in cols_1d:
        if c not in df.columns:
            continue
        first = df[c].iloc[0]
        dtype = np.asarray(first).dtype
        out[c] = np.stack([np.asarray(r) for r in df[c].values], axis=0).astype(dtype, copy=False)
    return out


@dataclass
class ChunkedOncoDataset(Dataset):
    data_chunks: list
    mode: str
    config: dict

    def __post_init__(self):
        self.df_paths = self.config['df_paths']
        self.chunk_lengths = [len(chunk) for chunk in self.data_chunks]
        self.cumulative_lengths = np.cumsum(self.chunk_lengths)
        self.total_length = self.cumulative_lengths[-1] if self.chunk_lengths else 0

        self.use_img_model = self.config.get('use_img_model', False)
        if self.use_img_model:
            # Augmentation only applies in the train split; val/test always
            # see the deterministic Resize+Normalize pipeline. Strength is
            # configurable so the augmentation can be turned off for ablations
            # by setting ``cxr_augment: false`` or by tuning the magnitudes.
            cxr_aug = self.config.get('cxr_augment', True)
            train_aug = cxr_aug and (self.mode == 'train')
            aug_cfg = self.config.get('cxr_augment_params', {})
            self.img_processor = CXRImageProcessor(
                img_size=self.config.get('img_size', 224),
                img_data_dir=self.config.get('img_data_dir'),
                train=train_aug,
                rotation_deg=aug_cfg.get('rotation_deg', 10.0),
                brightness=aug_cfg.get('brightness', 0.2),
                contrast=aug_cfg.get('contrast', 0.2),
                hflip_prob=aug_cfg.get('hflip_prob', 0.5),
            )

        # Cohort label source for the adversarial cohort discriminator.
        # Falls back to a single-cohort label (0) when the column is absent.
        self.cohort_col = self.config.get('cohort_col', 'cohort_id')

        # Patient-id column. The published demo data renames ``pid`` ->
        # ``demo_patient_id``; auto-detect so either schema loads unchanged.
        cols0 = set(self.data_chunks[0].columns) if self.data_chunks else set()
        self.pid_col = self.config.get(
            'pid_col', 'demo_patient_id' if 'demo_patient_id' in cols0 else 'pid')

        self._setup_config()

        # Pre-materialize array columns. Done after _setup_config so we can
        # gate on having data and skip empty chunks. Under Linux fork-mode
        # DataLoader workers, these arrays are shared (copy-on-write) with
        # the parent, so they cost RAM only once across all workers.
        self._preloaded = [_preload_arrays(chunk) for chunk in self.data_chunks]

    def _setup_config(self):
        self.dataframe_cols = set().union(*[set(c.columns) for c in self.data_chunks])
        self.seq_max_len = self.config['seq_max_len']
        self.config['cls_label_names'] = []
        self.config['reg_label_names'] = []
        self.config['n_cls'] = []

        if self.config.get('mode') == 'pretrain':
            if 'feat_info' not in self.config:
                raise ValueError("feat_info required for pretraining mode")
            feat_info = self.config['feat_info']
            self.cls_label_names = sorted(feat_info['category_cols'])
            self.reg_label_names = sorted(feat_info['float_cols'].keys())
            self.mean_std = np.array(
                [(feat_info['float_cols'][x]['mean'], feat_info['float_cols'][x]['std'])
                 for x in self.reg_label_names],
                dtype=np.float32,
            )
            self.config['cls_label_names'] = self.cls_label_names
            self.config['reg_label_names'] = self.reg_label_names
            self.config['mean_std'] = self.mean_std
            self.mask_ratio = self.config.get('mask_ratio', 0.5)
            return

        # Finetune mode: pull per-task labels from the first row's name lists.
        first_row = self.data_chunks[0].iloc[0]
        for label_col, name_col, n_cls in zip(
            self.config['cls_label_cols'],
            self.config['cls_label_name_cols'],
            self.config['cls_label_n_cls'],
        ):
            for name in first_row[name_col]:
                self.config['cls_label_names'].append(f'{label_col}_{name}')
                self.config['n_cls'].append(n_cls)
        for label_col, name_col in zip(
            self.config.get('reg_label_cols', []),
            self.config.get('reg_label_name_cols', []),
        ):
            for name in first_row[name_col]:
                self.config['reg_label_names'].append(f'{label_col}_{name}')
                self.config['n_cls'].append(1)

    def _runtime_slice(self, valid_mask: np.ndarray) -> slice:
        """Return the slice that keeps the most-recent ``seq_max_len`` valid
        visits for this sample.

        The preprocessed parquet packs valid visits at positions [0, n_valid)
        and right-pads to ``preprocessed_seq_max_len`` (usually 64). At
        runtime the model may be configured with a smaller ``seq_max_len``
        (e.g. 16); we then either:
          - shrink to ``[0, seq_max_len)`` for short patients (kept padding
            tail is harmless — masked by ``valid_mask``); or
          - shrink to ``[n_valid - seq_max_len, n_valid)`` for long patients,
            preserving the most recent visits (the clinically meaningful
            ones for diagnosis / prediction).

        Without this slice, GPT-2's positional embedding (sized to
        ``seq_max_len``) gets indexed up to the preprocessed sequence
        length and overflows.
        """
        n_valid = int(valid_mask.sum())
        L = valid_mask.shape[-1]
        target = self.seq_max_len
        if L <= target:
            return slice(0, L)
        if n_valid <= target:
            return slice(0, target)
        return slice(n_valid - target, n_valid)

    def read_col(self, chunk_idx: int, within_chunk_idx: int, _data, col: str):
        """Return the column value for a (chunk, row). Hits the preloaded
        contiguous ndarray for 1-D/2-D array columns and falls back to the
        per-row parquet cell for scalars (pid, cohort_id, xray_path …).

        Array columns are sliced along their last (sequence) axis by the
        runtime slice stashed on ``self._current_slice`` in ``__getitem__``,
        so every read returns an exactly ``seq_max_len``-long window.

        The unused ``_data`` arg is a vestige of the (now-removed) diskcache
        path; keeping it lets every caller stay one-line.
        """
        pre = self._preloaded[chunk_idx].get(col)
        if pre is not None:
            arr = pre[within_chunk_idx]
            slc = getattr(self, '_current_slice', None)
            if slc is not None and arr.ndim >= 1 and arr.shape[-1] > (slc.stop - slc.start):
                arr = arr[..., slc]
            return arr
        return self.data_chunks[chunk_idx].iloc[within_chunk_idx][col]

    def _get_cohort_id(self, chunk_idx, within_chunk_idx, data):
        if self.cohort_col in self.dataframe_cols:
            return int(self.read_col(chunk_idx, within_chunk_idx, data, self.cohort_col))
        return 0

    def _get_xray_path(self, chunk_idx, within_chunk_idx, data):
        col = self.config.get('xray_path_col', 'xray_path')
        if col in self.dataframe_cols:
            v = self.read_col(chunk_idx, within_chunk_idx, data, col)
            return v if isinstance(v, str) else ''
        return ''

    def _maybe_add_cxr(self, tensors, chunk_idx, within_chunk_idx, data):
        if not self.use_img_model:
            return
        path = self._get_xray_path(chunk_idx, within_chunk_idx, data)
        cxr_image, cxr_present = self.img_processor.load_image_with_presence(path)
        tensors['cxr_images'] = cxr_image
        tensors['cxr_present'] = torch.tensor(cxr_present, dtype=torch.bool)

    def _to_int64(self, arr):
        """numpy array of any shape (possibly nested list from parquet) → int64 tensor."""
        return torch.as_tensor(np.asarray(arr, dtype=np.int64))

    def read_sample(self, chunk_idx: int, within_chunk_idx: int, data: dict):
        tensors = {
            'cat_feats': self._to_int64(self.read_col(
                chunk_idx, within_chunk_idx, data, 'tokenized_category_feats')),
            'float_feats': self._to_int64(self.read_col(
                chunk_idx, within_chunk_idx, data, 'tokenized_float_feats')),
            'valid_mask': torch.as_tensor(
                np.asarray(self.read_col(chunk_idx, within_chunk_idx, data, 'valid_mask'), dtype=bool)),
            'time_index': self._to_int64(self.read_col(
                chunk_idx, within_chunk_idx, data, 'time_index')),
            'cohort_id': torch.tensor(
                self._get_cohort_id(chunk_idx, within_chunk_idx, data), dtype=torch.long),
            # Default: EHR is always present (we always have some lab data per
            # patient in COMPASS). Modality-dropout in training_step or
            # inference-mode configs can override this per sample.
            'ehr_present': torch.tensor(True, dtype=torch.bool),
        }
        self._maybe_add_cxr(tensors, chunk_idx, within_chunk_idx, data)
        return tensors

    def read_sample_label_pretrain(self, chunk_idx: int, within_chunk_idx: int, data: dict):
        """Feature-level masking for pretraining.

        Vectorised: per timestep with ≥1 valid feature, ~``mask_ratio`` of those
        features are randomly selected; their input tokens are zeroed to -1
        (the "missing" sentinel) and the matching positions in the output
        tensors are filled with the reconstruction targets.
        """
        cat_tok = np.asarray(self.read_col(
            chunk_idx, within_chunk_idx, data, 'tokenized_category_feats'), dtype=np.int64)
        float_tok = np.asarray(self.read_col(
            chunk_idx, within_chunk_idx, data, 'tokenized_float_feats'), dtype=np.int64)
        valid_mask = np.asarray(self.read_col(
            chunk_idx, within_chunk_idx, data, 'valid_mask'), dtype=bool)
        orig_cat = np.asarray(self.read_col(
            chunk_idx, within_chunk_idx, data, 'category_feats'), dtype=np.int64)
        orig_float = np.asarray(self.read_col(
            chunk_idx, within_chunk_idx, data, 'float_feats'), dtype=np.float32)

        n_cat, L = cat_tok.shape
        n_float = float_tok.shape[0]

        # Per-(feature, timestep) "present" matrices.
        cat_present = (cat_tok != -1) & valid_mask[None, :]
        float_present = np.isfinite(orig_float) & valid_mask[None, :] & (float_tok != -1)

        # Stack cat- and float-feature presence into one (n_cat + n_float, L) grid
        # so we can sample a uniform fraction across modalities.
        present = np.concatenate([cat_present, float_present], axis=0)
        keep_prob = 1.0 - self.mask_ratio
        rand = np.random.random(present.shape)
        # `output` selects features to RECONSTRUCT (≈ mask_ratio of present ones).
        is_output = present & (rand >= keep_prob)
        # `input` retains the rest of the present features for the encoder.
        is_input = present & ~is_output

        # Guarantee ≥1 reconstructed feature per timestep that has any valid feature,
        # mirroring the original behaviour.
        has_present_t = present.any(axis=0)
        has_output_t = is_output.any(axis=0)
        force_t = np.where(has_present_t & ~has_output_t)[0]
        if force_t.size:
            for t in force_t:
                cand = np.where(present[:, t])[0]
                pick = np.random.choice(cand)
                is_output[pick, t] = True
                is_input[pick, t] = False

        cat_in_mask, float_in_mask = is_input[:n_cat], is_input[n_cat:]
        cat_out_mask, float_out_mask = is_output[:n_cat], is_output[n_cat:]

        # Build inputs: blank out the to-be-reconstructed slots.
        cat_input = np.where(cat_out_mask, -1, cat_tok).astype(np.int64)
        float_input = np.where(float_out_mask, -1, float_tok).astype(np.int64)

        # Build targets.
        cat_target = np.where(cat_out_mask, orig_cat, 0).astype(np.int64)

        mean = self.mean_std[:, 0].reshape(-1, 1)
        std = self.mean_std[:, 1].reshape(-1, 1)
        # Avoid normalising garbage where the original value is missing.
        safe_float = np.where(np.isfinite(orig_float), orig_float, 0.0)
        float_normed = (safe_float - mean) / (std + 1e-10)
        float_target = np.where(float_out_mask, float_normed, 0.0).astype(np.float32)

        sample = {
            'cat_feats': torch.from_numpy(cat_input),
            'cat_valid_mask': torch.from_numpy(cat_in_mask),
            'float_feats': torch.from_numpy(float_input),
            'float_valid_mask': torch.from_numpy(float_in_mask),
            'cohort_id': torch.tensor(
                self._get_cohort_id(chunk_idx, within_chunk_idx, data), dtype=torch.long),
            'ehr_present': torch.tensor(True, dtype=torch.bool),
        }
        self._maybe_add_cxr(sample, chunk_idx, within_chunk_idx, data)

        label = {
            'cat_feats': torch.from_numpy(cat_target),
            'cat_valid_mask': torch.from_numpy(cat_out_mask),
            'float_feats': torch.from_numpy(float_target),
            'float_valid_mask': torch.from_numpy(float_out_mask),
        }
        return sample, label

    def read_label(self, chunk_idx: int, within_chunk_idx: int, data: dict):
        """Build the per-patient label dict for finetuning.

        Always returns dense ``values``/``masks`` tensors (zero-row when a
        modality has no labels), so downstream code can index them
        unconditionally.
        """
        if self.config.get('mode') == 'pretrain':
            return None

        time_index = np.asarray(self.read_col(
            chunk_idx, within_chunk_idx, data, 'time_index'), dtype=np.int64)
        seq_len = time_index.shape[0]

        def _stack(value_cols, norm_info=None):
            """Concatenate every per-task row into flat lists; optionally normalise."""
            values, masks = [], []
            for col in value_cols:
                v = np.asarray(self.read_col(chunk_idx, within_chunk_idx, data, col))
                if v.ndim == 1:
                    v = v[None, :]
                v = v.astype(np.float32)
                for row in v:
                    if norm_info is not None and len(values) < len(norm_info):
                        mean, std = norm_info[len(values)]
                        row = (row - mean) / (std + 1e-10)
                    nan = np.isnan(row) | (row == -1)
                    values.append(np.where(nan, -1, row))
                    masks.append(~nan)
            return values, masks

        cls_values, cls_masks = _stack(self.config['cls_label_cols'])
        reg_values, reg_masks = _stack(
            self.config.get('reg_label_cols', []),
            norm_info=self.config.get('reg_label_info'),
        )

        def _to_tensors(values, masks, *, long):
            if values:
                v = np.stack(values, axis=0)
                m = np.stack(masks, axis=0)
            else:
                v = np.zeros((0, seq_len), dtype=np.float32 if not long else np.int64)
                m = np.zeros((0, seq_len), dtype=bool)
            if long:
                v = v.astype(np.int64, copy=False)
                v[v == -1] = 0
                return torch.from_numpy(v), torch.from_numpy(m)
            return torch.from_numpy(v.astype(np.float32, copy=False)), torch.from_numpy(m)

        cls_v, cls_m = _to_tensors(cls_values, cls_masks, long=True)
        reg_v, reg_m = _to_tensors(reg_values, reg_masks, long=False)

        return {
            'cls': {'values': cls_v, 'masks': cls_m},
            'reg': {'values': reg_v, 'masks': reg_m},
            'time_index': torch.from_numpy(time_index),
        }

    def __len__(self):
        return self.total_length

    def __getitem__(self, idx):
        chunk_idx = np.searchsorted(self.cumulative_lengths, idx, side='right')
        within_chunk_idx = (
            idx - self.cumulative_lengths[chunk_idx - 1] if chunk_idx > 0 else idx
        )
        # Compute the per-sample runtime slice from the raw valid_mask BEFORE
        # any read_col call applies it (otherwise we'd recurse infinitely).
        raw_valid = self._preloaded[chunk_idx]['valid_mask'][within_chunk_idx]
        self._current_slice = self._runtime_slice(raw_valid)
        data = None  # diskcache slot vestige — see read_col docstring.

        if self.config.get('mode') == 'pretrain':
            sample, label = self.read_sample_label_pretrain(chunk_idx, within_chunk_idx, data)
            return {
                'pid': self.read_col(chunk_idx, within_chunk_idx, data, self.pid_col),
                'data': sample,
                'label': label,
            }
        return {
            'pid': self.read_col(chunk_idx, within_chunk_idx, data, self.pid_col),
            'data': self.read_sample(chunk_idx, within_chunk_idx, data),
            'label': self.read_label(chunk_idx, within_chunk_idx, data),
        }

class OncoDataModule(pl.LightningDataModule):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.df_paths = config.get('df_paths', [])
        self.dataset_col = config.get('dataset_col', None)
        self.batch_size = config.get('batch_size', None)
        self.train_folds = config.get('train_folds', None)
        self.valid_folds = config.get('valid_folds', None)
        self.test_folds = config.get('test_folds', None)

    def setup(self, stage=None):
        if not isinstance(self.df_paths, list):
            self.df_paths = [self.df_paths]
        if isinstance(self.df_paths[0], pd.DataFrame):
            self._setup_from_dataframe()
        else:
            self._setup_from_parquet()

    def _setup_from_dataframe(self):
        df = pd.concat(sorted(self.df_paths), axis=0, ignore_index=True)
        df = optimize_dtypes(df)
        
        
        df_train = df[df[self.dataset_col].isin(self.train_folds)].reset_index(drop=True)
        df_valid = df[df[self.dataset_col].isin(self.valid_folds)].reset_index(drop=True)
        df_test = df[df[self.dataset_col].isin(self.test_folds)].reset_index(drop=True)
        
        
        self.config['df_paths'] = self.df_paths  
        
        
        self.ds_train = ChunkedOncoDataset([df_train], 'train', self.config)
        self.ds_valid = ChunkedOncoDataset([df_valid], 'valid', self.config)
        self.ds_test = ChunkedOncoDataset([df_test], 'test', self.config)

    def _setup_from_parquet(self):
        dfs = []
        for path in sorted(self.df_paths):
            metadata_path = Path(path) / 'metadata.parquet'
            df = load_parquet(metadata_path)
            dfs.append(df)
        
        
        merged_df = pd.concat(dfs, axis=0, ignore_index=True)
        merged_df = optimize_dtypes(merged_df)
        
        
        df_train = merged_df[merged_df[self.dataset_col].isin(self.train_folds)].reset_index(drop=True)
        df_valid = merged_df[merged_df[self.dataset_col].isin(self.valid_folds)].reset_index(drop=True)
        df_test = merged_df[merged_df[self.dataset_col].isin(self.test_folds)].reset_index(drop=True)
        
        
        self.config['df_paths'] = self.df_paths
        
        
        self.ds_train = ChunkedOncoDataset([df_train], 'train', self.config)
        self.ds_valid = ChunkedOncoDataset([df_valid], 'valid', self.config)
        
        if self.config.get('test_df', '') != '':
            df_test = pd.read_csv(self.config['test_df'])
            df_test = optimize_dtypes(df_test)
            self.ds_test = ChunkedOncoDataset([df_test], 'test', self.config)
        else:
            self.ds_test = ChunkedOncoDataset([df_test], 'test', self.config)

    def _make_loader(self, dataset, shuffle=False):
        num_workers = 0 if sys.platform == 'win32' else 8
        kwargs = dict(
            batch_size=self.batch_size,
            num_workers=num_workers,
            pin_memory=True,
            shuffle=shuffle,
        )
        if num_workers > 0:
            kwargs.update(
                persistent_workers=True,
                prefetch_factor=8,
                multiprocessing_context=_MP_CONTEXT,
            )
        return DataLoader(dataset, **kwargs)

    def train_dataloader(self):
        return self._make_loader(self.ds_train, shuffle=True)

    def val_dataloader(self):
        return self._make_loader(self.ds_valid, shuffle=False)

    def test_dataloader(self):
        return self._make_loader(self.ds_test, shuffle=False)

    def teardown(self, stage=None):
        gc.collect()