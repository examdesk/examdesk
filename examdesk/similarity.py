"""Semantic similarity of two questions, using a quantized all-MiniLM-L6-v2 (23 MB ONNX).

The model is fetched from Hugging Face on first use; deployments run `download_model`.
"""

from collections.abc import Sequence
from functools import cache, lru_cache

import numpy as np
import onnxruntime
from django.conf import settings
from huggingface_hub import hf_hub_download
from numpy.typing import NDArray
from tokenizers import Tokenizer


def download() -> list[str]:
    """Paths of the model files, downloading them if not cached."""
    return [
        hf_hub_download(
            settings.EXAMDESK_MODEL_REPO, file, revision=settings.EXAMDESK_MODEL_REVISION
        )
        for file in settings.EXAMDESK_MODEL_FILES
    ]


@cache
def _model() -> tuple[onnxruntime.InferenceSession, Tokenizer]:
    model, tokenizer_file = download()
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = 1  # short inputs; leave the cores to other workers
    tokenizer = Tokenizer.from_file(tokenizer_file)
    tokenizer.no_padding()  # tokenizer.json pads to 128, which would skew mean pooling
    tokenizer.enable_truncation(settings.EXAMDESK_MODEL_MAX_TOKENS)
    return onnxruntime.InferenceSession(model, options), tokenizer


@lru_cache(maxsize=4096)
def _embed(text: str) -> NDArray[np.float32]:
    """Mean-pooled, normalized sentence embedding."""
    session, tokenizer = _model()
    ids = np.array([tokenizer.encode(text).ids])
    feed = {
        "input_ids": ids,
        "attention_mask": np.ones_like(ids),
        "token_type_ids": np.zeros_like(ids),
    }
    vector: NDArray[np.float32] = session.run(None, feed)[0][0].mean(axis=0)
    return vector / np.linalg.norm(vector)


def similarity(a: str, b: str) -> float:
    """Cosine similarity clamped to [0, 1]; 0 if either text is blank."""
    if not (a.strip() and b.strip()):
        return 0.0
    # Past this the tokenizer truncates anyway; cutting first bounds the cache keys.
    chars = 8 * settings.EXAMDESK_MODEL_MAX_TOKENS
    return max(0.0, float(_embed(a[:chars]) @ _embed(b[:chars])))


def best_similarity(texts: Sequence[str], others: Sequence[str]) -> float:
    """The highest similarity between any of `texts` and any of `others`."""
    return max((similarity(a, b) for a in texts for b in others), default=0.0)
