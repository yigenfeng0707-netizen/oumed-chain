"""Tiny set of helpers used by the training entry points and the dataset.

Heavy optional dependencies that earlier lived here (cv2, matplotlib,
statsmodels, the full `sklearn.metrics` namespace, pickle helpers) were
removed because none of the modules in this package actually use them.
"""
from __future__ import annotations

import json
import os
import random
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import torch
from tqdm import tqdm


# ---------------------------------------------------------------------------
# Reproducibility — applied once at import time so every Lightning rank starts
# from a known seed without an explicit call.
# ---------------------------------------------------------------------------

def set_seed(seed: int) -> None:
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    np.random.seed(seed)
    random.seed(seed)


torch.set_float32_matmul_precision('medium')
set_seed(1)


# ---------------------------------------------------------------------------
# JSON / Parquet IO
# ---------------------------------------------------------------------------

def json_load(path: str | os.PathLike):
    with open(path, 'r', encoding='utf8') as f:
        return json.load(f)


def load_parquet(path: str | os.PathLike, reset_index: bool = True) -> pd.DataFrame:
    """Round-trip the array-encoded parquet written by `preprocess_compass`.

    2-D feature arrays are persisted as nested lists of lists in the parquet
    cells; this helper restores each cell to a 1-D object-ndarray of 1-D
    ndarrays so the dataset's preload step can stack them into contiguous
    (N, n_feat, seq_len) blocks.
    """
    pf = pq.ParquetFile(path)
    chunks = [batch.to_pandas() for batch in pf.iter_batches(batch_size=100_000)]
    df = pd.concat(chunks, axis=0)
    for col in tqdm(df.columns, desc=f'load_parquet({Path(path).name})'):
        cell = df[col].iloc[0]
        if isinstance(cell, np.ndarray) and cell.size > 0 and isinstance(cell[0], np.ndarray):
            df[col] = [np.array(list(x)) for x in df[col]]
    if reset_index:
        df = df.reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Lightning logger version discovery
# ---------------------------------------------------------------------------

def load_ckpt(path: str | os.PathLike, map_location: str = 'cpu') -> dict:
    """Load a Lightning checkpoint with the *safe* pickle loader.

    Always uses ``weights_only=True`` (which restricts the unpickler to a
    short allow-list of safe types); we *never* fall back to the
    arbitrary-code-execution loader. Lightning ckpts carry a few non-tensor
    objects in their ``hyper_parameters`` block — the union of those across
    the project is registered with ``torch.serialization.safe_globals``
    below, so the safe loader succeeds without weakening the guarantee.

    If a future ckpt fails to load because it contains a type that isn't
    yet in the allowlist, ``torch.load`` raises ``UnpicklingError`` naming
    the offending class — add it to ``_SAFE_GLOBALS`` rather than disabling
    ``weights_only``.
    """
    # Import here to avoid forcing transformers at module import time for
    # callers that don't load checkpoints.
    from transformers import GPT2Config, BertConfig
    safe_globals = [GPT2Config, BertConfig]
    with torch.serialization.safe_globals(safe_globals):
        return torch.load(path, map_location=map_location, weights_only=True)


def get_max_version(root_dir: str | os.PathLike) -> int:
    """Highest ``version_<n>`` directory under ``root_dir`` (or 0)."""
    if not os.path.isdir(root_dir):
        print(f"Missing logger folder: {root_dir}")
        return 0
    versions = [
        int(d.split('_')[1])
        for d in os.listdir(root_dir)
        if d.startswith('version_') and os.path.isdir(os.path.join(root_dir, d))
    ]
    return max(versions) if versions else 0
