# -*- coding: utf-8 -*-
"""
vision_solver/vision.py — 视觉 LLM 问询 + 多模型投票内核（通用版）

从 common/agent_captcha.py 的内核【复制】而来，彻底解耦、独立演进。
负责：把 prompt + 图片发给视觉大模型，多模型并发投票、多数表决，返回结构化答案。

网关/key 全部从 config(.env) 读取，不在代码里留明文（复用现有视觉网关变量：
VISION_API_BASE/VISION_API_KEY/VISION_MODEL、VOTE_ZZ_*/VOTE_GPT_*/VOTE_OPUS_*、GEMMA_*）。
"""

import os
import re
import sys
import time

import requests

# 触发 .env 加载并读取网关变量（config 在仓库根目录）。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
try:
    import config as _cfg
except Exception:
    _cfg = None


def _c(name, default=""):
    """优先环境变量，其次 config.<name>，再 default。"""
    v = os.environ.get(name)
    if v:
        return v
    if _cfg is not None:
        v = getattr(_cfg, name, "")
        if v:
            return v
    return default


# 主视觉网关（gpt-5.x）。模型可用 VISION_MODEL 覆盖。
VISION_API_BASE = _c("VISION_API_BASE")
VISION_API_KEY = _c("VISION_API_KEY")
PRIMARY_MODEL = _c("VISION_MODEL", "gpt-5.5")
FALLBACK_MODELS = ["gpt-5.4-mini"]
GEMMA_API_BASE = _c("GEMMA_API_BASE")
GEMMA_API_KEY = _c("GEMMA_API_KEY")

# 模型有时把验证码当"不可协助"拒答；命中这些词就判定为拒答、换下一个 model/key。
_REFUSAL_MARKERS = (
    "cannot fulfill", "can't fulfill", "cannot assist", "can't assist",
    "i am unable", "i'm unable", "safety guidelines", "harmless ai",
    "not able to help", "cannot help with that",
)


def looks_like_refusal(text):
    if not text:
        return True
    t = text.lower()
    return any(m in t for m in _REFUSAL_MARKERS)


def _load_keys():
    keys = []
    if VISION_API_KEY:
        keys.append(VISION_API_KEY)
    if GEMMA_API_KEY and GEMMA_API_KEY not in keys:
        keys.append(GEMMA_API_KEY)
    env = os.environ.get("OPENROUTER_KEYS") or os.environ.get("OPENROUTER_KEY") or ""
    for k in env.replace("\n", ",").split(","):
        k = k.strip()
        if k.startswith("sk-or-") and k not in keys:
            keys.append(k)
    return keys


def _endpoint_for_key(key):
    if key.startswith("sk-or-"):
        return "https://openrouter.ai/api/v1/chat/completions"
    if key == GEMMA_API_KEY:
        return f"{GEMMA_API_BASE.rstrip('/')}/v1/chat/completions"
    return f"{VISION_API_BASE.rstrip('/')}/v1/chat/completions"


def _model_for_key(key, model):
    if key == GEMMA_API_KEY and model.startswith("gpt-"):
        return "google/gemma-4-31b-it:free"
    if key.startswith("sk-or-") and model.startswith("gpt-"):
        return "nvidia/nemotron-nano-12b-v2-vl:free"
    return model


def ask_vision(prompt, image_b64, models=None, keys=None, max_tokens=900, temperature=0.0):
    """把 prompt + 图片发给视觉 LLM，返回文本答案或 None。按 models 降级、按 keys 轮换。"""
    models = models or ([PRIMARY_MODEL] + FALLBACK_MODELS)
    keys = keys or _load_keys()
    if not keys:
        print("  [vision] 无可用 key")
        return None
    mtype = "image/jpeg" if image_b64.startswith("/9j/") else "image/png"
    payload_msg = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mtype};base64,{image_b64}"}},
        ],
    }]
    for model in models:
        for ki, key in enumerate(keys):
            real_model = _model_for_key(key, model)
            try:
                r = requests.post(
                    _endpoint_for_key(key),
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={"model": real_model, "messages": payload_msg,
                          "max_tokens": max_tokens, "temperature": temperature},
                    timeout=120,
                )
                if r.status_code == 200:
                    txt = r.json()["choices"][0]["message"]["content"]
                    if looks_like_refusal(txt):
                        print(f"  [vision] {real_model} key#{ki} refused, next")
                        continue
                    return txt
                if r.status_code in (404, 429, 500, 502, 503):
                    continue
                print(f"  [vision] {real_model} key#{ki} -> {r.status_code} {r.text[:80]}")
            except Exception as e:
                print(f"  [vision] {real_model} key#{ki} err: {str(e)[:60]}")
                continue
    print("  [vision] 所有 model/key 都失败")
    return None


# ---------------- 多模型投票池 ----------------
ZZ_BASE = _c("VOTE_ZZ_BASE")
ZZ_KEY = _c("VOTE_ZZ_KEY")
GPT_KEY = _c("VOTE_GPT_KEY") or ZZ_KEY
OPUS_BASE = _c("VOTE_OPUS_BASE")
OPUS_KEY = _c("VOTE_OPUS_KEY")
VOTER_MODELS = [(b, k, m) for (b, k, m) in [
    (ZZ_BASE, ZZ_KEY, "gemini-3.5-flash-c"),
    (ZZ_BASE, GPT_KEY, "gpt-5.5"),
    (ZZ_BASE, ZZ_KEY, "gemini-3.1-pro-preview-c"),
    (OPUS_BASE, OPUS_KEY, "claude-opus-4-8"),
] if b and k]


def _ask_one(base, key, model, prompt, image_b64, max_tokens=900):
    """单模型单网关问一次。base 以 /claude 结尾或含 /v1/messages -> Anthropic 原生；否则 OpenAI 兼容。"""
    if not base or not key:
        return None
    mtype = "image/jpeg" if image_b64.startswith("/9j/") else "image/png"
    for attempt in range(1, 4):
        try:
            is_anthropic = base.rstrip("/").endswith("/claude") or "/v1/messages" in base
            if is_anthropic:
                ep = base.rstrip("/")
                if not ep.endswith("/v1/messages"):
                    ep = ep + "/v1/messages"
                r = requests.post(
                    ep,
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image", "source": {"type": "base64", "media_type": mtype, "data": image_b64}}]}]},
                    timeout=20,
                )
                if r.status_code == 200:
                    txt = "".join(b.get("text", "") for b in r.json().get("content", []))
                    if txt and not looks_like_refusal(txt):
                        return txt
                if r.status_code not in (408, 429, 500, 502, 503, 504):
                    return None
            else:
                r = requests.post(
                    f"{base.rstrip('/')}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={"model": model, "messages": [{"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mtype};base64,{image_b64}"}}]}],
                          "max_tokens": max_tokens, "temperature": 0.0},
                    timeout=20,
                )
                if r.status_code == 200:
                    txt = r.json()["choices"][0]["message"]["content"]
                    if not looks_like_refusal(txt):
                        return txt
                if r.status_code not in (408, 429, 500, 502, 503, 504):
                    return None
        except Exception as e:
            print(f"  [vote] {model} attempt {attempt}/3 err: {str(e)[:50]}")
        if attempt < 3:
            time.sleep(attempt)
    if not is_anthropic:
        try:
            from curl_cffi import requests as curl_requests

            r = curl_requests.post(
                f"{base.rstrip('/')}/v1/chat/completions",
                impersonate="chrome131",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:{mtype};base64,{image_b64}"
                        }},
                    ]}],
                    "max_tokens": max_tokens,
                    "temperature": 0.0,
                },
                timeout=35,
            )
            if r.status_code == 200:
                txt = r.json()["choices"][0]["message"]["content"]
                if not looks_like_refusal(txt):
                    print(f"  [vote] {model} recovered through Chrome TLS")
                    return txt
        except Exception as e:
            print(f"  [vote] {model} Chrome TLS fallback err: {str(e)[:50]}")
    return None


def _parse_index(txt, n_options):
    """从回复抠 ANSWER=N（单选）。"""
    if not txt:
        return None
    mm = re.findall(r"ANSWER\s*=\s*(\d+)", txt)
    if mm:
        a = int(mm[-1])
        if 0 <= a < n_options:
            return a
    return None


def _parse_picklist(txt, n_options):
    """从回复抠 PICK=[i,j,...]（多选网格）。返回去重排序后的合法索引列表，或 None。"""
    if not txt:
        return None
    m = re.findall(
        r"\bPICK\b\s*[:=]\s*\[([0-9,\s]*)\]",
        txt,
        flags=re.IGNORECASE,
    )
    if not m:
        answer = re.findall(
            r"\bANSWER\b\s*[:=]\s*(\d+)", txt, flags=re.IGNORECASE
        )
        if answer:
            value = int(answer[-1])
            return [value] if 0 <= value < n_options else None
        tail = txt[-320:]
        if not re.search(
            r"\b(?:click|select|pick|answer|cell|cells|tile|tiles)\b",
            tail,
            flags=re.IGNORECASE,
        ):
            return None
        explicit_cells = re.findall(r"#\s*(\d+)", tail)
        if not explicit_cells:
            explicit_cells = re.findall(
                r"\b(?:cell|tile)\s*(\d+)\b", tail, flags=re.IGNORECASE
            )
        idxs = []
        for part in explicit_cells:
            value = int(part)
            if 0 <= value < n_options and value not in idxs:
                idxs.append(value)
        return sorted(idxs) if idxs else None
    raw = m[-1]
    idxs = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            v = int(part)
            if 0 <= v < n_options and v not in idxs:
                idxs.append(v)
    return sorted(idxs)


def _parse_points(txt):
    """从回复抠拖拽两点：FROM=(x,y) TO=(x,y)，坐标是 0..1 归一化(相对画布左上)。
    返回 ((fx,fy),(tx,ty)) 或 None。容忍 [] 包裹、空格、百分号。"""
    if not txt:
        return None
    def _grab(label):
        m = re.findall(label + r"\s*=\s*[\(\[]?\s*([0-9.]+)\s*[,\s]\s*([0-9.]+)\s*[\)\]]?", txt, re.I)
        if not m:
            return None
        x, y = float(m[-1][0]), float(m[-1][1])
        # 容忍模型给 0..100 百分数
        if x > 1.5 or y > 1.5:
            x, y = x / 100.0, y / 100.0
        if 0 <= x <= 1 and 0 <= y <= 1:
            return (x, y)
        return None
    fr = _grab("FROM")
    to = _grab("TO")
    if fr and to:
        return (fr, to)
    return None


def vote_points(prompt, image_b64, max_tokens=900, deadline=55):
    """多模型并发投票（拖拽两点）：各自给 FROM/TO 归一化坐标，取各点中位数(抗离群)。
    返回 ((fx,fy),(tx,ty), raw_list) 或 (None,None,raws)。"""
    import concurrent.futures as cf
    from statistics import median
    raws = []
    froms, tos = [], []
    if not VOTER_MODELS:
        txt = ask_vision(prompt, image_b64, max_tokens=max_tokens)
        pts = _parse_points(txt)
        if pts:
            return pts[0], pts[1], [("primary", pts, (txt or "")[-160:])]
        return None, None, [("primary", None, (txt or "")[-160:])]
    done = set()
    with cf.ThreadPoolExecutor(max_workers=len(VOTER_MODELS)) as ex:
        futs = {ex.submit(_ask_one, b, k, m, prompt, image_b64, max_tokens): m
                for (b, k, m) in VOTER_MODELS}
        try:
            for fut in cf.as_completed(futs, timeout=deadline):
                model = futs[fut]
                done.add(model)
                txt = fut.result()
                pts = _parse_points(txt)
                if pts:
                    froms.append(pts[0]); tos.append(pts[1])
                raws.append((model, pts, (txt or "")[-160:]))
                print(f"  [vote] {model} -> {pts}")
        except cf.TimeoutError:
            for fut, model in futs.items():
                if model not in done:
                    fut.cancel()
                    raws.append((model, None, "[deadline timeout]"))
    if not froms:
        return None, None, raws
    fx = median(p[0] for p in froms); fy = median(p[1] for p in froms)
    tx = median(p[0] for p in tos);   ty = median(p[1] for p in tos)
    return (fx, fy), (tx, ty), raws


def vote_answer(prompt, image_b64, n_options, max_tokens=900, deadline=55):
    """多模型并发投票（单选）：解析各自 ANSWER=N，多数票胜出，平票按 VOTER_MODELS 顺序。
    返回 (best, votes_dict, raw_list)。"""
    import concurrent.futures as cf
    from collections import Counter
    answers = []
    raws = []
    done = set()
    if not VOTER_MODELS:
        # 投票池没配 -> 退化为单模型 ask_vision
        txt = ask_vision(prompt, image_b64, max_tokens=max_tokens)
        a = _parse_index(txt, n_options)
        return a, ({a: 1} if a is not None else {}), [("primary", a, (txt or "")[-120:])]
    with cf.ThreadPoolExecutor(max_workers=len(VOTER_MODELS)) as ex:
        futs = {ex.submit(_ask_one, b, k, m, prompt, image_b64, max_tokens): m
                for (b, k, m) in VOTER_MODELS}
        try:
            for fut in cf.as_completed(futs, timeout=deadline):
                model = futs[fut]
                done.add(model)
                txt = fut.result()
                a = _parse_index(txt, n_options)
                if a is not None:
                    answers.append((model, a))
                raws.append((model, a, (txt or "")[-120:]))
                print(f"  [vote] {model} -> {a}")
        except cf.TimeoutError:
            for fut, model in futs.items():
                if model not in done:
                    fut.cancel()
                    raws.append((model, None, "[deadline timeout]"))
    if not answers:
        return None, {}, raws
    cnt = Counter(a for _, a in answers)
    top, topn = cnt.most_common(1)[0]
    order = [m for (_, _, m) in VOTER_MODELS]
    if list(cnt.values()).count(topn) > 1:
        for m in order:
            for mm, a in answers:
                if mm == m and cnt[a] == topn:
                    top = a
                    break
            else:
                continue
            break
    return top, dict(cnt), raws


def vote_picklist(prompt, image_b64, n_options, max_tokens=900, deadline=55, min_votes=2):
    """多模型并发投票（多选网格）：解析各自 PICK=[...]，按索引计票，得票 >= min_votes 的入选。
    单模型池时直接用该模型的 PICK。返回 (picked_list, votes_per_index, raw_list)。"""
    import concurrent.futures as cf
    from collections import Counter
    per_index = Counter()
    raws = []
    n_voters = 0
    if not VOTER_MODELS:
        txt = ask_vision(prompt, image_b64, max_tokens=max_tokens)
        parsed = _parse_picklist(txt, n_options)
        picks = parsed or []
        return picks, {i: 1 for i in picks}, [("primary", parsed, (txt or "")[-160:])]
    done = set()
    with cf.ThreadPoolExecutor(max_workers=len(VOTER_MODELS)) as ex:
        futs = {ex.submit(_ask_one, b, k, m, prompt, image_b64, max_tokens): m
                for (b, k, m) in VOTER_MODELS}
        try:
            for fut in cf.as_completed(futs, timeout=deadline):
                model = futs[fut]
                done.add(model)
                txt = fut.result()
                picks = _parse_picklist(txt, n_options)
                if picks is not None:
                    n_voters += 1
                    for i in picks:
                        per_index[i] += 1
                raws.append((model, picks, (txt or "")[-160:]))
                print(f"  [vote] {model} -> PICK {picks}")
        except cf.TimeoutError:
            for fut, model in futs.items():
                if model not in done:
                    fut.cancel()
                    raws.append((model, None, "[deadline timeout]"))
    if n_voters == 0:
        return [], {}, raws
    # 单模型回票时 min_votes 降到 1（否则永远选不出）
    thr = 1 if n_voters < 2 else min_votes
    picked = sorted([i for i, c in per_index.items() if c >= thr])
    return picked, dict(per_index), raws
