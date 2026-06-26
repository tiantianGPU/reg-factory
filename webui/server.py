# -*- coding: utf-8 -*-
"""
webui/server.py — reg-factory 本地 Web 面板后端(FastAPI)。

只绑 127.0.0.1(含 .env 密钥编辑，绝不监听公网)。职责：
  - 提供脚本 schema / .env 配置 给前端渲染表单
  - 把表单提交拼成命令行，subprocess 后台跑，SSE 实时推 stdout
  - 探测 BitBrowser / Clash 在线状态 + 当前节点

启动：  python -m uvicorn webui.server:app --port 8799   (或用 start.bat)
"""
import asyncio
import os
import sys
import time
import urllib.request

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# 项目根 = webui 的上一级
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBUI = os.path.join(ROOT, "webui")
ENV_PATH = os.path.join(ROOT, ".env")
ENV_EXAMPLE = os.path.join(ROOT, ".env.example")

sys.path.insert(0, WEBUI)
sys.path.insert(0, ROOT)
import scripts as schema  # noqa: E402


def _ensure_proxy_env():
    """接码等公网服务直连不通(sms-man 直连超时)，必须经 Clash。把 CLASH_PROXY 注进本进程
    环境，让 common.sms 的 requests(trust_env) 自动走代理；localhost API 直连(NO_PROXY)。"""
    proxy = ""
    try:
        proxy = _read_config_val("CLASH_PROXY", "http://127.0.0.1:7897")
    except Exception:
        proxy = "http://127.0.0.1:7897"
    if proxy and not os.environ.get("HTTPS_PROXY"):
        os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = proxy
        os.environ["http_proxy"] = os.environ["https_proxy"] = proxy
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost,::1"


app = FastAPI(title="reg-factory WebUI")

# 运行中的任务：run_id -> {proc, lines:[], done:bool, script, cmd, started}
RUNS = {}
_run_seq = [0]

# 接码助手：内存记录当前租用的 sms-man 号  pkey -> {phone, rented_at, codes:[], service}
SMS_RENTS = {}
SMS_RENT_TTL = 1200  # 20 分钟租期(秒)


# ============================================================ 配置/状态读取
def _read_config_val(key, default=""):
    """从环境/.env 读一个值(用于探测 Clash/BitBrowser 地址)。"""
    val = os.environ.get(key)
    if val:
        return val
    try:
        for line in open(ENV_PATH, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if k.strip() == key:
                    return v.strip().strip('"').strip("'") or default
    except Exception:
        pass
    return default


def _http_alive(url, timeout=3):
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status < 500
    except urllib.error.HTTPError:
        return True  # 4xx = 服务活着(拒绝裸请求)
    except Exception:
        return False


# ============================================================ .env 读写(保留注释/顺序)
def _parse_env_file(path):
    out = {}
    if not os.path.isfile(path):
        return out
    for line in open(path, encoding="utf-8"):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _write_env_file(path, updates):
    """把 updates(dict) 写回 .env：已存在的行原地改值(保留注释/顺序)，新 key 追加到末尾。"""
    lines = []
    seen = set()
    if os.path.isfile(path):
        lines = open(path, encoding="utf-8").read().splitlines()
    out = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.partition("=")[0].strip()
            if k in updates:
                out.append(f"{k}={updates[k]}")
                seen.add(k)
                continue
        out.append(line)
    # 新增的 key
    extra = [k for k in updates if k not in seen]
    if extra:
        out.append("")
        out.append("# ---- 由 WebUI 配置页新增 ----")
        for k in extra:
            out.append(f"{k}={updates[k]}")
    # 原子写
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.replace(tmp, path)


# ============================================================ 连通测试
def _direct_get(url, headers=None, timeout=8):
    """直连 GET(显式绕过代理——Clash 控制器/BitBrowser 都是 localhost)。
    返回 (status_code, body_text)。连不上抛异常。"""
    handler = urllib.request.ProxyHandler({})  # 空 = 不走任何代理
    opener = urllib.request.build_opener(handler)
    req = urllib.request.Request(url, headers=headers or {})
    with opener.open(req, timeout=timeout) as r:
        return r.status, r.read(8192).decode("utf-8", "replace")


def _test_clash():
    """测 Clash 控制器：GET /version 带 Bearer secret。区分 连不上 / 密码错 / OK。"""
    api = _read_config_val("CLASH_API", "http://127.0.0.1:9097").rstrip("/")
    secret = _read_config_val("CLASH_SECRET", "")
    headers = {"Authorization": f"Bearer {secret}"} if secret else {}
    try:
        code, body = _direct_get(api + "/version", headers=headers, timeout=6)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "密码(secret)错误或未设置 —— 检查 CLASH_SECRET"
        return False, f"控制器返回 HTTP {e.code}"
    except Exception as e:
        return False, f"连不上控制器({api})：{str(e)[:60]}。确认 Clash Verge 已开 External Controller"
    ver = ""
    try:
        import json as _j
        ver = _j.loads(body).get("version", "")
    except Exception:
        pass
    # 顺带报当前节点
    node = ""
    try:
        from common import proxy_switch as ps
        node = ps.current_node() or ""
    except Exception:
        pass
    return True, f"控制器连通 ✓ 内核版本 {ver}" + (f"，当前节点 {node}" if node else "")


def _test_bitbrowser():
    """测 BitBrowser 本地 API：探健康端点。"""
    api = _read_config_val("BITBROWSER_API", "http://127.0.0.1:54345").rstrip("/")
    # BitBrowser 的 /health 返回 200；裸根路径也活
    for path in ("/health", "/"):
        try:
            code, _ = _direct_get(api + path, timeout=5)
            return True, f"BitBrowser API 连通 ✓ (HTTP {code})"
        except urllib.error.HTTPError:
            return True, "BitBrowser API 在线 ✓ (服务响应)"
        except Exception as e:
            last = str(e)[:60]
    return False, f"连不上 BitBrowser({api})：{last}。确认比特浏览器客户端已启动"


def _proxied_get(url, timeout=20):
    """经 Clash 代理 GET(sms-man/firefox 等公网接码服务直连不通，必须走代理)。
    返回 (status, body_text)。"""
    proxy = _read_config_val("CLASH_PROXY", "http://127.0.0.1:7897")
    handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy}) if proxy else urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(handler)
    with opener.open(url, timeout=timeout) as r:
        return r.status, r.read(4096).decode("utf-8", "replace")


def _test_smsman():
    """测 sms-man 接码：经代理查询(直连超时)。get-balance 偶发 500，回退查 applications 验 token。"""
    token = _read_config_val("SMSMAN_TOKEN", "")
    if not token:
        return False, "未配置 SMSMAN_TOKEN"
    base = _read_config_val("SMSMAN_API_BASE", "https://api.sms-man.com/control").rstrip("/")
    import json as _j
    last = ""
    # get-balance 偶发 500/HTML 故障页，回退 applications；都试，识别"服务端故障"
    for path, pretty in (("get-balance", "balance"), ("applications", "applications")):
        try:
            code, body = _proxied_get(base + f"/{path}?" + urllib.parse.urlencode({"token": token}), timeout=18)
            b = body.lstrip()
            if b.startswith("<") or "<html" in b[:200].lower():
                last = f"sms-man 返回错误页(HTTP {code})——平台接口暂时故障/限流，非 token 问题，稍后再试"
                continue
            d = _j.loads(body)
            if path == "get-balance" and isinstance(d, dict) and ("balance" in d or "money" in d):
                return True, f"sms-man 连通 ✓ 余额 {d.get('balance') or d.get('money')}"
            if path == "applications":
                n = len(d) if isinstance(d, (dict, list)) else 0
                if n and not (isinstance(d, dict) and d.get("error_code")):
                    return True, f"sms-man 连通 ✓ (token 有效，服务数 {n})"
            if isinstance(d, dict) and (d.get("error_code") or d.get("error_msg")):
                return False, f"sms-man token 无效：{d.get('error_msg') or d.get('error_code')}"
        except Exception as e:
            last = f"sms-man 请求失败(经代理)：{str(e)[:70]}。确认 Clash 在线"
    return False, last or "sms-man 无有效响应(平台可能故障,稍后再试)"


def _test_firefox():
    """测 firefox.fun 接码：用 token 查询(getBalance 类)。"""
    token = _read_config_val("SMS_TOKEN", "")
    if not token:
        return False, "未配置 SMS_TOKEN"
    base = _read_config_val("SMS_API_BASE", "http://www.firefox.fun/yhapi.ashx")
    try:
        code, body = _proxied_get(base + "?" + urllib.parse.urlencode({"act": "getuserinfo", "token": token}), timeout=15)
        body = body.strip()
        # firefox 返回 1|... 表示成功，0|... 表示错误
        if body.startswith("1"):
            return True, f"firefox.fun 连通 ✓ {body[:80]}"
        return False, f"firefox.fun 返回：{body[:80]}（token 可能无效）"
    except Exception as e:
        return False, f"firefox.fun 请求失败：{str(e)[:80]}"


_TESTERS = {
    "clash": _test_clash,
    "bitbrowser": _test_bitbrowser,
    "smsman": _test_smsman,
    "firefox": _test_firefox,
}


@app.post("/api/test/{target}")
async def api_test(target: str, request: Request):
    # 先把页面上当前(可能未保存的)配置临时写进环境，让测试用最新值
    try:
        data = await request.json()
    except Exception:
        data = {}
    overrides = (data or {}).get("env") or {}
    saved = {}
    allowed = set(schema.env_keys()) | {"SMSMAN_API_BASE", "SMS_API_BASE"}
    for k, v in overrides.items():
        if k in allowed and v not in (None, ""):
            saved[k] = os.environ.get(k)
            os.environ[k] = str(v)
    try:
        fn = _TESTERS.get(target)
        if not fn:
            return JSONResponse({"ok": False, "msg": f"未知测试目标: {target}"}, status_code=400)
        ok, msg = await asyncio.to_thread(fn)
        return {"ok": ok, "msg": msg}
    finally:
        # 还原临时覆盖(不污染进程环境；真正保存走 /api/env)
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


# ============================================================ API
@app.get("/api/scripts")
def api_scripts():
    return {"scripts": schema.SCRIPTS}


@app.get("/api/links")
def api_links():
    return {"links": getattr(schema, "EXTERNAL_LINKS", [])}


@app.get("/api/embeds")
def api_embeds():
    return {"embeds": getattr(schema, "EMBED_PAGES", [])}


# ============================================================ 邮箱池批量导入
EMAILS_FILE = os.path.join(ROOT, "emails.txt")
import re as _re
_EMAIL_RE = _re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _parse_mail_line(line):
    """把一行拆成 [email, password, token, client_id]。兼容分隔符：----(多横线)/制表符/逗号/竖线/空格。
    email+密码必填，否则返回 None。"""
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    # 统一各种分隔符成 \x00：先处理 2+ 连字符，再 tab/逗号/竖线
    norm = _re.sub(r"-{2,}", "\x00", s)
    norm = _re.sub(r"[\t,|]+", "\x00", norm)
    parts = [p.strip() for p in norm.split("\x00")]
    # 若没拆出多列(只有空格分隔)，退化用空白拆
    if len(parts) < 2:
        parts = [p.strip() for p in s.split() if p.strip()]
    parts = [p for p in parts if p != ""]
    if len(parts) < 2:
        return None
    email, password = parts[0], parts[1]
    if not _EMAIL_RE.match(email):
        return None
    token = parts[2] if len(parts) >= 3 else ""
    client_id = parts[3] if len(parts) >= 4 else ""
    # 去掉尾部空字段，避免写出 "email----pass--------"(多余空列)
    fields = [email, password, token, client_id]
    while len(fields) > 2 and fields[-1] == "":
        fields.pop()
    return fields


def _existing_emails():
    emails = set()
    if os.path.isfile(EMAILS_FILE):
        for line in open(EMAILS_FILE, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                emails.add(line.split("----")[0].strip().lower())
    return emails


@app.get("/api/mailpool")
def api_mailpool_get():
    total = 0
    if os.path.isfile(EMAILS_FILE):
        for line in open(EMAILS_FILE, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                total += 1
    return {"total": total}


@app.post("/api/mailpool")
async def api_mailpool_import(request: Request):
    data = await request.json()
    text = (data or {}).get("text") or ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    existing = _existing_emails()
    added, skipped, bad = 0, 0, 0
    bad_samples = []
    seen = set(existing)
    out_lines = []
    for ln in lines:
        if not ln.strip():
            continue
        parsed = _parse_mail_line(ln)
        if not parsed:
            bad += 1
            if len(bad_samples) < 5:
                bad_samples.append(ln.strip()[:60])
            continue
        email = parsed[0].lower()
        if email in seen:
            skipped += 1
            continue
        seen.add(email)
        out_lines.append("----".join(parsed))
        added += 1
    if out_lines:
        # 追加(确保前面有换行)
        need_nl = os.path.isfile(EMAILS_FILE) and os.path.getsize(EMAILS_FILE) > 0
        with open(EMAILS_FILE, "a", encoding="utf-8") as f:
            if need_nl:
                f.write("\n")
            f.write("\n".join(out_lines) + "\n")
    total = len(_existing_emails())
    return {"ok": True, "added": added, "skipped": skipped, "bad": bad,
            "bad_samples": bad_samples, "total": total}


# ============================================================ sms-man 接码助手
def _gmail_service_default():
    return _read_config_val("SMSMAN_APP_ID_GMAIL", "") or "google"


@app.post("/api/sms/rent")
async def api_sms_rent(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    service = (data or {}).get("service") or _gmail_service_default()
    country = str((data or {}).get("country") or "0")
    if not _read_config_val("SMSMAN_TOKEN", ""):
        return {"ok": False, "msg": "未配置 SMSMAN_TOKEN，请到配置页填写"}
    try:
        from common import sms
        res = await asyncio.to_thread(sms._smsman_get_phone, service, country, "", ())
    except Exception as e:
        return {"ok": False, "msg": f"租号异常: {str(e)[:120]}"}
    if not res:
        return {"ok": False, "msg": f"租号失败(服务 '{service}' 无货/余额不足/服务名错)。可在配置页测试 sms-man，或换服务名"}
    phone, pkey = res
    rented_at = time.time()
    SMS_RENTS[pkey] = {"phone": phone, "rented_at": rented_at, "codes": [], "service": service}
    return {"ok": True, "phone": phone, "pkey": pkey, "service": service, "ttl": SMS_RENT_TTL}


@app.post("/api/sms/code")
async def api_sms_code(request: Request):
    data = await request.json()
    pkey = (data or {}).get("pkey")
    rec = SMS_RENTS.get(pkey)
    if not rec:
        return {"ok": False, "msg": "无此租号(可能已释放)"}
    elapsed = time.time() - rec["rented_at"]
    if elapsed > SMS_RENT_TTL:
        return {"ok": False, "expired": True, "msg": "号码已超 20 分钟租期，请重新获取号码"}
    try:
        from common import sms
        since = rec["codes"][-1] if rec["codes"] else None
        # 留出余量不超过剩余租期
        budget = int(min(90, max(15, SMS_RENT_TTL - elapsed)))
        code = await asyncio.to_thread(sms.smsman_peek_code, pkey, budget, 5, False, since)
    except Exception as e:
        return {"ok": False, "msg": f"取码异常: {str(e)[:120]}"}
    if not code:
        return {"ok": False, "msg": "暂未收到新验证码(可稍后再点)", "codes": rec["codes"],
                "elapsed": int(elapsed)}
    if code not in rec["codes"]:
        rec["codes"].append(code)
    return {"ok": True, "code": code, "codes": rec["codes"], "elapsed": int(elapsed)}


@app.post("/api/sms/release")
async def api_sms_release(request: Request):
    data = await request.json()
    pkey = (data or {}).get("pkey")
    if pkey in SMS_RENTS:
        try:
            from common import sms
            await asyncio.to_thread(sms._smsman_release, pkey)
        except Exception:
            pass
        SMS_RENTS.pop(pkey, None)
    return {"ok": True}


@app.get("/api/sms/rents")
def api_sms_rents():
    now = time.time()
    out = []
    for pkey, rec in list(SMS_RENTS.items()):
        elapsed = now - rec["rented_at"]
        if elapsed > SMS_RENT_TTL + 60:
            SMS_RENTS.pop(pkey, None)  # 过期太久自动清理
            continue
        out.append({"pkey": pkey, "phone": rec["phone"], "service": rec.get("service"),
                    "codes": rec["codes"], "elapsed": int(elapsed),
                    "remain": max(0, int(SMS_RENT_TTL - elapsed))})
    return {"rents": out, "ttl": SMS_RENT_TTL}


@app.get("/", response_class=HTMLResponse)
def index():
    return open(os.path.join(WEBUI, "static", "index.html"), encoding="utf-8").read()


@app.get("/api/status")
def api_status():
    bb = _read_config_val("BITBROWSER_API", "http://127.0.0.1:54345")
    clash = _read_config_val("CLASH_API", "http://127.0.0.1:9097")
    node = None
    try:
        from common import proxy_switch as ps
        node = ps.current_node()
    except Exception:
        node = None
    return {
        "bitbrowser": _http_alive(bb),
        "clash": _http_alive(clash),
        "node": node,
        "running": sum(1 for r in RUNS.values() if not r["done"]),
    }


@app.get("/api/env")
def api_env_get():
    # 若无 .env 用模板兜底
    cur = _parse_env_file(ENV_PATH)
    if not cur and os.path.isfile(ENV_EXAMPLE):
        cur = _parse_env_file(ENV_EXAMPLE)
    groups = []
    for g in schema.ENV_SCHEMA:
        items = []
        for it in g["items"]:
            items.append({
                "key": it["key"],
                "value": cur.get(it["key"], ""),
                "required": it.get("required", False),
                "secret": it.get("secret", False),
                "help": it.get("help", ""),
                "default": it.get("default", ""),
            })
        groups.append({"group": g["group"], "tests": g.get("tests", []), "items": items})
    return {"groups": groups, "env_exists": os.path.isfile(ENV_PATH)}


@app.post("/api/env")
async def api_env_set(request: Request):
    data = await request.json()
    updates = data.get("env") or {}
    # 只接受 schema 里声明的 key，避免写入垃圾
    allowed = set(schema.env_keys())
    updates = {k: ("" if v is None else str(v)) for k, v in updates.items() if k in allowed}
    if not os.path.isfile(ENV_PATH) and os.path.isfile(ENV_EXAMPLE):
        # 首次保存：以模板为底
        import shutil
        shutil.copy(ENV_EXAMPLE, ENV_PATH)
    _write_env_file(ENV_PATH, updates)
    return {"ok": True, "saved": len(updates)}


def _build_cmd(script, args):
    """把前端提交的 args(dict) 按 schema 拼成命令行 list。"""
    cmd = [sys.executable, "-u", os.path.join(ROOT, script["file"])]
    positional = []
    by_flag = {a["flag"]: a for a in script["args"]}
    for flag, spec in by_flag.items():
        if flag not in args:
            continue
        val = args[flag]
        typ = spec["type"]
        if spec.get("positional"):
            if val not in (None, "", []):
                positional.append(str(val))
            continue
        if typ == "bool":
            if val:
                cmd.append(flag)
        elif typ == "multi":
            if val:
                cmd.append(flag)
                cmd.extend(str(v) for v in val)
        else:
            if val not in (None, "", []):
                cmd.append(flag)
                cmd.append(str(val))
    cmd.extend(positional)
    return cmd


def _child_env():
    """子进程环境：注入 PYTHONUNBUFFERED + 代理(对齐 run_full_flow.build_child_env)。
    proxy 走 .env 的 CLASH_PROXY；localhost API 直连(NO_PROXY)。"""
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    proxy = _read_config_val("CLASH_PROXY", "http://127.0.0.1:7897")
    if proxy:
        env["HTTP_PROXY"] = env["HTTPS_PROXY"] = proxy
        env["http_proxy"] = env["https_proxy"] = proxy
        env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost,::1"
    return env


@app.post("/api/run")
async def api_run(request: Request):
    data = await request.json()
    sid = data.get("script")
    args = data.get("args") or {}
    script = schema.script_by_id(sid)
    if not script:
        return JSONResponse({"error": f"未知脚本: {sid}"}, status_code=400)
    cmd = _build_cmd(script, args)
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=ROOT, env=_child_env(),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    _run_seq[0] += 1
    run_id = f"r{_run_seq[0]}"
    rec = {"proc": proc, "lines": [], "done": False, "script": sid,
           "cmd": " ".join(cmd), "started": time.strftime("%H:%M:%S")}
    RUNS[run_id] = rec

    async def _pump():
        try:
            async for raw in proc.stdout:
                rec["lines"].append(raw.decode("utf-8", "replace").rstrip("\n"))
                if len(rec["lines"]) > 5000:
                    rec["lines"] = rec["lines"][-4000:]
        except Exception as e:
            rec["lines"].append(f"[webui] 读取输出异常: {e}")
        finally:
            await proc.wait()
            rec["done"] = True
            rec["lines"].append(f"[webui] 进程结束 exit={proc.returncode}")

    asyncio.create_task(_pump())
    return {"run_id": run_id, "cmd": rec["cmd"]}


@app.get("/api/logs/{run_id}")
async def api_logs(run_id: str):
    rec = RUNS.get(run_id)
    if not rec:
        return JSONResponse({"error": "无此任务"}, status_code=404)

    async def _stream():
        idx = 0
        while True:
            lines = rec["lines"]
            while idx < len(lines):
                yield f"data: {lines[idx]}\n\n"
                idx += 1
            if rec["done"] and idx >= len(rec["lines"]):
                yield "event: done\ndata: end\n\n"
                break
            await asyncio.sleep(0.4)

    return StreamingResponse(_stream(), media_type="text/event-stream")


@app.post("/api/stop/{run_id}")
async def api_stop(run_id: str):
    rec = RUNS.get(run_id)
    if not rec:
        return JSONResponse({"error": "无此任务"}, status_code=404)
    if not rec["done"]:
        try:
            rec["proc"].terminate()
        except Exception:
            pass
    return {"ok": True}


_ensure_proxy_env()
app.mount("/static", StaticFiles(directory=os.path.join(WEBUI, "static")), name="static")
