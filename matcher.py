import bisect
import os
import re
from collections import Counter
from typing import List, Tuple

_BOX_KEYS = ("x", "y", "width", "height")
_MIN_SUBSTRING_HEAD_LEN = 4


def _normalized_box(box: dict) -> dict:
    return {
        "x": int(box["x"]),
        "y": int(box["y"]),
        "width": max(1, int(round(box["width"]))),
        "height": max(1, int(round(box["height"]))),
    }


def get_landmark_fingerprint(item: dict) -> str:
    key = item.get("key") or ""
    head = (item.get("heading") or "").strip().lower()
    tag = (item.get("tag") or "").strip().lower()
    snip = re.sub(r"\s+", " ", (item.get("snippet") or "").strip().lower())[:80]

    if key.startswith("RENDERING::") or key.startswith("ID::"):
        return key
    if head and len(head) >= 3:
        return f"HEAD::{head}"
    if len(snip) >= 20:
        return f"SNIP::{tag}::{snip}"
    return f"TAG::{tag}::{key}"


def _is_weak_key(k: str) -> bool:
    return (k.startswith("TAG::") and k.endswith("::")) or k == "" or k.startswith("TAG::::")


def _headings_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    if min(len(a), len(b)) < _MIN_SUBSTRING_HEAD_LEN:
        return False
    return a in b or b in a


def _has_valid_box(item: dict) -> bool:
    box = item.get("box")
    return isinstance(box, dict) and all(isinstance(box.get(k), (int, float)) for k in _BOX_KEYS)


def optimal_non_overlapping_chain(pairs: List[Tuple[dict, dict]]) -> List[Tuple[dict, dict]]:
    """
    Finds the largest non-overlapping, strictly increasing sequence of landmark pairs.
    Allows up to 2px of subpixel boundary overlap to absorb rounding jitter.
    """
    if not pairs:
        return []

    # Sort primarily by production Y, secondarily by staging Y
    sorted_pairs = sorted(
        pairs,
        key=lambda ps: (ps[0]["box"]["y"], ps[1]["box"]["y"], -ps[0]["box"]["height"])
    )

    n = len(sorted_pairs)
    # dp[i] = length of best chain ending at index i
    dp = [1] * n
    parent = [-1] * n

    for i in range(n):
        p_curr = sorted_pairs[i][0]["box"]
        s_curr = sorted_pairs[i][1]["box"]
        p_top = p_curr["y"]
        s_top = s_curr["y"]

        for j in range(i):
            p_prev = sorted_pairs[j][0]["box"]
            s_prev = sorted_pairs[j][1]["box"]

            p_prev_bot = p_prev["y"] + p_prev["height"]
            s_prev_bot = s_prev["y"] + s_prev["height"]

            # Accept if strictly proceeding downwards on both sides (with 2px tolerance for subpixel rounding)
            if (p_top >= p_prev_bot - 2) and (s_top >= s_prev_bot - 2):
                if dp[j] + 1 > dp[i]:
                    dp[i] = dp[j] + 1
                    parent[i] = j

    # Reconstruct optimal chain
    best_end = max(range(n), key=lambda idx: dp[idx]) if n > 0 else -1
    chain = []
    curr = best_end
    while curr != -1:
        chain.append(sorted_pairs[curr])
        curr = parent[curr]

    return chain[::-1]


def match_landmarks(
    prod_landmarks: List[dict],
    stg_landmarks: List[dict],
    logger=print,
) -> List[Tuple[dict, dict]]:
    prod_landmarks = [
        dict(l, box=_normalized_box(l["box"]))
        for l in (prod_landmarks or [])
        if _has_valid_box(l)
    ]
    stg_landmarks = [
        dict(l, box=_normalized_box(l["box"]))
        for l in (stg_landmarks or [])
        if _has_valid_box(l)
    ]

    if not prod_landmarks or not stg_landmarks:
        return []

    p_sorted = sorted(prod_landmarks, key=lambda l: (l["box"]["y"], l["box"]["height"]))
    s_sorted = sorted(stg_landmarks, key=lambda l: (l["box"]["y"], l["box"]["height"]))

    p_keys = [get_landmark_fingerprint(p) for p in p_sorted]
    s_keys = [get_landmark_fingerprint(s) for s in s_sorted]

    # Pass 1: Strict unique key matches
    p_counts = Counter(p_keys)
    s_counts = Counter(s_keys)
    s_index = {}
    for s_idx, k in enumerate(s_keys):
        s_index.setdefault(k, []).append(s_idx)

    matches = []
    used_p, used_s = set(), set()
    for p_idx, k in enumerate(p_keys):
        if _is_weak_key(k):
            continue
        if p_counts[k] == 1 and s_counts.get(k, 0) == 1:
            s_cand = s_index[k][0]
            matches.append((p_idx, s_cand))
            used_p.add(p_idx)
            used_s.add(s_cand)

    # Pass 2: Semantic text/heading scoring
    for p_idx, p in enumerate(p_sorted):
        if p_idx in used_p:
            continue
        p_text = set((p.get("snippet") or "").lower().split()[:20])
        p_head = (p.get("heading") or "").strip().lower()
        p_y = p["box"]["y"]

        best_score = 0.35
        best_s = None

        for s_idx, s in enumerate(s_sorted):
            if s_idx in used_s:
                continue

            score = 0.0
            s_head = (s.get("heading") or "").strip().lower()
            if _headings_match(p_head, s_head):
                score += 0.55

            s_text = set((s.get("snippet") or "").lower().split()[:20])
            if p_text and s_text:
                intersection = len(p_text & s_text)
                union = len(p_text | s_text)
                if union > 0:
                    score += (intersection / float(union)) * 0.45

            y_diff = abs(p_y - s["box"]["y"])
            if y_diff < 800:
                score += (1.0 - y_diff / 800.0) * 0.15

            if score > best_score:
                best_score = score
                best_s = s_idx

        if best_s is not None:
            matches.append((p_idx, best_s))
            used_p.add(p_idx)
            used_s.add(best_s)

    raw_pairs = [(p_sorted[i], s_sorted[j]) for i, j in matches]
    ordered_pairs = optimal_non_overlapping_chain(raw_pairs)

    logger(
        f"Matched landmarks: prod={len(prod_landmarks)} stg={len(stg_landmarks)} "
        f"-> matched={len(matches)} -> ordered_siblings={len(ordered_pairs)}"
    )

    final_pairs = []
    for p, s in ordered_pairs:
        final_pairs.append((p["box"], s["box"]))
        logger(
            f"Landmark delta: "
            f"x={s['box']['x'] - p['box']['x']} "
            f"y={s['box']['y'] - p['box']['y']}"
        )

    return final_pairs