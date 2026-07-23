# -*- coding: utf-8 -*-
"""
vision_solver/imaging.py — 截图 / 拼网格 / 增强 / 标注 工具（通用版）

从 common/agent_captcha.py 复制而来，纯图像处理，无平台耦合。依赖 Pillow（缺失则降级）。
"""

import base64
import json


async def screenshot_frame_region(frame, path):
    """截某 iframe 在主页面里的可视区域为 png，返回 base64。"""
    try:
        handle = await frame.frame_element()
        await handle.screenshot(path=path)
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    except Exception as e:
        print(f"  [img] frame screenshot err: {str(e)[:70]}")
        return None


async def shot_element(scope, selector, path, scale=3):
    """截 scope(page 或 frame) 里某元素并放大 scale 倍。返回放大后 png base64，失败 None。"""
    try:
        el = scope.locator(selector).first
        if await el.count() == 0:
            return None
        await el.screenshot(path=path)
    except Exception as e:
        print(f"  [img] shot_element({selector}) err: {str(e)[:60]}")
        return None
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        if scale and scale != 1:
            im = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
        im.save(path)
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    except Exception:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()


def stitch_options_grid(b64_list, path, reference_b64=None, cols=4, label_h=24, return_geom=False):
    """把一组候选图拼成一张带编号(#0,#1...)的网格大图，可选最上方放参考图。
    把 N 次调用压成 1 次，并给模型横向对比视角。
    return_geom=True 返回 (base64, geom)，geom={"cells":[(x,y,w,h)...],"size":(W,H)}。"""
    try:
        from PIL import Image, ImageDraw
        import io
        imgs = [Image.open(io.BytesIO(base64.b64decode(b))).convert("RGB") for b in b64_list]
        if not imgs:
            return (None, None) if return_geom else None
        cw = max(i.width for i in imgs)
        ch = max(i.height for i in imgs)
        n = len(imgs)
        cols = min(cols, n)
        rows = (n + cols - 1) // cols
        gap = 12
        cell_w, cell_h = cw + gap, ch + label_h + gap
        ref_block = 0
        ref_img = None
        if reference_b64:
            ref_img = Image.open(io.BytesIO(base64.b64decode(reference_b64))).convert("RGB")
            ref_block = ref_img.height + label_h + gap
        W = cols * cell_w + gap
        H = ref_block + rows * cell_h + gap
        canvas = Image.new("RGB", (W, H), (255, 255, 255))
        draw = ImageDraw.Draw(canvas)
        y0 = gap
        if ref_img is not None:
            draw.text((gap, 2), "REFERENCE (target):", fill=(200, 0, 0))
            canvas.paste(ref_img, (gap, label_h))
            y0 = ref_block + gap
        cells = []
        for idx, im in enumerate(imgs):
            r, c = divmod(idx, cols)
            x = gap + c * cell_w
            y = y0 + r * cell_h
            draw.text((x, y), f"#{idx}", fill=(0, 0, 200))
            canvas.paste(im, (x, y + label_h))
            cells.append((x, y + label_h, im.width, im.height))
        canvas.save(path)
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        if return_geom:
            return b64, {"cells": cells, "size": (W, H)}
        return b64
    except Exception as e:
        print(f"  [img] stitch err: {str(e)[:60]}")
        return (None, None) if return_geom else None


def enhance_local(image_b64, path, scale=2, max_side=1600, jpeg_quality=82):
    """本地传统增强（Lanczos 放大 + 锐化 + 对比/亮度 + 去噪），存 JPEG 控体积。返回 base64。"""
    try:
        from PIL import Image, ImageEnhance, ImageFilter
        import io
        im = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")
        if scale and scale != 1:
            im = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
        long_side = max(im.width, im.height)
        if long_side > max_side:
            r = max_side / long_side
            im = im.resize((max(1, int(im.width * r)), max(1, int(im.height * r))), Image.LANCZOS)
        im = im.filter(ImageFilter.MedianFilter(size=3))
        im = ImageEnhance.Contrast(im).enhance(1.6)
        im = ImageEnhance.Brightness(im).enhance(1.15)
        im = ImageEnhance.Sharpness(im).enhance(2.2)
        im = im.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=2))
        im.save(path, "JPEG", quality=jpeg_quality)
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    except Exception as e:
        print(f"  [img] enhance err: {str(e)[:60]}")
        return image_b64


def annotate_choice(grid_path, geom, picked, out_path, note="", votes_raw=None):
    """复盘标注：在拼图网格上标注最终选择(粗红框，picked 可为单个 int 或 list)+各模型投票。"""
    try:
        from PIL import Image, ImageDraw
        im = Image.open(grid_path).convert("RGB")
        draw = ImageDraw.Draw(im)
        cells = (geom or {}).get("cells", [])
        picks = picked if isinstance(picked, (list, tuple)) else [picked]
        palette = [(0, 120, 255), (255, 140, 0), (0, 200, 0), (200, 0, 200), (0, 200, 200)]
        votes_raw = votes_raw or []
        for mi, item in enumerate(votes_raw):
            model, a = item[0], item[1]
            alist = a if isinstance(a, (list, tuple)) else ([a] if a is not None else [])
            col = palette[mi % len(palette)]
            for av in alist:
                if not (0 <= av < len(cells)):
                    continue
                x, y, w, h = cells[av]
                off = 8 + mi * 4
                draw.rectangle([x - off, y - off, x + w + off, y + h + off], outline=col, width=2)
            short = model.split("/")[-1].replace("-preview-c", "").replace("-c", "")[:10]
            draw.text((6, 14 + mi * 12), f"{short}>{alist}", fill=col)
        for pv in picks:
            if pv is not None and 0 <= pv < len(cells):
                x, y, w, h = cells[pv]
                for t in range(5):
                    draw.rectangle([x - 3 - t, y - 3 - t, x + w + 3 + t, y + h + 3 + t], outline=(255, 0, 0))
                draw.text((x + 2, y + h - 14), f"#{pv}", fill=(255, 0, 0))
        if note:
            draw.text((6, im.height - 16), note[:140], fill=(255, 0, 0))
        im.save(out_path)
        return out_path
    except Exception as e:
        print(f"  [img] annotate err: {str(e)[:60]}")
        return None


def overlay_grid_numbers(image_b64, path, rows, cols,
                         top_frac=0.0, bottom_frac=0.0, left_frac=0.0, right_frac=0.0,
                         pad_frac=0.0):
    """在一张整图上按 rows×cols 画网格线 + 在每格左上角标 #0,#1...（reading order）。
    返回 (base64, geom)。geom 用画布像素坐标，cells_xy=[(cx,cy)...] 为每格中心（点击用）。
    四边内缩比例(top/bottom/left/right_frac)把网格框定到真实图块区(扣掉题干条/边距)。
    pad_frac: 兼容旧调用的四周等比额外内缩。"""
    try:
        from PIL import Image, ImageDraw
        import io
        im = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")
        W, H = im.width, im.height
        gx0 = int(W * (left_frac + pad_frac))
        gx1 = W - int(W * (right_frac + pad_frac))
        gy0 = int(H * (top_frac + pad_frac))
        gy1 = H - int(H * (bottom_frac + pad_frac))
        gw, gh = gx1 - gx0, gy1 - gy0
        cw, ch = gw / cols, gh / rows
        draw = ImageDraw.Draw(im)
        cells = []          # 每格中心（画布像素坐标，给点击用）
        boxes = []          # 每格框（标注复盘用）
        for r in range(rows):
            for c in range(cols):
                x = gx0 + c * cw
                y = gy0 + r * ch
                idx = r * cols + c
                draw.rectangle([x, y, x + cw, y + ch], outline=(255, 0, 0), width=2)
                draw.text((x + 4, y + 4), f"#{idx}", fill=(255, 0, 0))
                cells.append((int(x + cw / 2), int(y + ch / 2)))
                boxes.append((int(x), int(y), int(cw), int(ch)))
        im.save(path)
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        geom = {"cells_xy": cells, "boxes": boxes, "size": (W, H), "rows": rows, "cols": cols}
        return b64, geom
    except Exception as e:
        print(f"  [img] overlay_grid err: {str(e)[:60]}")
        return image_b64, None


def detect_shortest_bead_chain_cells(image_b64, geom, count=2):
    """Map the shortest bright bead chains in a dark canvas to grid click cells."""
    try:
        import io
        import math
        from PIL import Image

        image = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")
        boxes = list((geom or {}).get("boxes") or [])
        centers = list((geom or {}).get("cells_xy") or [])
        if not boxes or len(boxes) != len(centers):
            return []

        left = max(0, min(box[0] for box in boxes))
        top = max(0, min(box[1] for box in boxes))
        right = min(image.width, max(box[0] + box[2] for box in boxes))
        bottom = min(image.height, max(box[1] + box[3] for box in boxes))
        grid_area = max(1, (right - left) * (bottom - top))

        brightness = []
        for y in range(top, bottom):
            for x in range(left, right):
                brightness.append(max(image.getpixel((x, y))))
        brightness.sort()
        background_high = brightness[int(len(brightness) * 0.80)]
        bright_floor = max(135, min(190, background_high + 30))

        mask = set()
        for y in range(top, bottom):
            for x in range(left, right):
                red, green, blue = image.getpixel((x, y))
                if (
                    max(red, green, blue) >= bright_floor
                    and max(red, green, blue) - min(red, green, blue) >= 16
                ):
                    mask.add((x, y))

        min_component_area = max(12, int(grid_area * 0.00035))
        max_component_area = max(min_component_area + 1, int(grid_area * 0.004))
        beads = []
        while mask:
            seed = mask.pop()
            queue = [seed]
            pixels = [seed]
            for px, py in queue:
                for neighbor in (
                    (px + 1, py), (px - 1, py),
                    (px, py + 1), (px, py - 1),
                ):
                    if neighbor in mask:
                        mask.remove(neighbor)
                        queue.append(neighbor)
                        pixels.append(neighbor)
            size = len(pixels)
            if not min_component_area <= size <= max_component_area:
                continue
            xs = [pixel[0] for pixel in pixels]
            ys = [pixel[1] for pixel in pixels]
            width = max(xs) - min(xs) + 1
            height = max(ys) - min(ys) + 1
            if max(width, height) > 2.2 * max(1, min(width, height)):
                continue
            if size / max(1, width * height) < 0.25:
                continue
            beads.append({
                "x": sum(xs) / size,
                "y": sum(ys) / size,
                "diameter": max(width, height),
            })

        if len(beads) < count * 3:
            return []
        diameters = sorted(bead["diameter"] for bead in beads)
        median_diameter = diameters[len(diameters) // 2]
        link_distance = max(8.0, median_diameter * 1.7)

        remaining = set(range(len(beads)))
        chains = []
        while remaining:
            seed = remaining.pop()
            queue = [seed]
            chain = [seed]
            for current in queue:
                linked = []
                for candidate in remaining:
                    distance = math.hypot(
                        beads[current]["x"] - beads[candidate]["x"],
                        beads[current]["y"] - beads[candidate]["y"],
                    )
                    if distance <= link_distance:
                        linked.append(candidate)
                for candidate in linked:
                    remaining.remove(candidate)
                    queue.append(candidate)
                    chain.append(candidate)
            if len(chain) >= 3:
                chains.append(chain)

        if len(chains) < count:
            return []
        selected = sorted(chains, key=len)[:count]
        picked = []
        for chain in selected:
            candidates = []
            for cell_index, (cx, cy) in enumerate(centers):
                if cell_index in picked:
                    continue
                distance = min(
                    math.hypot(cx - beads[index]["x"], cy - beads[index]["y"])
                    for index in chain
                )
                candidates.append((distance, cell_index))
            if not candidates:
                return []
            picked.append(min(candidates)[1])
        return sorted(picked)
    except Exception as error:
        print(f"  [img] bead-chain detection err: {str(error)[:60]}")
        return []


def annotate_canvas_choice(grid_path, geom, picked, out_path, note="", votes_raw=None):
    """canvas_grid 复盘：在已编号网格图上，粗红框标最终选择 + 各模型投票彩框。
    geom 用 overlay_grid_numbers 的 boxes 字段。"""
    try:
        from PIL import Image, ImageDraw
        im = Image.open(grid_path).convert("RGB")
        draw = ImageDraw.Draw(im)
        boxes = (geom or {}).get("boxes", [])
        picks = picked if isinstance(picked, (list, tuple)) else [picked]
        palette = [(0, 120, 255), (255, 140, 0), (0, 200, 0), (200, 0, 200), (0, 200, 200)]
        for mi, item in enumerate(votes_raw or []):
            model, a = item[0], item[1]
            alist = a if isinstance(a, (list, tuple)) else ([a] if a is not None else [])
            col = palette[mi % len(palette)]
            for av in alist:
                if 0 <= av < len(boxes):
                    x, y, w, h = boxes[av]
                    off = 6 + mi * 4
                    draw.rectangle([x + off, y + off, x + w - off, y + h - off], outline=col, width=2)
            short = model.split("/")[-1][:12]
            draw.text((6, 6 + mi * 12), f"{short}>{alist}", fill=col)
        for pv in picks:
            if pv is not None and 0 <= pv < len(boxes):
                x, y, w, h = boxes[pv]
                for t in range(4):
                    draw.rectangle([x + t, y + t, x + w - t, y + h - t], outline=(255, 0, 0))
        if note:
            draw.text((6, im.height - 16), note[:140], fill=(255, 0, 0))
        im.save(out_path)
        return out_path
    except Exception as e:
        print(f"  [img] annotate_canvas err: {str(e)[:60]}")
        return None


def parse_json_answer(text):
    """从 LLM 回复里抠出最后一个平衡的 JSON 对象（容忍 ```json 包裹和前后文字）。"""
    if not text:
        return None
    candidates = []
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    candidates.append(text[start:i + 1])
    for blob in reversed(candidates):
        try:
            return json.loads(blob)
        except Exception:
            continue
    return None
