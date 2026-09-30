"""Offline candidate scoring with a tiny character MLP and corpus n-grams.

The bundled MLP predicts a character from the previous four characters. Its
probabilities are interpolated with corpus n-grams to score Rime candidates.
This is a small demonstration vocabulary, not general Chinese understanding.
"""

from __future__ import annotations

import threading
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

import numpy as np

DATA = Path(__file__).parent / "data"


class LocalLanguageModel:
    name = "tiny-char-mlp + ngram (offline)"
    MAX_CACHE = 512
    MAX_TEXT = 64

    def __init__(self, model_path: str | Path | None = None, corpus_path: str | Path | None = None):
        path = Path(model_path) if model_path is not None else DATA / "tiny_lm.npz"
        if not path.is_file():
            raise FileNotFoundError(
                f"Local weights missing: {path}. Run scripts/train_tiny_lm.py first."
            )
        with np.load(path, allow_pickle=False) as archive:
            if int(archive["format_version"]) != 1:
                raise ValueError("Unsupported local model format")
            self.vocab = archive["vocab"].tolist()
            self.context_size = int(archive["context_size"])
            self.embedding = archive["embedding"].astype(np.float32)
            self.w1 = archive["w1"].astype(np.float32)
            self.b1 = archive["b1"].astype(np.float32)
            self.w2 = archive["w2"].astype(np.float32)
            self.b2 = archive["b2"].astype(np.float32)
        n = len(self.vocab)
        if (
            not 1 <= self.context_size <= 16
            or self.embedding.ndim != 2
            or self.embedding.shape[0] != n
            or self.w1.ndim != 2
            or self.w1.shape[0] != self.context_size * self.embedding.shape[1]
            or self.b1.shape != (self.w1.shape[1],)
            or self.w2.shape != (self.w1.shape[1], n)
            or self.b2.shape != (n,)
        ):
            raise ValueError("Invalid local model tensor shapes")
        self.ids = {char: index for index, char in enumerate(self.vocab)}
        self.parameter_count = sum(
            value.size for value in (self.embedding, self.w1, self.b1, self.w2, self.b2)
        )
        self._lock = threading.RLock()
        self._scores: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._distributions: OrderedDict[str, np.ndarray] = OrderedDict()
        corpus = Path(corpus_path) if corpus_path is not None else DATA / "corpus.txt"
        lines = [
            line.strip()
            for line in corpus.read_text("utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        self._ngrams: dict[str, Counter[int]] = defaultdict(Counter)
        for line in lines:
            for index, char in enumerate(line):
                for width in range(min(4, index) + 1):
                    self._ngrams[line[index - width : index]][self.ids.get(char, 1)] += 1
        counts = self._ngrams[""]
        self._unigram = np.full(n, 0.05, dtype=np.float32)
        for token, count in counts.items():
            self._unigram[token] += count
        self._unigram /= self._unigram.sum()

    def _distribution(self, context: str) -> np.ndarray:
        context = context[-self.context_size :]
        with self._lock:
            cached = self._distributions.get(context)
            if cached is not None:
                self._distributions.move_to_end(context)
                return cached
        tokens = [0] * max(0, self.context_size - len(context))
        tokens += [self.ids.get(char, 1) for char in context]
        embedded = self.embedding[tokens].reshape(-1)
        # Explicit einsum avoids a heavyweight threaded BLAS dispatch for these
        # tiny matrices; latency matters more than large-matrix throughput here.
        hidden = np.tanh(np.einsum("i,ij->j", embedded, self.w1) + self.b1)
        logits = np.einsum("i,ij->j", hidden, self.w2) + self.b2
        neural = np.exp(logits - logits.max())
        neural /= neural.sum()
        statistical = self._unigram
        for width in range(min(4, len(context)), 0, -1):
            counts = self._ngrams.get(context[-width:])
            if counts:
                statistical = self._unigram * 0.5
                for token, count in counts.items():
                    statistical[token] += count
                statistical /= statistical.sum()
                break
        probability = np.maximum(0.45 * neural + 0.55 * statistical, 1e-9)
        with self._lock:
            self._distributions[context] = probability
            if len(self._distributions) > self.MAX_CACHE:
                self._distributions.popitem(last=False)
        return probability

    def score(self, context: str, text: str) -> float:
        if not text:
            return 0.0
        context, text = context[-self.context_size :], text[: self.MAX_TEXT]
        key = (context, text)
        with self._lock:
            if key in self._scores:
                self._scores.move_to_end(key)
                return self._scores[key]
        total = 0.0
        history = context
        for char in text:
            probability = self._distribution(history)
            total += float(np.log(probability[self.ids.get(char, 1)]))
            history = (history + char)[-self.context_size :]
        result = total / len(text)
        with self._lock:
            self._scores[key] = result
            if len(self._scores) > self.MAX_CACHE:
                self._scores.popitem(last=False)
        return result
