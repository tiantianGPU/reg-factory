# -*- coding: utf-8 -*-
"""
vision_solver/drivers.py — 通用求解 driver

两种交互范式：
  grid_select  多选网格（reCAPTCHA/hCaptcha）：截整块挑战图 -> 投票得 PICK 列表 ->
               点选对应 tile -> 点 Verify -> 检测是否通过/出新图，循环。
  single_pick  单选（Arkose 排序拼图）：逐张截候选 -> 拼网格 -> 投票得 best ->
               导航到第 best 张 -> Submit。
"""

import asyncio
import math
import os
import random

from .vision import vote_answer, vote_picklist, vote_points
from .imaging import (
    shot_element, screenshot_frame_region, stitch_options_grid, enhance_local, annotate_choice,
    overlay_grid_numbers, annotate_canvas_choice, detect_shortest_bead_chain_cells,
)
from .schema import build_prompt


async def _find_frame(page, frame_match):
    """按 url 关键字找承载挑战的 frame；frame_match 空则返回 main_frame。"""
    if not frame_match:
        return page.main_frame
    matches = []
    for f in page.frames:
        u = (f.url or "").lower()
        if any(k.lower() in u for k in frame_match):
            matches.append(f)
    # hCaptcha may retain hidden preload challenge frames. Prefer the matching
    # frame whose canvas is actually laid out, then preserve the old first-match
    # behavior for non-canvas challenge drivers.
    for f in matches:
        try:
            canvas = f.locator("canvas").first
            box = await canvas.bounding_box() if await canvas.count() else None
            if box and box.get("width", 0) > 100 and box.get("height", 0) > 100:
                return f
        except Exception:
            pass
    return matches[0] if matches else None


async def _challenge_present(page, frame_match, markers):
    """挑战是否还在：frame 还在 或 页面/ frame 文本未出现成功标志。"""
    if frame_match:
        for f in page.frames:
            u = (f.url or "").lower()
            if any(k.lower() in u for k in frame_match):
                return True
        return False
    # 主页面模式：靠成功标志判断
    if markers:
        try:
            body = (await page.evaluate("()=>document.body.innerText||''")).lower()
        except Exception:
            body = ""
        return not any(m.lower() in body for m in markers)
    return True


async def _read_instruction(scope, sel):
    """读题干文本（select all X 的整句）。"""
    if not sel:
        return ""
    try:
        el = scope.locator(sel).first
        if await el.count() > 0:
            return ((await el.inner_text()) or "").replace("\n", " ").strip()
    except Exception:
        pass
    return ""


async def solve_grid_select(page, spec, shot_dir):
    """多选网格求解。返回 True=通过 / False=失败。"""
    os.makedirs(shot_dir, exist_ok=True)
    for rnd in range(spec.max_rounds):
        frame = await _find_frame(page, spec.frame_match)
        if frame is None:
            print(f"  [grid] R{rnd} 挑战 frame 消失，判通过")
            return True
        await asyncio.sleep(spec.settle_ms / 1000)
        instruction = await _read_instruction(frame, spec.challenge_text_sel)

        # 截挑战图：优先整块 grid_image_sel；否则逐 tile 截图拼网格
        tiles = frame.locator(spec.tile_sel) if spec.tile_sel else None
        n_tiles = await tiles.count() if tiles else 0
        if spec.grid_image_sel:
            img = await shot_element(frame, spec.grid_image_sel, f"{shot_dir}/grid_r{rnd}.png", scale=2)
            geom = None
        elif n_tiles > 0:
            cells = []
            capture_failed = False
            for i in range(n_tiles):
                c = None
                for capture_attempt in range(3):
                    c = await shot_element(
                        frame,
                        f"{spec.tile_sel} >> nth={i}",
                        f"{shot_dir}/t_r{rnd}_{i}.png",
                        scale=2,
                    )
                    if c:
                        break
                    await asyncio.sleep(0.4)
                if c:
                    cells.append(c)
                else:
                    capture_failed = True
                    break
            if capture_failed or len(cells) != n_tiles:
                print(
                    f"  [grid] R{rnd} tile DOM changed during capture; "
                    "retrying without submitting"
                )
                await asyncio.sleep(1)
                continue
            img, geom = stitch_options_grid(cells, f"{shot_dir}/grid_r{rnd}.png", cols=spec.cols, return_geom=True)
        else:
            print(f"  [grid] R{rnd} 无 tile/grid 选择器，无法截图")
            return False
        if not img:
            print(f"  [grid] R{rnd} 截图失败")
            return False
        img_hd = enhance_local(img, f"{shot_dir}/grid_r{rnd}_hd.png", scale=1)

        n_opt = n_tiles if n_tiles > 0 else 9   # 整图模式默认按 3x3=9 编号
        prompt = build_prompt(spec, instruction, n_opt)
        picks, votes, raws = [], {}, []
        for vote_attempt in range(1, 4):
            picks, votes, raws = vote_picklist(
                prompt, img_hd, n_opt, deadline=spec.deadline
            )
            if any(raw[1] is not None for raw in raws):
                break
            if vote_attempt < 3:
                print(
                    f"  [grid] R{rnd} model response was unavailable or malformed; "
                    f"retrying current challenge ({vote_attempt + 1}/3)"
                )
                await asyncio.sleep(vote_attempt)
        if not any(raw[1] is not None for raw in raws):
            print(f"  [grid] R{rnd} no parseable model response; leaving challenge intact")
            return False
        print(f"  [grid] R{rnd} instruction={instruction[:50]!r} -> PICK {picks} votes={votes}")
        try:
            annotate_choice(f"{shot_dir}/grid_r{rnd}.png", geom, picks,
                            f"{shot_dir}/REVIEW_r{rnd}.png", note=f"r{rnd} {picks}", votes_raw=raws)
        except Exception:
            pass

        # 点选中的 tile
        if tiles and picks:
            for i in picks:
                try:
                    await tiles.nth(i).click(timeout=4000)
                    await asyncio.sleep(0.3)
                except Exception:
                    pass
        # 提交
        if spec.submit_sel:
            try:
                await frame.locator(spec.submit_sel).first.click(timeout=5000)
            except Exception:
                try:
                    await page.locator(spec.submit_sel).first.click(timeout=4000)
                except Exception:
                    pass
        await asyncio.sleep(max(spec.settle_ms, 2500) / 1000)

        # 判通过：frame 没了
        if spec.success_gone_frame and spec.frame_match:
            if await _find_frame(page, spec.frame_match) is None:
                print(f"  [grid] R{rnd} 后挑战 frame 消失，通过")
                return True
        # 否则继续下一轮（reCAPTCHA 选完常换新图）
    print("  [grid] 跑满轮数仍未通过")
    return False


async def solve_single_pick(page, spec, shot_dir):
    """单选（Arkose 类）求解。返回 True=通过 / False=失败。"""
    os.makedirs(shot_dir, exist_ok=True)
    for rnd in range(spec.max_rounds):
        frame = await _find_frame(page, spec.frame_match)
        if frame is None:
            print(f"  [single] R{rnd} 挑战 frame 消失，判通过")
            return True
        await asyncio.sleep(spec.settle_ms / 1000)
        instruction = await _read_instruction(frame, spec.challenge_text_sel)
        # 数候选张数：靠依次点 next 直到点不动，先固定从 cand_sel 截当前张
        ref = await shot_element(frame, spec.ref_sel, f"{shot_dir}/ref_r{rnd}.png", scale=3) if spec.ref_sel else None
        nxt = frame.get_by_role("button", name=spec.next_btn_role) if spec.next_btn_role else None
        cands = []
        N = 6  # 默认 6，多数 Arkose 排序题如此；点 next 截到点不动为止
        for i in range(N):
            c = await shot_element(frame, spec.cand_sel, f"{shot_dir}/c_r{rnd}_{i}.png", scale=3)
            if c:
                cands.append(c)
            if i < N - 1 and nxt:
                try:
                    await nxt.first.click(timeout=3000)
                    await asyncio.sleep(0.8)
                except Exception:
                    break
        if not cands:
            print(f"  [single] R{rnd} 无候选图")
            return False
        grid, geom = stitch_options_grid(cands, f"{shot_dir}/grid_r{rnd}.png",
                                         reference_b64=ref, cols=spec.cols, return_geom=True)
        grid_hd = enhance_local(grid, f"{shot_dir}/grid_r{rnd}_hd.png", scale=2)
        prompt = build_prompt(spec, instruction, len(cands))
        best, votes, raws = vote_answer(prompt, grid_hd, len(cands), deadline=spec.deadline)
        if best is None or not (0 <= best < len(cands)):
            best = 0
        print(f"  [single] R{rnd} -> #{best} votes={votes}")
        try:
            annotate_choice(f"{shot_dir}/grid_r{rnd}.png", geom, best,
                            f"{shot_dir}/REVIEW_r{rnd}.png", note=f"r{rnd} #{best}", votes_raw=raws)
        except Exception:
            pass
        # 导航回到第 best 张（当前停在最后一张）
        frame = await _find_frame(page, spec.frame_match)
        if frame is None:
            return True
        if spec.prev_btn_role:
            prv = frame.get_by_role("button", name=spec.prev_btn_role)
            for _ in range(len(cands) - 1 - best):
                try:
                    await prv.first.click(timeout=3000)
                    await asyncio.sleep(0.6)
                except Exception:
                    pass
        # 提交
        if spec.submit_role:
            for _ in range(4):
                try:
                    await frame.get_by_role("button", name=spec.submit_role).first.click(timeout=4000)
                    break
                except Exception:
                    frame = await _find_frame(page, spec.frame_match)
                    if frame is None:
                        return True
                    await asyncio.sleep(1.5)
        await asyncio.sleep(max(spec.settle_ms, 3000) / 1000)
        if await _find_frame(page, spec.frame_match) is None:
            print(f"  [single] R{rnd} 后通过")
            return True
    print("  [single] 跑满轮数仍未通过")
    return False


# ———————————————————— canvas_grid（新版 hCaptcha：整块画布） ————————————————————

# 题干判 1×3 单选卡片 vs 3×3 多选网格的关键词
_CARD_HINTS = ("card", "different", "unique", "doesn't belong", "does not belong", "odd one")
# 单答案关键词：题干用单数"the item/thing/object/one"且没有"all" -> 整张图里只点 1 格
_SINGLE_HINTS = ("the item", "the thing", "the object", "the one", "the image",
                 "the picture", "which ", "tap the", "click the", "select the",
                 "choose the", "find the")
_MULTI_HINTS = ("all ", "each ", "every ")


def _infer_layout(instruction, spec):
    """据题干推断 (rows, cols, single)：
      含 card/different 类   -> 1×3 单选（卡片）
      含"the item/thing…"且无"all/each/every" -> rows×cols 但单选（只点 1 格）
      其余（"select all …"） -> rows×cols 多选。"""
    t = (instruction or "").lower()
    if any(h in t for h in _CARD_HINTS):
        return 1, 3, True
    if any(h in t for h in _SINGLE_HINTS) and not any(h in t for h in _MULTI_HINTS):
        return spec.rows, spec.cols, True
    return spec.rows, spec.cols, False


async def _canvas_alive(frame, canvas_sel):
    """挑战画布是否还在且可见（>0 尺寸）。通过后 frame 常残留但 canvas 没了/0尺寸。"""
    try:
        el = frame.locator(canvas_sel).first
        if await el.count() == 0:
            return False
        box = await el.bounding_box()
        return bool(box and box.get("width", 0) > 10 and box.get("height", 0) > 10)
    except Exception:
        return False


async def _wait_canvas(frame, canvas_sel, timeout=6.0):
    """轮询等画布渲染出来（刚出题/换新题时画布会有渲染延迟）。出现返回 True。"""
    import time as _t
    t0 = _t.time()
    while _t.time() - t0 < timeout:
        if await _canvas_alive(frame, canvas_sel):
            return True
        await asyncio.sleep(0.4)
    return False


async def _shot_canvas_stable(frame, canvas_sel, path, settle=1.3, max_wait=12.0,
                              initial=1.5, need_stable=2):
    """反复截画布，直到连续 need_stable 帧字节一致(图像渲染稳定)再返回，避免截到
    加载中/动画中/渐入的半成品。有的 hCaptcha 题图是渐入或逐块拼出现的，太快截会糊/缺。
      initial: 首次截图前的强制等待（给图片基本加载时间）。
      settle:  相邻两帧间隔（要足够长，太短会误判"正在渐入的某一瞬"为稳定）。
      need_stable: 需要连续多少帧完全一致才算稳。
    返回 (base64, w, h) 或 (None,0,0)。"""
    import time as _t
    import base64 as _b64, io as _io

    def _dims(b):
        try:
            from PIL import Image as _I
            im = _I.open(_io.BytesIO(_b64.b64decode(b)))
            return im.width, im.height
        except Exception:
            return 0, 0

    await asyncio.sleep(initial)
    last = None
    stable = 0
    best = None
    t0 = _t.time()
    while _t.time() - t0 < max_wait:
        cur = await shot_element(frame, canvas_sel, path, scale=1)
        if cur:
            best = cur
            if cur == last:
                stable += 1
                if stable >= need_stable:
                    w, h = _dims(cur)
                    return cur, w, h
            else:
                stable = 0
        last = cur
        await asyncio.sleep(settle)
    # 超时仍用最后拿到的一帧
    if best:
        w, h = _dims(best)
        return best, w, h
    return None, 0, 0


async def _click_canvas_cell(frame, canvas_sel, cx, cy, canvas_w, canvas_h):
    """在 canvas 上按"画布像素坐标 (cx,cy)"点击：换算成元素内偏移（截图像素可能=CSS像素*dpr）。"""
    el = frame.locator(canvas_sel).first
    box = await el.bounding_box()
    if not box:
        return False
    # 截图分辨率(canvas_w/h) 与元素 CSS 尺寸(box) 可能因 devicePixelRatio 不同 -> 等比换算
    fx = box["width"] / canvas_w if canvas_w else 1
    fy = box["height"] / canvas_h if canvas_h else 1
    px = cx * fx
    py = cy * fy
    try:
        await el.click(position={"x": px, "y": py}, timeout=4000)
        return True
    except Exception as e:
        print(f"  [canvas] click err: {str(e)[:50]}")
        return False


async def solve_canvas_grid(page, spec, shot_dir):
    """新版 hCaptcha 整块画布求解：截 canvas -> 按行列切网格编号 -> 投票 ->
    按像素坐标点 canvas 对应格 -> 点提交 -> 检测通过/出新图，循环。返回 True/False。"""
    os.makedirs(shot_dir, exist_ok=True)
    submitted = False   # 是否已至少提交过一次（用于区分"画布还没渲染"vs"已通过画布消失"）
    for rnd in range(spec.max_rounds):
        frame = await _find_frame(page, spec.frame_match)
        if frame is None:
            print(f"  [canvas] R{rnd} 挑战 frame 消失，判通过")
            return True
        await asyncio.sleep(spec.settle_ms / 1000)
        # 等画布渲染；提交过后若画布始终不出现 = 已通过，否则是渲染慢需继续等
        if not await _wait_canvas(frame, spec.canvas_sel, timeout=6.0):
            if submitted:
                print(f"  [canvas] R{rnd} 画布已消失，判通过")
                return True
            print(f"  [canvas] R{rnd} 画布迟迟未出现，再等")
            continue
        instruction = await _read_instruction(frame, spec.challenge_text_sel)
        if not instruction:
            # 题干还没渲染出来，等一下重读
            await asyncio.sleep(1.0)
            instruction = await _read_instruction(frame, spec.challenge_text_sel)

        rows, cols, single = _infer_layout(instruction, spec)
        n_opt = rows * cols

        # 截整块挑战画布（等渲染稳定：先等图基本加载，再要求连续帧一致，避免半成品）
        img, canvas_w, canvas_h = await _shot_canvas_stable(
            frame, spec.canvas_sel, f"{shot_dir}/hc_r{rnd}.png",
            settle=1.3, max_wait=12.0, initial=1.5, need_stable=2)
        if not img:
            print(f"  [canvas] R{rnd} 画布截图失败")
            await asyncio.sleep(1.5)
            continue

        # 画编号网格（四边内缩到真实图块区，扣掉题干条/边距，保证点中心准）
        grid_b64, geom = overlay_grid_numbers(
            img, f"{shot_dir}/hc_r{rnd}_grid.png", rows, cols,
            top_frac=spec.grid_top_frac, bottom_frac=spec.grid_bottom_frac,
            left_frac=spec.grid_left_frac, right_frac=spec.grid_right_frac,
            pad_frac=spec.grid_pad_frac)
        grid_hd = enhance_local(grid_b64, f"{shot_dir}/hc_r{rnd}_hd.png", scale=1)

        prompt = build_prompt(spec, instruction, n_opt)
        local_picks = []
        if spec.answer_format == "SHORTEST_BEAD_CHAINS":
            local_picks = detect_shortest_bead_chain_cells(img, geom, count=2)
        direct_clicks = []
        if spec.answer_format == "TWO_POINTS":
            point_image = enhance_local(
                img, f"{shot_dir}/hc_r{rnd}_points.jpg", scale=1
            )
            first, second, raws = vote_points(
                prompt,
                point_image,
                max_tokens=spec.answer_max_tokens,
                deadline=spec.deadline,
            )
            if not first or not second:
                print(f"  [canvas] R{rnd} no valid two-point vision answer")
                return False
            direct_clicks = [
                (first[0] * canvas_w, first[1] * canvas_h),
                (second[0] * canvas_w, second[1] * canvas_h),
            ]
            picks = []
            votes = {}
            print(
                f"  [canvas] R{rnd} direct click points -> "
                f"{first}, {second}"
            )
        elif local_picks:
            picks = local_picks
            votes = {index: 1 for index in picks}
            raws = [("bead-chain-detector", picks, "local image analysis")]
            print(f"  [canvas] R{rnd} local shortest-chain detection -> {picks}")
        elif single:
            # 单答案：vote_answer 取多数票（平票按模型顺序），永不空选
            best, votes, raws = vote_answer(prompt, grid_hd, n_opt, deadline=spec.deadline)
            if best is None:
                # 兜底：从同一批回复里抠 PICK，用得票最高的格；再不行点中心格
                from .vision import _parse_picklist
                from collections import Counter
                pc = Counter()
                for item in raws:
                    pl = _parse_picklist(item[2], n_opt) if len(item) > 2 else None
                    for i in (pl or []):
                        pc[i] += 1
                best = pc.most_common(1)[0][0] if pc else n_opt // 2
                print(f"  [canvas] R{rnd} 单选无 ANSWER，兜底点 #{best}")
            picks = [best]
        else:
            picks, votes, raws = vote_picklist(
                prompt,
                grid_hd,
                n_opt,
                max_tokens=spec.answer_max_tokens,
                deadline=spec.deadline,
            )
            if not picks:
                # 多选也全空：退化成最高票一格，避免空提交浪费一轮
                from collections import Counter
                pc = Counter()
                for item in raws:
                    for i in (item[1] or []) if isinstance(item[1], (list, tuple)) else []:
                        pc[i] += 1
                if pc:
                    picks = [pc.most_common(1)[0][0]]
                    print(f"  [canvas] R{rnd} 多选无共识，兜底点最高票 #{picks[0]}")
            if not picks and not votes:
                print(f"  [canvas] R{rnd} no valid vision answer; refusing empty submit")
                return False
        print(f"  [canvas] R{rnd} ins={instruction[:48]!r} layout={rows}x{cols} "
              f"single={single} -> {picks} votes={votes}")
        try:
            annotate_canvas_choice(f"{shot_dir}/hc_r{rnd}_grid.png", geom, picks,
                                   f"{shot_dir}/REVIEW_r{rnd}.png",
                                   note=f"r{rnd} {picks} {instruction[:40]}", votes_raw=raws)
        except Exception:
            pass

        # 按像素坐标点选中格
        cells_xy = (geom or {}).get("cells_xy", [])
        clicked = 0
        click_targets = direct_clicks or [
            cells_xy[index]
            for index in picks
            if 0 <= index < len(cells_xy)
        ]
        for cx, cy in click_targets:
            if await _click_canvas_cell(
                frame, spec.canvas_sel, cx, cy, canvas_w, canvas_h
            ):
                clicked += 1
                await asyncio.sleep(0.35)
        print(f"  [canvas] R{rnd} clicked {clicked}/{len(click_targets)} targets")

        # 提交（hCaptcha 是 Verify/Skip 同一个 .button-submit）
        if spec.submit_sel and clicked > 0:
            try:
                await frame.locator(spec.submit_sel).first.click(timeout=5000)
                submitted = True
            except Exception:
                pass
        await asyncio.sleep(max(spec.settle_ms, 2500) / 1000)

        # 判通过：挑战 frame 没了，或提交后画布消失
        if spec.success_gone_frame:
            f2 = await _find_frame(page, spec.frame_match)
            if f2 is None:
                print(f"  [canvas] R{rnd} 后挑战 frame 消失，通过")
                return True
            if submitted and not await _canvas_alive(f2, spec.canvas_sel):
                # 画布消失也可能是"正在渲染下一题"，短等确认它不再回来
                if not await _wait_canvas(f2, spec.canvas_sel, timeout=3.0):
                    print(f"  [canvas] R{rnd} 后画布消失未再出现，通过")
                    return True
        # 否则继续下一轮（hCaptcha 常连续多题）
    print("  [canvas] 跑满轮数仍未通过")
    return False


# ———————————————————— canvas_drag（画布拖拽：把piece拖到target） ————————————————————

async def _drag_on_canvas(frame, page, canvas_sel, fr, to, canvas_w, canvas_h, steps=24):
    """Drag between normalized canvas points using a human-like trajectory."""
    el = frame.locator(canvas_sel).first
    box = await el.bounding_box()
    if not box:
        print("  [drag] unable to read canvas bbox")
        return False
    x0 = box["x"] + box["width"] * fr[0]
    y0 = box["y"] + box["height"] * fr[1]
    x1 = box["x"] + box["width"] * to[0]
    y1 = box["y"] + box["height"] * to[1]
    print(
        f"  [drag] canvas_css={box['width']:.0f}x{box['height']:.0f} "
        f"image={canvas_w}x{canvas_h} mouse=({x0:.0f},{y0:.0f})->({x1:.0f},{y1:.0f})"
    )
    try:
        from common import human_mouse

        await human_mouse.human_move_to(page, x0, y0)
        await asyncio.sleep(random.uniform(0.14, 0.32))
        await page.mouse.down()
        await asyncio.sleep(random.uniform(0.10, 0.22))
        path = human_mouse.windmouse_path(
            x0, y0, x1, y1, gravity=8.0, wind=2.4,
            max_step=11.0, target_area=14.0,
        )
        for index, (px, py) in enumerate(path):
            await page.mouse.move(
                px + random.uniform(-0.45, 0.45),
                py + random.uniform(-0.45, 0.45),
            )
            progress = index / max(1, len(path) - 1)
            edge_slowdown = 1.0 - math.sin(math.pi * progress)
            await asyncio.sleep(
                random.uniform(0.006, 0.013) + edge_slowdown * 0.009
            )
        await page.mouse.move(x1, y1)
        await asyncio.sleep(random.uniform(0.12, 0.28))
        await page.mouse.up()
        return True
    except Exception as e:
        print(f"  [drag] mouse drag err: {str(e)[:60]}")
        try:
            await page.mouse.up()
        except Exception:
            pass
        return False


async def solve_canvas_drag(page, spec, shot_dir):
    """画布拖拽求解：截 canvas -> 投票 FROM/TO 归一化坐标 -> mouse 拖放 -> 提交，循环。
    返回 True/False。需要 spec.mode='canvas_drag'。"""
    os.makedirs(shot_dir, exist_ok=True)
    submitted = False
    empty_answers = 0
    for rnd in range(spec.max_rounds):
        frame = await _find_frame(page, spec.frame_match)
        if frame is None:
            print(f"  [drag] R{rnd} 挑战 frame 消失，判通过")
            return True
        await asyncio.sleep(spec.settle_ms / 1000)
        if not await _wait_canvas(frame, spec.canvas_sel, timeout=6.0):
            if submitted:
                print(f"  [drag] R{rnd} 画布已消失，判通过")
                return True
            print(f"  [drag] R{rnd} 画布迟迟未出现，再等")
            continue
        instruction = await _read_instruction(frame, spec.challenge_text_sel)

        img, canvas_w, canvas_h = await _shot_canvas_stable(
            frame, spec.canvas_sel, f"{shot_dir}/drag_r{rnd}.png",
            settle=1.3, max_wait=12.0, initial=1.5, need_stable=2)
        if not img:
            print(f"  [drag] R{rnd} 画布截图失败")
            await asyncio.sleep(1.5)
            continue
        img_hd = enhance_local(img, f"{shot_dir}/drag_r{rnd}_hd.png", scale=1)

        prompt = build_prompt(spec, instruction, 0)
        # Gemini 3.6 may spend most of a 900-token budget on visual reasoning
        # and truncate before its final FROM/TO line.
        fr, to, raws = vote_points(
            prompt, img_hd, max_tokens=1800, deadline=spec.deadline
        )
        print(f"  [drag] R{rnd} ins={instruction[:48]!r} FROM={fr} TO={to}")
        # 复盘：在原图上画 FROM(蓝)->TO(红) 箭头
        try:
            _annotate_drag(f"{shot_dir}/drag_r{rnd}.png", fr, to,
                           f"{shot_dir}/REVIEW_r{rnd}.png", instruction[:40], raws)
        except Exception:
            pass
        if not fr or not to:
            print(f"  [drag] R{rnd} 投票无有效坐标，跳过")
            empty_answers += 1
            if empty_answers >= 2:
                print("  [drag] consecutive vision failures; using another solver")
                return False
            await asyncio.sleep(1.0)
            continue
        empty_answers = 0

        await _drag_on_canvas(frame, page, spec.canvas_sel, fr, to, canvas_w, canvas_h)
        await asyncio.sleep(spec.settle_ms / 1000)

        if spec.submit_sel:
            try:
                await frame.locator(spec.submit_sel).first.click(timeout=5000)
                submitted = True
            except Exception:
                pass
        await asyncio.sleep(max(spec.settle_ms, 2500) / 1000)

        if spec.success_gone_frame:
            f2 = await _find_frame(page, spec.frame_match)
            if f2 is None:
                print(f"  [drag] R{rnd} 后挑战 frame 消失，通过")
                return True
            if submitted and not await _canvas_alive(f2, spec.canvas_sel):
                if not await _wait_canvas(f2, spec.canvas_sel, timeout=3.0):
                    print(f"  [drag] R{rnd} 后画布消失未再出现，通过")
                    return True
    print("  [drag] 跑满轮数仍未通过")
    return False


def _annotate_drag(img_path, fr, to, out_path, note, raws):
    """拖拽复盘：原图上画各模型 FROM/TO 点 + 最终 FROM(蓝圈)->TO(红圈)箭头。"""
    from PIL import Image, ImageDraw
    im = Image.open(img_path).convert("RGB")
    W, H = im.width, im.height
    d = ImageDraw.Draw(im)
    palette = [(0, 120, 255), (255, 140, 0), (0, 200, 0), (200, 0, 200)]
    for mi, item in enumerate(raws or []):
        pts = item[1]
        if not pts:
            continue
        col = palette[mi % len(palette)]
        (fx, fy), (tx, ty) = pts
        d.ellipse([fx*W-4, fy*H-4, fx*W+4, fy*H+4], outline=col, width=2)
        d.ellipse([tx*W-4, ty*H-4, tx*W+4, ty*H+4], outline=col, width=2)
        d.line([fx*W, fy*H, tx*W, ty*H], fill=col, width=1)
    if fr and to:
        fx, fy = fr[0]*W, fr[1]*H
        tx, ty = to[0]*W, to[1]*H
        d.ellipse([fx-9, fy-9, fx+9, fy+9], outline=(0, 80, 255), width=3)
        d.ellipse([tx-9, ty-9, tx+9, ty+9], outline=(255, 0, 0), width=3)
        d.line([fx, fy, tx, ty], fill=(255, 0, 0), width=3)
    if note:
        d.text((6, H-14), note[:120], fill=(255, 0, 0))
    im.save(out_path)
