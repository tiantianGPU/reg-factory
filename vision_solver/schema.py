# -*- coding: utf-8 -*-
"""
vision_solver/schema.py — CaptchaSpec：一类验证码的可配置定义

用户通过 CaptchaSpec 描述「这是什么验证码、长什么样、怎么操作」，solver 据此通用求解。
可用 dataclass 直接构造，或从 dict / json 文件加载（presets/ 下有预置）。
"""

import json
from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass
class CaptchaSpec:
    # —— 标识 ——
    name: str = "generic"
    # 哪个 iframe 承载挑战：url 含任一关键字即命中（空=在主页面，不找 frame）
    frame_match: List[str] = field(default_factory=list)

    # —— 模式 ——
    # grid_select：多选网格（reCAPTCHA/旧 hCaptcha：DOM 里有可点 tile，选中所有含目标的 tile + Verify）
    # single_pick：单选（Arkose 排序拼图，导航到第 best 张 + Submit）
    # canvas_grid：整块画布多/单选（新版 hCaptcha：挑战画进一个 <canvas>，无 DOM tile，
    #              截画布 -> 按行列切网格编号 -> 投票 PICK -> 按像素坐标点 canvas -> 提交）
    # canvas_drag：整块画布拖拽（把某元素拖到目标位置：截画布 -> 投票 FROM/TO 归一化坐标 ->
    #              换算像素 -> mouse down/move/up 拖放 -> 提交）
    mode: str = "grid_select"

    # —— 给模型的题目 prompt（{instruction} 会被页面读到的题干替换；留空用内置模板）——
    prompt: str = ""

    # —— 选择器 ——
    challenge_text_sel: str = ""      # 题干文本元素（读 "select all X" 的 X），可空
    # grid_select 用：
    tile_sel: str = ""                # 每个可点格子（多选，按 DOM 顺序对应 #0,#1...）
    grid_image_sel: str = ""          # 整块挑战图（截一张整图给模型；与逐 tile 截图二选一）
    submit_sel: str = ""              # 提交/验证按钮（Verify / 确认）
    # single_pick 用：
    ref_sel: str = ""                 # 参考图（"match this"）
    cand_sel: str = ""                # 当前候选图
    next_btn_role: str = ""           # 下一张按钮的 aria role name（如 "Navigate to next image"）
    prev_btn_role: str = ""           # 上一张
    submit_role: str = ""             # 提交按钮 role name（如 "Submit"）
    # canvas_grid 用（新版 hCaptcha 整块画布）：
    canvas_sel: str = "canvas"        # 挑战画布元素
    rows: int = 3                     # 网格行数（题干含 card/different -> 自动按 1×3 覆盖）
    # 网格区在画布内的四边内缩比例（画布 = 题干条 + 内缩的图块区 + 底边距）。
    # 实测 hCaptcha 500×470：上≈0.30 下≈0.036 左右≈0.164。点 tile 中心，必须扣掉这些边距才准。
    grid_top_frac: float = 0.30       # 顶部内缩（题干/示例条占比）
    grid_bottom_frac: float = 0.036   # 底部内缩
    grid_left_frac: float = 0.164     # 左侧内缩
    grid_right_frac: float = 0.164    # 右侧内缩
    grid_pad_frac: float = 0.0        # 兼容旧字段（四周等比内缩，叠加在上面之外），一般留 0
    example_text_sel: str = ""        # 画布外的示例图说明（可空）

    # —— 行为 ——
    answer_format: str = "PICK_LIST"  # PICK_LIST(多选) | ANSWER_INDEX(单选)
    cols: int = 3                     # 拼网格列数 / 网格列数
    max_rounds: int = 6               # 一关最多几轮（reCAPTCHA 选完常出新图）
    deadline: int = 55                # 单轮投票最多等几秒
    answer_max_tokens: int = 900      # Vision response budget; lower for strict short answers
    settle_ms: int = 800              # 每步操作后等待（页面渲染）
    # 判通过：挑战 frame 消失 / 出现成功标志文本（任一命中即算过）
    success_gone_frame: bool = True
    success_markers: List[str] = field(default_factory=list)

    @staticmethod
    def from_dict(d: dict) -> "CaptchaSpec":
        valid = {f for f in CaptchaSpec.__dataclass_fields__}
        return CaptchaSpec(**{k: v for k, v in d.items() if k in valid})

    @staticmethod
    def from_json(path: str) -> "CaptchaSpec":
        with open(path, encoding="utf-8") as f:
            return CaptchaSpec.from_dict(json.load(f))

    def to_dict(self) -> dict:
        return asdict(self)


# 内置 prompt 模板（spec.prompt 为空时按 mode 选用；{instruction} 替换页面题干）
GRID_PROMPT = (
    "You are an accessibility helper reading an image-selection challenge. "
    "The image below is a grid of tiles labeled #0,#1,#2,... in reading order (left-to-right, top-to-bottom). "
    "Task: \"{instruction}\". "
    "Look at every tile and decide which tiles contain the requested object (even partially). "
    "Return exactly one line and no explanation: PICK=[a,b,c] "
    "(a JSON-style list of the matching tile numbers; use PICK=[] if none)."
)

SINGLE_PROMPT = (
    "You are an accessibility helper solving a visual matching puzzle. "
    "Top = REFERENCE (target). Below = numbered candidates #0..#{last}. "
    "Task: \"{instruction}\". Pick the ONE candidate that best matches the reference "
    "(match by underlying shape; ignore rotation and black/white fill inversion). "
    "Reason briefly, then output the VERY LAST line exactly: ANSWER=<number> (e.g. ANSWER=2)."
)

DRAG_PROMPT = (
    "You are an accessibility helper solving a drag-and-drop visual puzzle. "
    "The movable piece is usually the full-color character in a card on the right. "
    "The destination is exactly one dark silhouette among several candidates on the left. "
    "Compare the complete outer contour carefully, especially the head or snout, tail, "
    "arms, legs, and pose. Ignore colors, background grid bars, and other silhouettes. "
    "Task: \"{instruction}\". "
    "Give the pixel position to grab (the movable piece's center) and where to drop it (the target's center), "
    "as fractions 0..1 of the image (x from left edge, y from top edge). "
    "Use at most one short sentence of reasoning, then output the VERY LAST line EXACTLY as: FROM=(x,y) TO=(x,y) "
    "e.g. FROM=(0.20,0.55) TO=(0.78,0.40)."
)


def build_prompt(spec: CaptchaSpec, instruction: str, n_options: int) -> str:
    if spec.prompt:
        tmpl = spec.prompt
    elif spec.mode == "grid_select" or spec.mode == "canvas_grid":
        tmpl = GRID_PROMPT
    elif spec.mode == "canvas_drag":
        tmpl = DRAG_PROMPT
    else:
        tmpl = SINGLE_PROMPT
    return tmpl.replace("{instruction}", instruction or "") \
               .replace("{last}", str(max(0, n_options - 1))) \
               .replace("{n}", str(n_options))
