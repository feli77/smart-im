"""Offline language-model boundary and a genuinely neural, tiny CPU baseline.

The bundled MLP predicts a character from the previous four characters. Its
probabilities are interpolated with corpus n-grams. Corpus suffix retrieval
proposes continuations; model likelihood ranks them. This is intentionally a
small demonstration vocabulary, not general Chinese semantic understanding.
"""

from __future__ import annotations

import re
import threading
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

import numpy as np

from smart_im.types import Candidate

DATA = Path(__file__).parent / "data"
_BOUNDARY = re.compile(r"[，。！？；、\n,.!?;]")


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
        self._lines = [
            line.strip()
            for line in corpus.read_text("utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        self._ngrams: dict[str, Counter[int]] = defaultdict(Counter)
        self._continuations: dict[str, Counter[str]] = defaultdict(Counter)
        for line in self._lines:
            for index, char in enumerate(line):
                for width in range(min(4, index) + 1):
                    self._ngrams[line[index - width : index]][self.ids.get(char, 1)] += 1
                if index:
                    continuation = _BOUNDARY.split(line[index:], maxsplit=1)[0][:12]
                    if continuation:
                        for width in range(1, min(12, index) + 1):
                            self._continuations[line[index - width : index]][continuation] += 1
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

    def predict(self, context: str, limit: int = 5) -> list[Candidate]:
        if not context or limit <= 0:
            return []
        context = context[-64:]
        proposals: dict[str, tuple[int, int]] = {}
        # Use only the longest available matching suffix. Filling the list from
        # weaker suffixes can produce nonsense such as 请查收 + 到你的邮件.
        for width in range(min(12, len(context)), 0, -1):
            matches = self._continuations.get(context[-width:])
            if not matches:
                continue
            for text, count in matches.most_common(20):
                proposals.setdefault(text, (width, count))
            break
        candidates = [
            Candidate(
                text=text,
                frequency=count,
                source="model",
                score=self.score(context, text) + 0.30 * width,
                annotation="本地模型续写",
            )
            for text, (width, count) in proposals.items()
        ]
        return sorted(candidates, key=lambda item: (-item.score, -item.frequency, item.text))[
            : min(limit, 20)
        ]


class GGUFModel:
    """Optional local GGUF adapter; never resolves a Hub ID or downloads files.

    A compatible base/autocomplete model should be supplied by the user. The
    bundled tiny model remains available without this optional dependency.
    """

    def __init__(self, model_path: str | Path, context_size: int = 256):
        path = Path(model_path).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() != ".gguf":
            raise ValueError("GGUFModel requires an existing local .gguf file")
        try:
            from llama_cpp import Llama
        except ImportError as error:
            raise RuntimeError("Install the optional smart-im[gguf] dependency first") from error
        self._context_size = min(2048, max(128, context_size))
        self._lock = threading.RLock()
        self._model = Llama(
            model_path=str(path),
            n_ctx=self._context_size,
            n_threads=2,
            logits_all=True,
            verbose=False,
        )
        self._scores: OrderedDict[tuple[str, str], float] = OrderedDict()
        self.name = f"GGUF local: {path.name}"

    def score(self, context: str, text: str) -> float:
        if not text:
            return 0.0
        # Bound both tokenization input and evaluation length.
        context, text = context[-512:], text[:64]
        key = (context, text)
        with self._lock:
            if key in self._scores:
                self._scores.move_to_end(key)
                return self._scores[key]
            prefix = self._model.tokenize(context.encode("utf-8"), add_bos=True)
            continuation = self._model.tokenize(text.encode("utf-8"), add_bos=False)
            if not continuation:
                return 0.0
            continuation = continuation[: self._context_size // 2]
            prefix = prefix[-(self._context_size - len(continuation)) :]
            if not prefix:
                prefix = [self._model.token_bos()]
            self._model.reset()
            self._model.eval(prefix + continuation)
            total = 0.0
            for index, token in enumerate(continuation):
                logits = np.asarray(self._model.scores[len(prefix) + index - 1], dtype=np.float64)
                shifted = logits - logits.max()
                total += float(shifted[token] - np.log(np.exp(shifted).sum()))
            result = total / len(continuation)
            self._scores[key] = result
            if len(self._scores) > 128:
                self._scores.popitem(last=False)
            return result

    def predict(self, context: str, limit: int = 5) -> list[Candidate]:
        if not context or limit <= 0:
            return []
        with self._lock:
            tokens = self._model.tokenize(context[-512:].encode("utf-8"), add_bos=True)
            result = self._model.create_completion(
                prompt=tokens[-(self._context_size - 32) :],
                max_tokens=24,
                temperature=0.0,
                stop=["\n", "。", "！", "？"],
                echo=False,
            )
            text = result["choices"][0]["text"].strip()[:24]
        if not text:
            return []
        return [
            Candidate(
                text=text,
                source="model",
                score=self.score(context, text),
                annotation="本地 GGUF 续写",
            )
        ]
