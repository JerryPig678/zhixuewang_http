"""Answer sheet annotation rendering.

Renders webpage-style grading annotations onto zhixue.com answer sheet
images with PIL:

- Total score bar (red headline)
- Wrong-question red boxes + per-question "13题 14/17 -3" labels
- Per-subquestion deduction grid "16(2): -3/3" (from checksheet
  ``stepDatas[].stepStandardScore`` full scores and ``stepRecords`` scores)
- Teacher spot marks ("+1") at their original positions

Coordinate system: section/branch positions are millimetres on an A3
landscape sheet (420mm wide); px = mm * image_width / 420. Branch
coordinates come in two variants (relative to section / absolute page
coords) and are auto-detected.
"""
from __future__ import annotations

import io
import json
from typing import Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFont

PAPER_W_MM = 420.0
MAX_WIDTH = 1600

RED = (225, 30, 30)
GREEN = (25, 135, 60)
GRAY = (110, 110, 110)
DARK = (55, 55, 55)

_FONT_CANDIDATES = {
    True: [
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ],
    False: [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ],
}


def _load_font(size: int, bold: bool = False):
    for path in _FONT_CANDIDATES[bold]:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def build_score_map(sheet_datas: dict, step_datas: list | None = None) -> Dict[int, dict]:
    """question number -> {score, standard_score, steps, spot_marks}.

    steps: [(score, full or None)]; full scores come from
    ``stepDatas[].stepStandardScore`` matched by topicStartNum + stepNum.
    """
    step_full: Dict[int, Dict[int, float]] = {}
    for sd in step_datas or []:
        try:
            t = int(sd.get("topicStartNum", 0))
            n = int(sd.get("stepNum", 0))
            step_full.setdefault(t, {})[n] = sd.get("stepStandardScore", 0.0)
        except (ValueError, TypeError, AttributeError):
            continue

    details = (sheet_datas.get("userAnswerRecordDTO") or {}).get("answerRecordDetails") or []
    score_map: Dict[int, dict] = {}
    for d in details:
        try:
            steps: List[Tuple[int, float, float | None]] = []
            spot_marks: List[dict] = []
            topic = int(d["dispTitle"])
            for sub in d.get("subTopics") or []:
                if not sub:
                    continue
                for st in sub.get("stepRecords") or []:
                    if st:
                        n = int(st.get("stepNum", 0))
                        steps.append((n, st.get("score", 0.0), step_full.get(topic, {}).get(n)))
                for m in sub.get("teacherMarkingRecords") or []:
                    mc = m.get("markingContent")
                    if not mc or not isinstance(mc, str) or mc.strip() in ("", "[]"):
                        continue
                    try:
                        items = json.loads(mc)
                        if isinstance(items, str):
                            items = json.loads(items)
                        for it in items or []:
                            if isinstance(it, dict) and it.get("content"):
                                spot_marks.append(it)
                    except (ValueError, TypeError):
                        continue
            steps.sort()
            score_map[topic] = {
                "score": d.get("score", 0.0),
                "standard_score": d.get("standardScore", 0.0),
                "is_correct": d.get("isCorrect", False),
                "answer": d.get("answer"),  # 选择题学生所选选项, 如 "C"
                "steps": [(s, f) for _, s, f in steps],
                "spot_marks": spot_marks,
            }
        except (ValueError, KeyError, TypeError):
            continue
    return score_map


# 选择题选项气泡: firstOption/rowOffset 等字段为 96dpi 像素单位, 相对 branch 区域
_PX2MM = 25.4 / 96.0
CHOICE_OK = (26, 188, 156)  # 原版同款青绿 (答对选项)
CHOICE_BAD = (225, 30, 30)  # 答错选项


def _draw_choice_bubbles(draw: ImageDraw.Draw, br: dict, score_map: Dict[int, dict],
                         gx: float, gy: float, gw: float, gh: float, px_per_mm: float):
    """复刻原版选择题涂色: 学生所涂选项, 答对青绿 / 答错红色.

    布局模型 (由月考英语卡实测校准):
      - 每行一题, 行距 = gh/n_rows
      - firstOption 为第一行首个选项气泡, 96dpi 像素单位, 相对 branch 区域
      - 选项间距自适应: (gw - fo_left - fo_w) / (n-1), 实测各选项数卡均吻合
    """
    chooses = br.get("chooses") or []
    fo = br.get("firstOption") or {}
    ix_list = br.get("ixList") or []
    if not chooses or not fo or not ix_list:
        return
    fo_l = fo.get("left", 0) * _PX2MM
    fo_t = fo.get("top", 0) * _PX2MM
    fo_w = fo.get("width", 0) * _PX2MM
    fo_h = fo.get("height", 0) * _PX2MM
    if fo_w <= 0 or fo_h <= 0:
        return
    n_opts = len(chooses)
    pitch_x = (gw - fo_l - fo_w) / (n_opts - 1) if n_opts > 1 else 0.0
    n_rows = len(ix_list)
    pitch_y = (gh - fo_t - fo_h) / (n_rows - 1) if n_rows > 1 else 0.0
    bw_px = max(2, fo_w * px_per_mm)
    bh_px = max(2, fo_h * px_per_mm)
    radius = min(bw_px, bh_px) * 0.25
    for r, q in enumerate(ix_list):
        rec = score_map.get(q)
        if not rec:
            continue
        ans = rec.get("answer")
        if not ans or ans not in chooses:
            continue
        i = chooses.index(ans)
        x = (gx + fo_l + i * pitch_x) * px_per_mm
        y = (gy + fo_t + r * pitch_y) * px_per_mm
        color = CHOICE_OK if rec.get("is_correct") else CHOICE_BAD
        draw.rounded_rectangle([x, y, x + bw_px, y + bh_px], radius=radius, fill=color)


def _draw_topic_scores(draw: ImageDraw.Draw, ix_list: list, score_map: Dict[int, dict],
                       gx: float, gy: float, gw: float, gh: float, px_per_mm: float, fsize: int):
    """解答区域多题号时, 在每条横线末端画原版同款每小题得分 (红色 "56:0分").

    布局: 题号按行优先两列排布 (56 57 第一行), 与原版一致.
    """
    recs = [(q, score_map[q]) for q in ix_list if q in score_map]
    if len(recs) < 2 or gw < 60:
        return
    n = len(recs)
    n_rows = (n + 1) // 2
    font = _load_font(int(fsize * 0.72), bold=True)
    cell_h = gh / n_rows * px_per_mm
    half_w = gw / 2 * px_per_mm
    for idx, (q, r) in enumerate(recs):
        row, col = idx // 2, idx % 2
        text = f"{q}:{r['score']:g}分"
        tw = draw.textbbox((0, 0), text, font=font)[2]
        x = gx * px_per_mm + (col + 1) * half_w - tw - fsize * 0.8
        y = gy * px_per_mm + row * cell_h + (cell_h - fsize) / 2
        draw.text((x, y), text, fill=RED, font=font)


def _draw_label(draw: ImageDraw.Draw, text: str, x: float, y: float, color, fsize: int,
                max_width: float | None = None):
    """在 (x, y) 画红/绿底白字标签, y 为标签顶部; 超过 max_width 时自动缩小字号."""
    font = _load_font(fsize, bold=True)
    pad = max(4, fsize // 5)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if max_width and tw + pad * 2 > max_width:
        fsize = max(12, int(fsize * max_width / (tw + pad * 2)))
        font = _load_font(fsize, bold=True)
        pad = max(3, fsize // 5)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.rectangle([x, y, x + tw + pad * 2, y + th + pad * 2], fill=color)
    draw.text((x + pad, y + pad - bbox[1]), text, fill="white", font=font)
    return th + pad * 2


def _resolve_branch_mm(sec_pos: dict, br_pos: dict) -> Tuple[float, float]:
    """Resolve branch absolute mm coords (relative vs absolute templates)."""
    sx, sy = sec_pos.get("left", 0), sec_pos.get("top", 0)
    sw, sh = sec_pos.get("width", 0), sec_pos.get("height", 0)
    bl, bt = br_pos.get("left", 0), br_pos.get("top", 0)
    bw, bh = br_pos.get("width", 0), br_pos.get("height", 0)
    tol = 1.0
    rx, ry = sx + bl, sy + bt
    if rx < sx - tol or ry < sy - tol or rx + bw > sx + sw + tol or ry + bh > sy + sh + tol:
        return float(bl), float(bt)
    return rx, ry


def _draw_sub_scores(draw: ImageDraw.Draw, title: int, steps: List[Tuple[float, float | None]],
                     x: float, y: float, fsize: int) -> float:
    """Per-subquestion deduction grid, two columns like the webpage."""
    if not steps:
        return 0.0
    font = _load_font(fsize, bold=True)
    line_h = int(fsize * 1.35)
    col_gap = int(fsize * 0.6)
    cells = []
    for i, (score, full) in enumerate(steps):
        text = f"-{full - score:g}/{full:g}" if full is not None else f"{score:g}"
        cells.append((f"{title}({i + 1}):", text))
    col0, col1 = cells[0::2], cells[1::2]

    def col_width(col):
        w = 0
        for label, num in col:
            w = max(w, draw.textbbox((0, 0), label, font=font)[2]
                    + draw.textbbox((0, 0), num, font=font)[2])
        return w

    w0 = col_width(col0)
    for row, (label, num) in enumerate(col0):
        yy = y + row * line_h
        draw.text((x, yy), label, fill=(70, 70, 70), font=font)
        draw.text((x + draw.textbbox((0, 0), label, font=font)[2], yy), num, fill=RED, font=font)
    for row, (label, num) in enumerate(col1):
        yy = y + row * line_h
        draw.text((x + w0 + col_gap, yy), label, fill=(70, 70, 70), font=font)
        draw.text((x + w0 + col_gap + draw.textbbox((0, 0), label, font=font)[2], yy), num, fill=RED, font=font)
    return len(col0) * line_h


def _draw_spot_marks(draw: ImageDraw.Draw, marks: List[dict],
                     img_w: int, img_h: int, fsize: int) -> None:
    font = _load_font(fsize, bold=True)
    for m in marks:
        try:
            left = float(m.get("left", 0)) / 100.0
            top = float(m.get("top", 0)) / 100.0
        except (ValueError, TypeError):
            continue
        content = str(m.get("content", ""))
        color = RED if content.startswith("-") else GREEN
        x, y = left * img_w, top * img_h
        pad = max(2, fsize // 6)
        bbox = draw.textbbox((0, 0), content, font=font)
        draw.rectangle([x, y, x + (bbox[2] - bbox[0]) + pad * 2, y + (bbox[3] - bbox[1]) + pad * 2],
                       fill=color)
        draw.text((x + pad, y + pad - bbox[1]), content, fill="white", font=font)


def _paper_width_mm(page: dict, img: Image.Image) -> float:
    """由 locatePoint 定位块推断卡纸宽度.

    A3 横向 420mm (maxX≈408-414) / A4 横向 297mm / A4 纵向 210mm (maxX≈204).
    无定位数据时按图片纵横比 fallback.
    """
    lp = page.get("locatePoint") or []
    if lp:
        try:
            max_x = max(p.get("left", 0) + p.get("width", 0) for p in lp)
            if max_x > 350:
                return 420.0
            if max_x > 250:
                return 297.0
            return 210.0
        except (ValueError, TypeError):
            pass
    return 420.0 if img.width >= img.height else 210.0


def _page_uses_percent(page: dict) -> bool:
    """页面坐标是否使用百分比字段 (部分卡模板 left/top 为设计分辨率像素,
    同时提供 leftPercent 等百分比; 旧模板百分比字段为 0 占位)."""
    for s in page.get("sections") or []:
        if not s:
            continue
        c = s.get("contents") or {}
        positions = [s.get("position"), c.get("position")]
        for b in c.get("branch") or []:
            if b:
                positions.append(b.get("position"))
        for pos in positions:
            if pos and (pos.get("leftPercent") or pos.get("topPercent") or pos.get("widthPercent")):
                return True
    return False


def _pos_px(pos: dict, img: Image.Image) -> Tuple[float, float, float, float]:
    """百分比 pos -> (x, y, w, h) 图像像素."""
    return (pos.get("leftPercent", 0) * img.width,
            pos.get("topPercent", 0) * img.height,
            pos.get("widthPercent", 0) * img.width,
            pos.get("heightPercent", 0) * img.height)


def annotate_page(img: Image.Image, page: dict, score_map: Dict[int, dict]) -> None:
    """Draw annotations onto one answer sheet page (in place)."""
    draw = ImageDraw.Draw(img)
    use_percent = _page_uses_percent(page)
    px_per_mm = img.width / _paper_width_mm(page, img)
    fsize = max(22, int(img.width * 0.011))
    seen_boxes = set()

    for sec in page.get("sections") or []:
        if not sec:
            continue
        contents = sec.get("contents") or {}
        sec_pos = sec.get("position") or contents.get("position") or {}
        for br in contents.get("branch") or []:
            if not br:
                continue
            ix_list = br.get("ixList") or []
            recs = [(i, score_map[i]) for i in ix_list if i in score_map]
            if not recs:
                continue
            br_pos = br.get("position") or {}
            if use_percent:
                bp_has_pct = any(br_pos.get(k) for k in ("leftPercent", "topPercent", "widthPercent"))
                if bp_has_pct:
                    x1, y1, bw, bh = _pos_px(br_pos, img)
                else:
                    # branch 无百分比时退化到 section 区域
                    x1, y1, bw, bh = _pos_px(sec_pos, img)
                x2, y2 = x1 + bw, y1 + bh
                box_key = (round(x1, 1), round(y1, 1), round(bw, 1), round(bh, 1))
            else:
                ax, ay = _resolve_branch_mm(sec_pos, br_pos)
                bw, bh = br_pos.get("width", 0), br_pos.get("height", 0)
                box_key = (round(ax, 1), round(ay, 1), round(bw, 1), round(bh, 1))
                x1, y1 = ax * px_per_mm, ay * px_per_mm
                x2, y2 = (ax + bw) * px_per_mm, (ay + bh) * px_per_mm
            if box_key in seen_boxes:
                continue
            seen_boxes.add(box_key)
            if x2 - x1 < 20 or y2 - y1 < 20:
                continue
            # 选择题涂色 (原版同款): 答对青绿/答错红 (仅毫米模板, 百分比卡无实测数据不画)
            if not use_percent and br.get("chooses") and br.get("firstOption"):
                _draw_choice_bubbles(draw, br, score_map, ax, ay, bw, bh, px_per_mm)
            got = sum(r["score"] for _, r in recs)
            full = sum(r["standard_score"] for _, r in recs)
            wrong = [i for i, r in recs if r["score"] < r["standard_score"]]
            color = RED if wrong else GREEN
            if wrong:
                draw.rectangle([x1, y1, x2, y2], outline=color, width=max(3, fsize // 8))
            # 多题号解答区域: 每条横线末端画每小题得分 (原版同款 "56:0分")
            if not use_percent and len(ix_list) >= 2 and not br.get("chooses"):
                _draw_topic_scores(draw, ix_list, score_map, ax, ay, bw, bh, px_per_mm, fsize)
            start = f"{ix_list[0]}题" if len(recs) == 1 else f"{ix_list[0]}-{ix_list[-1]}题"
            deduct = full - got
            label = f"{start} {got:g}/{full:g}" + (f"  -{deduct:g}" if wrong else "")
            label_h = _draw_label(draw, label, x1, max(0, y1 - fsize * 1.6), color, fsize,
                                  max_width=(x2 - x1))
            sub_y = y1 - fsize * 1.6 + label_h + 6
            if sub_y + fsize * 1.3 < y2 or y1 - fsize * 1.6 < 0:
                for i, r in recs:
                    if r.get("steps"):
                        used = _draw_sub_scores(draw, i, r["steps"], x1, max(sub_y, 4), int(fsize * 0.8))
                        sub_y += used + 4
            for _, r in recs:
                _draw_spot_marks(draw, r.get("spot_marks", []), img.width, img.height, int(fsize * 0.9))


def add_score_bar(img: Image.Image, score, standard_score, title: str) -> Image.Image:
    """Prepend a white info bar with the total score in red."""
    bar_h = max(80, int(img.height * 0.055))
    big = _load_font(int(bar_h * 0.62), bold=True)
    small = _load_font(int(bar_h * 0.30))
    bar = Image.new("RGB", (img.width, bar_h), "white")
    d = ImageDraw.Draw(bar)
    score_text = f"{score:g}" if score is not None else "?"
    d.text((24, bar_h * 0.10), score_text, fill=RED, font=big)
    sw = d.textbbox((24, bar_h * 0.10), score_text, font=big)[2]
    tail = f" / {standard_score:g} 分" if standard_score is not None else ""
    d.text((sw + 14, bar_h * 0.56), tail, fill=GRAY, font=small)
    if title:
        d.text((sw + 14 + int(bar_h * 2.4), bar_h * 0.56), title, fill=DARK, font=small)
    out = Image.new("RGB", (img.width, img.height + bar_h), "white")
    out.paste(bar, (0, 0))
    out.paste(img, (0, bar_h))
    return out


def merge_pages(images: List[Image.Image], max_width: int = MAX_WIDTH) -> bytes:
    """Stack pages vertically into one long JPEG."""
    max_w = max(p.width for p in images)
    scaled = []
    for p in images:
        if p.width != max_w:
            p = p.resize((max_w, int(p.height * max_w / p.width)), Image.LANCZOS)
        scaled.append(p)
    total_h = sum(p.height for p in scaled)
    canvas = Image.new("RGB", (max_w, total_h), "white")
    y = 0
    for p in scaled:
        canvas.paste(p, (0, y))
        y += p.height
    if canvas.width > max_width:
        canvas = canvas.resize((max_width, int(canvas.height * max_width / canvas.width)), Image.LANCZOS)
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=88)
    return buf.getvalue()
