"""Text normalization / answer extraction / simple NLP utilities.

Pure-python implementations (no external deps) so rule metrics work in every
environment; heavier libs (sacrebleu, rouge-score, sentence-transformers) are
used automatically when installed.
"""

from __future__ import annotations

import hashlib
import math
import random
import re
from collections import Counter

_ARTICLES_EN = re.compile(r"\b(a|an|the)\b", re.IGNORECASE)
_PUNCT_WS = re.compile(r"[\s" + re.escape("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~。！？，、；：（）【】《》“”‘’—…·") + r"]+")
_NUMBER_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?")
BOXED_RE = re.compile(r"\\boxed\{([^{}]+)\}")
FINAL_ANSWER_RE = re.compile(r"####\s*(.+)")


def normalize_answer(text: str, *, lower: bool = True, strip_articles: bool = True) -> str:
    """Lowercase, remove punctuation/articles/whitespace - SQuAD-style normalization."""
    if text is None:
        return ""
    out = str(text)
    if lower:
        out = out.lower()
    if strip_articles:
        out = _ARTICLES_EN.sub(" ", out)
    out = _PUNCT_WS.sub(" ", out)
    return " ".join(out.split())


def tokenize(text: str) -> list[str]:
    return normalize_answer(text).split()


def extract_number(text: str) -> float | None:
    """Extract the final numeric answer from model output (GSM8K style).

    Order: ``#### x`` marker, ``\\boxed{}``, last number in text (commas stripped).
    """
    if not text:
        return None
    if m := FINAL_ANSWER_RE.search(text):
        return _to_float(m.group(1))
    if m := BOXED_RE.search(text):
        return _to_float(m.group(1))
    numbers = _NUMBER_RE.findall(text)
    for raw in reversed(numbers):
        val = _to_float(raw)
        if val is not None:
            return val
    return None


def _to_float(raw: str) -> float | None:
    raw = raw.strip().rstrip(".")
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        m = _NUMBER_RE.search(raw)
        return float(m.group().replace(",", "")) if m else None


_ANSWER_IS_RE = re.compile(
    r"(?:answer|option|答案|选项)\s*(?:is|:|：|是)?\s*([A-Ha-h])\b", re.IGNORECASE)
_WORD_TOKEN_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)*")


def extract_choice_letter(text: str, choices_len: int = 4) -> str | None:
    """Extract chosen option letter (A-H) from a multiple-choice answer."""
    if not text:
        return None
    if m := BOXED_RE.search(text):
        inner = m.group(1).strip()
        if len(inner) == 1 and inner.isalpha():
            return inner.upper()
    if m := FINAL_ANSWER_RE.search(text):
        inner = m.group(1).strip()
        if inner and inner[0].isalpha() and len(inner) <= 3:
            return inner[0].upper()
    limit = chr(ord("A") + max(choices_len - 1, 1))
    if m := _ANSWER_IS_RE.search(text):
        letter = m.group(1).upper()
        if letter <= limit:
            return letter
    # whole-word single letters ("B.", "(c)", "answer: D") - contractions like
    # "I'd" stay a single token and never match
    for token in _WORD_TOKEN_RE.findall(text):
        if len(token) == 1:
            letter = token.upper()
            if letter <= limit:
                return letter
    return None


def exact_match(prediction: str, reference: str | list[str], *, extract: str | None = None,
                 tol: float | None = None, case_sensitive: bool = False) -> bool:
    """Exact match with optional extraction mode ('number'|'choice') and numeric tolerance."""
    refs = reference if isinstance(reference, list) else [reference]

    def norm(s: str) -> str:
        return s if case_sensitive else normalize_answer(s)

    if extract == "number":
        pred_num = extract_number(prediction)
        if pred_num is None:
            return False
        for ref in refs:
            ref_num = extract_number(ref) if not _is_number(ref) else _to_float(ref)
            if ref_num is None:
                continue
            if tol is not None:
                if math.isclose(pred_num, ref_num, rel_tol=tol, abs_tol=tol):
                    return True
            elif math.isclose(pred_num, ref_num, rel_tol=1e-6, abs_tol=1e-9):
                return True
        return False

    if extract == "choice":
        pred_letter = extract_choice_letter(prediction)
        if pred_letter is None:
            return False
        for ref in refs:
            if ref.strip() and ref.strip()[0].isalpha():
                if pred_letter == ref.strip()[0].upper():
                    return True
            elif norm(pred_letter) == norm(ref):
                return True
        return False

    pred_norm = norm(prediction)
    return any(pred_norm == norm(r) for r in refs if pred_norm != "" or norm(r) == "")


def _is_number(s: str) -> bool:
    try:
        float(s.replace(",", ""))
        return True
    except (ValueError, TypeError):
        return False


def token_f1(prediction: str, reference: str) -> tuple[float, float, float]:
    """Token-level precision/recall/F1 between prediction and single reference."""
    pred_toks, ref_toks = tokenize(prediction), tokenize(reference)
    common = Counter(pred_toks) & Counter(ref_toks)
    num_same = sum(common.values())
    if num_same == 0 or not pred_toks or not ref_toks:
        return 0.0, 0.0, 0.0
    precision = num_same / len(pred_toks)
    recall = num_same / len(ref_toks)
    f1 = 2 * precision * recall / (precision + recall)
    return precision, recall, f1


def token_f1_multi(prediction: str, references: list[str]) -> tuple[float, float, float]:
    """Max token F1 over multiple references (SQuAD style)."""
    best = (0.0, 0.0, 0.0)
    for ref in references:
        scores = token_f1(prediction, ref)
        if scores[2] > best[2]:
            best = scores
    return best


def rouge_l(prediction: str, reference: str, *, beta: float = 1.2) -> tuple[float, float, float]:
    """Sentence-level ROUGE-L F-measure via LCS."""
    pred_toks, ref_toks = tokenize(prediction), tokenize(reference)
    if not pred_toks or not ref_toks:
        return 0.0, 0.0, 0.0
    lcs = _lcs_len(pred_toks, ref_toks)
    precision = lcs / len(pred_toks)
    recall = lcs / len(ref_toks)
    if precision + recall == 0:
        return 0.0, 0.0, 0.0
    beta2 = beta * beta
    f = (1 + beta2) * precision * recall / (beta2 * precision + recall)
    return precision, recall, f


def _lcs_len(a: list[str], b: list[str]) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


def bleu(prediction: str, references: list[str], *, max_n: int = 4) -> float:
    """Corpus-free smoothed BLEU (brevity-penalty applied, add-1 smoothing)."""
    pred_toks = tokenize(prediction)
    if not pred_toks:
        return 0.0
    ref_toks_list = [tokenize(r) for r in references if tokenize(r)]
    if not ref_toks_list:
        return 0.0
    log_prec = 0.0
    orders_used = 0
    total_matches = 0
    for n in range(1, max_n + 1):
        pred_ngrams = Counter(tuple(pred_toks[i:i + n]) for i in range(len(pred_toks) - n + 1))
        match, total = 0, sum(pred_ngrams.values())
        if total == 0:
            continue  # sequence shorter than this n-gram order
        orders_used += 1
        for ng in pred_ngrams:
            best = max(sum(1 for i in range(len(rt) - n + 1) if tuple(rt[i:i + n]) == ng)
                       for rt in ref_toks_list)
            match += min(pred_ngrams[ng], best)
        total_matches += match
        log_prec += math.log((match + 1.0) / (total + 1.0))
    if total_matches == 0:
        return 0.0  # conventional BLEU-0 when nothing matches
    if orders_used == 0:
        return 0.0
    ref_len = min(len(rt) for rt in ref_toks_list)
    bp = 1.0 if len(pred_toks) > ref_len else math.exp(1 - ref_len / max(len(pred_toks), 1))
    return bp * math.exp(log_prec / orders_used)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def hash_embedding(text: str, dim: int = 256) -> list[float]:
    """Deterministic dependency-free char-ngram TF embedding (fallback when
    sentence-transformers is unavailable)."""
    vec = [0.0] * dim
    norm = normalize_answer(text, strip_articles=False)
    for i in range(len(norm)):
        for n in (2, 3):
            gram = norm[i:i + n]
            if len(gram) < n:
                continue
            h = int(hashlib.md5(gram.encode("utf-8")).hexdigest(), 16)
            idx, sign = h % dim, 1 if (h >> 128) & 1 else -1
            vec[idx] += sign
    return vec


# ---------------------------------------------------------------- perturbations

_SYNonyms = {
    "quick": "fast", "fast": "quick", "big": "large", "large": "big",
    "buy": "purchase", "purchase": "buy", "help": "assist", "assist": "help",
    "show": "display", "display": "show", "tell": "explain", "explain": "tell",
    "start": "begin", "begin": "start", "use": "utilize", "utilize": "use",
}


def stable_seed(text: str) -> int:
    return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)


def perturb_text(text: str, kind: str, seed: int | None = None) -> str:
    """Deterministically perturb text for robustness testing.

    Kinds: typo (char swap), case, whitespace, paraphrase (light synonym swap).
    """
    if not text:
        return text
    rng = random.Random(seed if seed is not None else stable_seed(text))
    if kind == "typo":
        if len(text) < 4:
            return text
        i = rng.randrange(1, len(text) - 2)
        return text[:i] + text[i + 1] + text[i] + text[i + 2:]
    if kind == "case":
        return "".join(c.upper() if rng.random() < 0.4 else c.lower() for c in text)
    if kind == "whitespace":
        return re.sub(r"(\s)", lambda m: m.group(1) * 2 if rng.random() < 0.4 else m.group(1), text)
    if kind == "paraphrase":
        words = text.split()
        out = []
        for w in words:
            key = w.lower().strip(".,!?")
            out.append(_SYNonyms.get(key, w) if rng.random() < 0.6 else w)
        return " ".join(out)
    raise ValueError(f"unknown perturbation kind: {kind}")


PERTURBATION_KINDS = ("typo", "case", "whitespace", "paraphrase")
