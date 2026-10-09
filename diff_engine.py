import bisect
import os
from collections import Counter
from dataclasses import dataclass
from typing import List, Optional, Tuple
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

RED = (225, 29, 72, 220)
YELLOW = (250, 204, 21, 220)
YELLOW_RGB = (250, 204, 21)
WHITE = (255, 255, 255)
MIN_REGION_WIDTH = 4
STRIP_HEIGHT = 1024


def get_landmark_fingerprint(item: dict) -> str:
    key = item.get("key", "")
    head = (item.get("heading") or "").strip().lower()
    tag = (item.get("tag") or "").strip().lower()

    if key.startswith("RENDERING::") or key.startswith("ID::"):
        return key
    if head:
        return f"HEAD::{head}"
    return f"TAG::{tag}::{key}"


def longest_increasing_chain(pairs: List[Tuple[dict, dict]]) -> List[Tuple[dict, dict]]:
    tails_vals, tails_idx = [], []
    prev = [None] * len(pairs)
    for i, (_, s) in enumerate(pairs):
        v = s["box"]["y"]
        pos = bisect.bisect_left(tails_vals, v)
        if pos == len(tails_vals):
            tails_vals.append(v)
            tails_idx.append(i)
        else:
            tails_vals[pos] = v
            tails_idx[pos] = i
        prev[i] = tails_idx[pos - 1] if pos > 0 else None

    chain = []
    k = tails_idx[-1] if tails_idx else None
    while k is not None:
        chain.append(pairs[k])
        k = prev[k]
    return chain[::-1]


def match_landmarks(prod_landmarks: List[dict], stg_landmarks: List[dict], logger=print) -> List[Tuple[dict, dict]]:
    if not prod_landmarks or not stg_landmarks:
        return []

    p_sorted = sorted(prod_landmarks, key=lambda l: (l["box"]["y"], l["box"]["height"]))
    s_sorted = sorted(stg_landmarks, key=lambda l: (l["box"]["y"], l["box"]["height"]))

    p_keys = [get_landmark_fingerprint(p) for p in p_sorted]
    s_keys = [get_landmark_fingerprint(s) for s in s_sorted]

    p_counts = Counter(k for k in p_keys if not k.startswith("TAG::"))
    s_index = {}
    for s_idx, k in enumerate(s_keys):
        if not k.startswith("TAG::"):
            s_index.setdefault(k, []).append(s_idx)

    matches = []
    used_p, used_s = set(), set()
    for p_idx, k in enumerate(p_keys):
        if k.startswith("TAG::") or p_counts[k] != 1:
            continue
        cands = s_index.get(k, [])
        if len(cands) == 1:
            matches.append((p_idx, cands[0]))
            used_p.add(p_idx)
            used_s.add(cands[0])

    for p_idx, p in enumerate(p_sorted):
        if p_idx in used_p:
            continue
        p_y = p["box"]["y"]
        p_text = set((p.get("snippet") or "").lower().split()[:14])
        p_head = (p.get("heading") or "").strip().lower()

        best_score, best_s = 0.28, None
        for s_idx, s in enumerate(s_sorted):
            if s_idx in used_s:
                continue
            score = 0.0
            s_head = (s.get("heading") or "").strip().lower()
            if p_head and s_head and (p_head == s_head or p_head in s_head or s_head in p_head):
                score += 0.55

            s_text = set((s.get("snippet") or "").lower().split()[:14])
            if p_text and s_text:
                score += (len(p_text & s_text) / float(max(len(p_text | s_text), 1))) * 0.45

            y_diff = abs(p_y - s["box"]["y"])
            if y_diff < 500:
                score += (1.0 - y_diff / 500.0) * 0.25

            if score > best_score:
                best_score, best_s = score, s_idx

        if best_s is not None:
            matches.append((p_idx, best_s))
            used_p.add(p_idx)
            used_s.add(best_s)

    pairs = [(p_sorted[i], s_sorted[j]) for i, j in matches]
    pairs.sort(key=lambda ps: (ps[0]["box"]["y"], ps[1]["box"]["y"]))
    pairs = longest_increasing_chain(pairs)
    final_pairs = [(p["box"], s["box"]) for p, s in pairs]

    logger(
        f"Matched landmarks: prod={len(prod_landmarks)} stg={len(stg_landmarks)} "
        f"-> matched={len(matches)} -> ordered={len(final_pairs)}"
    )
    return final_pairs


def _int_box(b: dict) -> dict:
    return {
        "x": int(b["x"]),
        "y": int(b["y"]),
        "width": int(round(b["width"])),
        "height": int(round(b["height"])),
    }


def create_hazard_stripe_box(width: int, height: int, stripe_w: int = 12) -> Image.Image:
    box = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(box)
    for offset in range(-height, width + height, stripe_w * 2):
        draw.polygon(
            [
                (offset, 0),
                (offset + stripe_w, 0),
                (offset + stripe_w + height, height),
                (offset + height, height),
            ],
            fill=YELLOW_RGB,
        )
    return box


def paste_yellow_highlight(canvas: Image.Image, box_coords: Tuple[int, int, int, int]):
    x1, y1, x2, y2 = box_coords
    w = max(0, x2 - x1)
    h = max(0, y2 - y1)
    if w <= 0 or h <= 0:
        return
    overlay = Image.new("RGBA", (w, h), YELLOW)
    canvas.paste(overlay, (x1, y1), overlay)


def find_best_2d_shift(
    prod_gray: Image.Image,
    stg_gray: Image.Image,
    px: int,
    sx: int,
    py: int,
    sy: int,
    w: int,
    h: int,
    max_v_shift: int = 12,
    max_h_shift: int = 2,
) -> Tuple[int, int]:
    if py < 0 or sy < 0 or (py + h) > prod_gray.height or (sy + h) > stg_gray.height:
        return 0, 0

    s_blk = stg_gray.crop((sx, sy, sx + w, sy + h))
    cache = {}

    def score(dx, dy):
        key = (dx, dy)
        if key not in cache:
            cur_x = max(0, min(prod_gray.width - w, px + dx))
            cur_y = max(0, min(prod_gray.height - h, py + dy))
            p_blk = prod_gray.crop((cur_x, cur_y, cur_x + w, cur_y + h))
            cache[key] = ImageStat.Stat(ImageChops.difference(p_blk, s_blk)).sum[0]
        return cache[key]

    best_dx, best_dy = 0, 0
    best_score = score(0, 0)

    for dy in range(-max_v_shift, max_v_shift + 1):
        for dx in range(-max_h_shift, max_h_shift + 1):
            s = score(dx, dy)
            if s < best_score:
                best_score = s
                best_dx, best_dy = dx, dy

    return best_dx, best_dy


def make_diff_mask_subpixel(p_blk: Image.Image, s_blk: Image.Image, tolerance: int) -> Image.Image:
    d = ImageChops.difference(p_blk, s_blk)
    r, g, b = d.split()
    base_diff = ImageChops.lighter(ImageChops.lighter(r, g), b)
    min_diff = base_diff

    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)):
        p_shifted = ImageChops.offset(p_blk, dx, dy)
        d_s = ImageChops.difference(p_shifted, s_blk)
        r_s, g_s, b_s = d_s.split()
        cur_diff = ImageChops.lighter(ImageChops.lighter(r_s, g_s), b_s)
        min_diff = ImageChops.darker(min_diff, cur_diff)

    lut = [0] * (tolerance + 1) + [255] * (255 - tolerance)
    return min_diff.point(lut)


def diff_region(
    prod_rgb: Image.Image,
    stg_rgb: Image.Image,
    px: int,
    py: int,
    sx: int,
    sy: int,
    w: int,
    h: int,
    tolerance: int,
) -> Tuple[Image.Image, int]:
    mask = Image.new("L", (w, h), 0)
    count = 0
    for y in range(0, h, STRIP_HEIGHT):
        hh = min(STRIP_HEIGHT, h - y)
        p_blk = prod_rgb.crop((px, py + y, px + w, py + y + hh))
        s_blk = stg_rgb.crop((sx, sy + y, sx + w, sy + y + hh))

        m = make_diff_mask_subpixel(p_blk, s_blk, tolerance)
        c = m.histogram()[255]
        count += c
        mask.paste(m, (0, y))
    return mask, count


@dataclass
class Segment:
    p_start: int
    p_end: int
    s_start: int
    s_end: int
    meta: Optional[dict] = None

    @property
    def p_h(self):
        return max(0, self.p_end - self.p_start)

    @property
    def s_h(self):
        return max(0, self.s_end - self.s_start)


def build_aligned_segments(landmark_pairs: List[Tuple[dict, dict]], prod_h: int, stg_h: int) -> List[Segment]:
    segments = []
    p_last = 0
    s_last = 0

    for idx, (p_box, s_box) in enumerate(landmark_pairs or [], start=1):
        p_box, s_box = _int_box(p_box), _int_box(s_box)

        p_top = max(0, min(p_box["y"], prod_h))
        s_top = max(0, min(s_box["y"], stg_h))
        p_bot = min(prod_h, p_top + p_box["height"])
        s_bot = min(stg_h, s_top + s_box["height"])

        if p_top < p_last or s_top < s_last or (p_bot <= p_top and s_bot <= s_top):
            continue

        # Gap between landmarks
        if p_top > p_last or s_top > s_last:
            segments.append(Segment(p_last, p_top, s_last, s_top, meta=None))

        # Matched landmark
        segments.append(
            Segment(
                p_top, p_bot, s_top, s_bot,
                meta={
                    "px": p_box["x"], "pw": p_box["width"],
                    "sx": s_box["x"], "sw": s_box["width"],
                    "idx": idx
                }
            )
        )
        p_last, s_last = p_bot, s_bot

    if p_last < prod_h or s_last < stg_h:
        segments.append(Segment(p_last, prod_h, s_last, stg_h, meta=None))

    return segments


def compute_component_isolated_diff(
    img_prod: Image.Image,
    img_stg: Image.Image,
    landmark_pairs: Optional[List[Tuple[dict, dict]]] = None,
    threshold: float = 0.10,
    max_v_shift: int = 16,
    max_h_shift: int = 2,
    noise_floor: float = 0.001,
    diff_gaps: bool = True,
    save_sections: bool = False,
    file_prefix: str = "",
    out_dir: str = "",
) -> Tuple[Image.Image, Image.Image, Image.Image, int]:
    prod_rgb = img_prod.convert("RGB")
    stg_rgb = img_stg.convert("RGB")
    prod_w, prod_h = prod_rgb.size
    stg_w, stg_h = stg_rgb.size
    max_w = max(prod_w, stg_w)

    tolerance = max(18, min(254, int(threshold * 255)))

    prod_gray = prod_rgb.convert("L")
    stg_gray = stg_rgb.convert("L")

    segments = build_aligned_segments(landmark_pairs, prod_h, stg_h)

    # 1. Calculate overall required height with padding inserted
    total_h = 0
    plan = []
    for seg in segments:
        p_h, s_h = seg.p_h, seg.s_h
        diff_h = abs(p_h - s_h)

        # Absorb 1px browser rounding variations so identical items don't trigger yellow padding
        if diff_h <= 1:
            seg_alloc_h = max(p_h, s_h)
            plan.append((seg, seg_alloc_h, 0))
            total_h += seg_alloc_h
        else:
            seg_alloc_h = max(p_h, s_h)
            plan.append((seg, seg_alloc_h, diff_h))
            total_h += seg_alloc_h

    total_h = max(1, total_h)

    aligned_prod = Image.new("RGB", (max_w, total_h), WHITE)
    aligned_stg = Image.new("RGB", (max_w, total_h), WHITE)
    diff_canvas = Image.new("RGBA", (max_w, total_h), (0, 0, 0, 0))

    dest_y = 0
    total_mismatch = 0

    for seg, alloc_h, delta_h in plan:
        p_h, s_h = seg.p_h, seg.s_h
        overlap_h = min(p_h, s_h)

        # Slices to paste
        if p_h > 0:
            p_crop = prod_rgb.crop((0, seg.p_start, prod_w, seg.p_end))
            aligned_prod.paste(p_crop, (0, dest_y))

        if s_h > 0:
            s_crop = stg_rgb.crop((0, seg.s_start, stg_w, seg.s_end))
            aligned_stg.paste(s_crop, (0, dest_y))

        # Diff calculation on the overlapping section
        if overlap_h > 0:
            geometry = None
            if seg.meta:
                px, sx = max(0, seg.meta["px"]), max(0, seg.meta["sx"])
                w = min(seg.meta["pw"], seg.meta["sw"], prod_w - px, stg_w - sx)
                geometry = (px, sx, w)
            elif diff_gaps:
                geometry = (0, 0, min(prod_w, stg_w))

            if geometry and geometry[2] >= MIN_REGION_WIDTH:
                px, sx, w = geometry
                dx, dy = 0, 0
                if seg.meta and overlap_h >= 6:
                    dx, dy = find_best_2d_shift(
                        prod_gray, stg_gray, px, sx, seg.p_start, seg.s_start, w, overlap_h, max_v_shift, max_h_shift
                    )

                p_crop_x = max(0, min(prod_w - w, px + dx))
                p_crop_y = max(0, min(prod_h - overlap_h, seg.p_start + dy))

                mask, count = diff_region(prod_rgb, stg_rgb, p_crop_x, p_crop_y, sx, seg.s_start, w, overlap_h, tolerance)

                if count / float(w * overlap_h) < noise_floor:
                    mask, count = None, 0

                if mask is not None:
                    total_mismatch += count
                    diff_canvas.paste(RED, (sx, dest_y, sx + w, dest_y + overlap_h), mask)

        # Real height discrepancy (> 1px): insert hazard stripes to sync content below it
        if delta_h > 1:
            total_mismatch += delta_h * max_w
            yellow_y1 = dest_y + overlap_h
            yellow_y2 = dest_y + alloc_h

            paste_yellow_highlight(diff_canvas, (0, yellow_y1, max_w, yellow_y2))
            stripe = create_hazard_stripe_box(max_w, delta_h)

            if p_h < s_h:
                # Production was shorter: pad Prod with hazard stripes so subsequent content shifts down
                aligned_prod.paste(stripe, (0, yellow_y1))
            else:
                # Staging was shorter: pad Staging with hazard stripes
                aligned_stg.paste(stripe, (0, yellow_y1))

        dest_y += alloc_h

    return aligned_prod, aligned_stg, diff_canvas, total_mismatch