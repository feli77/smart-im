"""Rebuild the bundled character MLP from the authored offline corpus.

Run: OPENBLAS_NUM_THREADS=1 .venv/bin/python scripts/train_tiny_lm.py
No third-party training data, torch, network, or model download is needed.
The corpus is deliberately tiny: this is a working inference baseline, not a
general-purpose pretrained Chinese model. Fixed seed, Adam, 4-char context.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src" / "smart_im" / "data"


def train(epochs: int, output: Path, seed: int = 42) -> dict[str, float | int]:
    lines = [
        line.strip()
        for line in (DATA / "corpus.txt").read_text("utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    # 0 is padding/start-of-sentence; 1 is unknown. Both are plain strings,
    # never object arrays: np.load(..., allow_pickle=False) is sufficient.
    vocab = ["<PAD>", "<UNK>"] + sorted(set("".join(lines)))
    ids = {char: i for i, char in enumerate(vocab)}
    context_size, embedding_size, hidden_size = 4, 12, 48
    inputs, targets = [], []
    for line in lines:
        history = [0] * context_size
        for char in line:
            inputs.append(history[-context_size:])
            targets.append(ids[char])
            history.append(ids[char])
    x = np.asarray(inputs, dtype=np.int32)
    y = np.asarray(targets, dtype=np.int32)
    rng = np.random.default_rng(seed)
    n, width = len(vocab), context_size * embedding_size
    params = {
        "embedding": rng.normal(0, 0.15, (n, embedding_size)).astype(np.float32),
        "w1": rng.normal(0, 1 / width**0.5, (width, hidden_size)).astype(np.float32),
        "b1": np.zeros(hidden_size, dtype=np.float32),
        "w2": rng.normal(0, 1 / hidden_size**0.5, (hidden_size, n)).astype(np.float32),
        "b2": np.zeros(n, dtype=np.float32),
    }
    first = {key: np.zeros_like(value) for key, value in params.items()}
    second = {key: np.zeros_like(value) for key, value in params.items()}
    step = 0
    loss = 0.0
    started = time.perf_counter()
    for epoch in range(epochs):
        permutation = rng.permutation(len(x))
        loss = 0.0
        for offset in range(0, len(x), 128):
            indices = permutation[offset : offset + 128]
            batch_x, batch_y = x[indices], y[indices]
            embedded = params["embedding"][batch_x].reshape(len(indices), -1)
            hidden = np.tanh(embedded @ params["w1"] + params["b1"])
            logits = hidden @ params["w2"] + params["b2"]
            logits -= logits.max(axis=1, keepdims=True)
            probability = np.exp(logits)
            probability /= probability.sum(axis=1, keepdims=True)
            loss -= float(np.log(probability[np.arange(len(indices)), batch_y] + 1e-10).sum())
            probability[np.arange(len(indices)), batch_y] -= 1
            probability /= len(indices)
            hidden_grad = (probability @ params["w2"].T) * (1 - hidden**2)
            embedding_grad = (hidden_grad @ params["w1"].T).reshape(
                len(indices), context_size, embedding_size
            )
            grads = {
                "embedding": np.zeros_like(params["embedding"]),
                "w1": embedded.T @ hidden_grad,
                "b1": hidden_grad.sum(axis=0),
                "w2": hidden.T @ probability,
                "b2": probability.sum(axis=0),
            }
            np.add.at(grads["embedding"], batch_x, embedding_grad)
            step += 1
            rate = 0.012 * (0.3 + 0.7 * (1 - epoch / max(1, epochs)))
            for key, value in params.items():
                first[key] *= 0.9
                first[key] += 0.1 * grads[key]
                second[key] *= 0.999
                second[key] += 0.001 * grads[key] ** 2
                mean = first[key] / (1 - 0.9**step)
                variance = second[key] / (1 - 0.999**step)
                value -= rate * mean / (np.sqrt(variance) + 1e-8)
        if epoch == 0 or (epoch + 1) % 20 == 0 or epoch + 1 == epochs:
            print(f"epoch={epoch + 1:3d} training_nll={loss / len(x):.4f}", flush=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output,
        **params,
        vocab=np.asarray(vocab),
        context_size=np.asarray(context_size, dtype=np.int32),
        seed=np.asarray(seed, dtype=np.int32),
        epochs=np.asarray(epochs, dtype=np.int32),
        format_version=np.asarray(1, dtype=np.int32),
    )
    return {
        "characters": len(x),
        "vocabulary": n,
        "parameters": sum(p.size for p in params.values()),
        "training_nll": loss / len(x),
        "seconds": time.perf_counter() - started,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--output", type=Path, default=DATA / "tiny_lm.npz")
    args = parser.parse_args()
    if not 1 <= args.epochs <= 1000:
        parser.error("epochs must be between 1 and 1000")
    print(train(args.epochs, args.output))
