"""ONNX int8 sentence embedder. multilingual-e5-small, 384 dims.

Rules.md 2.1: no PyTorch at request time. Rules.md 2.2: the session is created
once at startup with an explicit thread count, never per request.

Three details here are easy to get wrong and all three silently degrade recall
rather than raising, which is why each is called out:

  1. e5 REQUIRES the "query: " and "passage: " prefixes. Omitting them costs a
     large amount of recall and nothing anywhere will tell you. Architecture.md 3.2.
  2. Pooling is MASKED MEAN over last_hidden_state, not CLS. Taking token 0 of an
     e5 model produces plausible-looking vectors that retrieve badly.
  3. Vectors are L2-normalised here, so the index can use inner product directly
     and skip hnswlib's internal renormalisation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Iterable, Literal

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

MAX_TOKENS: Final[int] = 512
EMBED_DIM: Final[int] = 384

QUERY_PREFIX: Final[str] = "query: "
PASSAGE_PREFIX: Final[str] = "passage: "

TextKind = Literal["query", "passage"]


class Embedder:
    """Wraps one ONNX session and its tokenizer.

    `threads` is 2 on the hot path per Rules.md 2.2 - the ONNX Runtime default
    oversubscribes on a model this small and measures slower. The offline index
    build passes a higher count, because there the workload is throughput-bound
    rather than latency-bound.
    """

    def __init__(
        self,
        model_path: Path,
        tokenizer_path: Path,
        threads: int = 2,
        max_tokens: int = MAX_TOKENS,
    ) -> None:
        if not model_path.exists():
            raise FileNotFoundError(
                f"{model_path} missing. Run scripts/03_export_onnx.py first."
            )

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self.session = ort.InferenceSession(
            str(model_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        # XLM-R based models take input_ids + attention_mask; some exports also
        # want token_type_ids. Feed exactly what this graph declares.
        self._input_names = {i.name for i in self.session.get_inputs()}

        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.enable_truncation(max_length=max_tokens)
        self.tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        self.max_tokens = max_tokens
        self.model_path = model_path

    # -- tokenization -------------------------------------------------------

    def token_count(self, text: str) -> int:
        """Token length without special tokens. Used by the chunkers so that
        chunk sizes are measured in real tokens rather than whitespace words."""
        return len(self.tokenizer.encode(text, add_special_tokens=False).ids)

    def token_ids(self, text: str) -> list[int]:
        return self.tokenizer.encode(text, add_special_tokens=False).ids

    def decode(self, ids: list[int]) -> str:
        return self.tokenizer.decode(ids)

    # -- embedding ----------------------------------------------------------

    @staticmethod
    def _prefix(kind: TextKind) -> str:
        return QUERY_PREFIX if kind == "query" else PASSAGE_PREFIX

    def encode(self, texts: Iterable[str], kind: TextKind) -> np.ndarray:
        """Embed a batch. Returns float32 (n, 384), L2-normalised."""
        prefix = self._prefix(kind)
        prefixed = [prefix + t for t in texts]
        if not prefixed:
            return np.zeros((0, EMBED_DIM), dtype=np.float32)

        encodings = self.tokenizer.encode_batch(prefixed)
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)

        feeds: dict[str, np.ndarray] = {"input_ids": input_ids}
        if "attention_mask" in self._input_names:
            feeds["attention_mask"] = attention_mask
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.zeros_like(input_ids)

        last_hidden: np.ndarray = self.session.run(None, feeds)[0]  # (n, seq, 384)
        return self._mean_pool(last_hidden, attention_mask)

    def encode_one(self, text: str, kind: TextKind) -> np.ndarray:
        """Single-text path for the hot path. Returns (384,)."""
        vec: np.ndarray = self.encode([text], kind)[0]
        return vec

    @staticmethod
    def _mean_pool(last_hidden: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
        """Masked mean pool, then L2 normalise.

        Padding tokens must be excluded from the mean. Including them shifts every
        vector toward the pad embedding by an amount that varies with sequence
        length, which corrupts short texts worst - exactly our corpus.
        """
        mask = attention_mask.astype(np.float32)[..., None]  # (n, seq, 1)
        summed = (last_hidden * mask).sum(axis=1)
        counts = np.clip(mask.sum(axis=1), 1e-9, None)
        pooled = summed / counts
        norms = np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
        normalized: np.ndarray = (pooled / norms).astype(np.float32)
        return normalized
