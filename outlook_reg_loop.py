"""Standalone Outlook registration loop. Continuously registers fresh
outlook accounts via BitBrowser + standalone register_outlook script, and
writes each success to _data_bundle/_outlook_pool/ as one JSON file per
record (email + password + session cookies).

The Replit batch (_batch_register.py / bs_register_step1.py) consumes these
via the `pool` email source — fully decoupled, so a slow self-reg attempt
never blocks the Replit signup pipeline.

Usage:
  python outlook_reg_loop.py                       # loop forever
  python outlook_reg_loop.py --count 20            # 20 attempts then exit
  python outlook_reg_loop.py --target-pool 10      # stop refilling once pool >= 10
  python outlook_reg_loop.py --max-press 5         # OUTLOOK_REG_MAX_PRESS
  python outlook_reg_loop.py --sleep 5             # gap between attempts (s)

Reads HTTP_PROXY env for Clash routing (host:port form). Set
SELF_REG_SCRIPT_PATH to override standalone script location.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import importlib.util
import urllib.request
from datetime import datetime

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ARTIFACT_DIR = os.path.dirname(os.path.abspath(__file__))
POOL_DIR = os.path.join(ARTIFACT_DIR, "_outlook_pool")
# 账号注册侧消费的池（common/emails.next_email 读取），格式 email----password----token----clientid
EMAILS_POOL = os.path.join(ARTIFACT_DIR, "emails.txt")
# 注册成功但 Graph refresh_token 抽取失败的号：邮箱+密码单独存这里，别丢。
# 之后可用 extract_graph_tokens.py 对这个文件补抽 RT，或浏览器登录直接用。
NO_GRAPH_POOL = os.path.join(ARTIFACT_DIR, "outlook_no_graph.txt")
DEFAULT_MAX_PRESS = "5"

STANDALONE_PATH = os.environ.get(
    "SELF_REG_SCRIPT_PATH",
    os.path.join(ARTIFACT_DIR, "register_outlook_standalone.py"),
)

# Optional Clash rotation between attempts. Without this, MS PerimeterX
# learns the egress IP after 1-2 signups and ERR_CONNECTION_CLOSEDs us out.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import _clash_verge  # type: ignore
except ImportError:
    _clash_verge = None


def log(msg, level="INFO"):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{level}] {msg}", flush=True)


def _env_truthy_norotate():
    """OUTLOOK_NO_ROTATE 环境变量：1/true/yes/on 任一即视为开启不轮换。"""
    return (os.environ.get("OUTLOOK_NO_ROTATE", "") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def ensure_clash_proxy_env():
    """Use .env CLASH_PROXY for direct loop runs, while keeping local APIs direct."""
    existing = (
        os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
        or ""
    ).strip()
    proxy = existing or os.environ.get("CLASH_PROXY", "").strip()
    if not proxy:
        return ""
    if not existing:
        os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = proxy
        os.environ["http_proxy"] = os.environ["https_proxy"] = proxy
    no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    required = ["127.0.0.1", "localhost", "::1"]
    parts = [p.strip() for p in no_proxy.split(",") if p.strip()]
    for item in required:
        if item not in parts:
            parts.append(item)
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = ",".join(parts)
    return proxy


def load_standalone():
    if not os.path.isfile(STANDALONE_PATH):
        log(f"standalone not found at {STANDALONE_PATH}", "ERR")
        sys.exit(1)
    spec = importlib.util.spec_from_file_location("_self_reg_standalone", STANDALONE_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    log(f"loaded standalone from {STANDALONE_PATH}")
    return m


def init_clash():
    """Connect to Clash controller. Returns (client, group_name) or (None, None)."""
    if _clash_verge is None:
        return None, None
    api = os.environ.get("CLASH_API", "").strip() or None
    secret = os.environ.get("CLASH_SECRET", "").strip()
    if not api:
        try:
            api = _clash_verge.auto_detect_api(secret=secret)
        except Exception as e:
            log(f"clash auto-detect failed: {e}", "WARN")
            return None, None
    if not api:
        return None, None
    try:
        client = _clash_verge.ClashClient(api=api, secret=secret)
    except Exception as e:
        log(f"clash client init failed: {e}", "WARN")
        return None, None
    group = (os.environ.get("CLASH_GROUP", "").strip() or "").strip()
    if not group or group.lower() == "auto":
        try:
            group = _clash_verge.auto_pick_group(client) or ""
        except Exception as e:
            log(f"clash auto-pick group failed: {e}", "WARN")
    if not group:
        log("clash: no usable group", "WARN")
        return None, None
    log(f"clash ready: api={api} group={group!r}")
    return client, group


# Clash 节点轮换排除名单：国内直连/大陆节点从中国 IP 出口，Outlook(MS PerimeterX)
# 对中国 IP 的按住验证基本必挂，且轮到它纯浪费一次 attempt，故从 GLOBAL 轮换里剔除。
# 子串匹配（节点名含任一即排除）。可经 CLASH_EXCLUDE_NODES 环境变量追加（逗号分隔）。
_CN_EXCLUDE_HINTS = ("国内直连", "直连", "DIRECT", "大陆", "国内", "China", "回国")

def _ordered_nodes(client, group, excluded):
    """按名称排序的可用节点列表(已排除 CN/excluded)。不做区域优先，节点平等轮换。"""
    try:
        nodes = [n for n in client.list_nodes(group) if n not in excluded]
    except Exception as e:
        log(f"list nodes err: {type(e).__name__}: {e}", "WARN")
        return []
    return sorted(nodes)


# 本会话已试过的节点(跨 attempt 累积)，保证逐个用，全用完再重置一轮。
_TRIED_NODES = set()


def _rotate_excluded(client, group):
    """把 CN/直连子串提示解析成 GLOBAL 组里真实节点名集合（pick_node 用精确匹配，
    故必须先列出实际节点名再按子串挑出要排除的）。CLASH_EXCLUDE_NODES 追加精确名。"""
    ex = set()
    extra = (os.environ.get("CLASH_EXCLUDE_NODES") or "").strip()
    if extra:
        ex |= {x.strip() for x in extra.replace("，", ",").split(",") if x.strip()}
    try:
        for name in client.list_nodes(group):
            if any(h in name for h in _CN_EXCLUDE_HINTS):
                ex.add(name)
    except Exception as e:
        log(f"resolve excluded nodes err: {type(e).__name__}: {e}", "WARN")
    return ex


def maybe_rotate(client, group, strategy="round_robin", max_latency_ms=6000,
                 mixed_port=7897):
    """切到下一个节点并验证出口 IP 变了。按名称顺序平等轮换：在 _ordered_nodes 排好
    的列表里挑第一个本会话没试过的节点，全试过则重置循环。排除 CN/直连。"""
    if client is None or not group:
        return None
    try:
        excluded = _rotate_excluded(client, group)
        ordered = _ordered_nodes(client, group, excluded)
        if not ordered:
            log("no usable node after exclude", "WARN")
            return None
        # 挑【第一个本会话没试过的】节点，全试过则重置循环。
        global _TRIED_NODES
        nxt = next((n for n in ordered if n not in _TRIED_NODES), None)
        if nxt is None:           # 一轮全试过，重置再来
            _TRIED_NODES = set()
            nxt = ordered[0]
        _TRIED_NODES.add(nxt)
        ip_before = None
        try:
            ip_before = _clash_verge.public_ip(timeout=5, mixed_port=mixed_port)
        except Exception:
            pass
        client.switch(group, nxt)
        try:
            client.close_connections()
        except Exception:
            pass
        time.sleep(1.5)
        ip_after = None
        try:
            ip_after = _clash_verge.public_ip(timeout=5, mixed_port=mixed_port)
        except Exception:
            pass
        changed = bool(ip_before and ip_after and ip_before != ip_after)
        log(f"clash rotate -> {nxt} IP {ip_before}->{ip_after} "
            f"{'changed' if changed else 'UNCHANGED'}")
        return {"ok": True, "next": nxt, "ip_changed": changed,
                "ip_before": ip_before, "ip_after": ip_after}
    except Exception as e:
        log(f"clash rotate err: {type(e).__name__}: {e}", "WARN")
        return None


def _probe_delay(client, node, timeout_ms):
    """探测单节点延迟(ms)，超时/出错返回 None。用 Clash 自带 /delay(直接测该节点，
    无需先 switch)。"""
    try:
        return client.delay(node, _clash_verge.DEFAULT_TEST_URL, timeout_ms)
    except Exception:
        return None


def maybe_rotate_verified(client, group, mixed_port=7897):
    """轮换到【可用】节点：切之前先探 /delay，跳过超时的，在一批里挑延迟最低的。

    旧逻辑按名字顺序直接 switch，只在切完后验 IP —— 会把整整一次 attempt(~3min)
    浪费在死节点/超时节点上。现在改成：先探测候选节点延迟，超时(None)或超过
    CLASH_MAX_LATENCY_MS 的直接跳过并标记试过，在 CLASH_PROBE_BATCH 个可用节点里
    选延迟最低的再 switch。本会话所有节点都试过则重置一轮。
    """
    if client is None or not group:
        return None
    try:
        excluded = _rotate_excluded(client, group)
        ordered = _ordered_nodes(client, group, excluded)
        if not ordered:
            log("no usable node after exclude", "WARN")
            return None

        # 延迟上限 + 每轮探测多少个候选后就在可用的里挑最优
        try:
            max_latency_ms = int(os.environ.get("CLASH_MAX_LATENCY_MS", "2500") or "2500")
        except Exception:
            max_latency_ms = 2500
        try:
            probe_batch = max(1, int(os.environ.get("CLASH_PROBE_BATCH", "8") or "8"))
        except Exception:
            probe_batch = 8
        probe_tmo = max_latency_ms + 1500

        global _TRIED_NODES
        # 本会话把所有节点都试过了 -> 重置，开新一轮
        if all(n in _TRIED_NODES for n in ordered):
            _TRIED_NODES = set()

        ip_before = None
        try:
            ip_before = _clash_verge.public_ip(timeout=5, mixed_port=mixed_port)
        except Exception:
            pass

        # 1) 探测候选：跳过超时/过慢，在一批可用节点里挑延迟最低的
        best, best_d, probed = None, 1 << 30, 0
        for node in ordered:
            if node in _TRIED_NODES:
                continue
            if probed >= probe_batch and best is not None:
                break
            d = _probe_delay(client, node, probe_tmo)
            probed += 1
            if d is None or d > max_latency_ms:
                _TRIED_NODES.add(node)   # 超时/过慢：本轮不再考虑
                log(f"clash probe skip {node} "
                    f"({'timeout' if d is None else str(d) + 'ms >' + str(max_latency_ms)})")
                continue
            if d < best_d:
                best, best_d = node, d

        # 2) 一批里全超时？放宽 batch，继续往后找第一个能响应的(哪怕慢)，避免整轮无节点
        if best is None:
            for node in ordered:
                if node in _TRIED_NODES:
                    continue
                d = _probe_delay(client, node, probe_tmo)
                if d is not None:
                    best, best_d = node, d
                    break
                _TRIED_NODES.add(node)

        if best is None:
            log("no responsive Clash node (all timed out)", "WARN")
            return {"ok": False, "next": None, "ip_changed": False,
                    "ip_before": ip_before, "ip_after": None}

        # 3) 切到选中的可用节点并验出口 IP
        _TRIED_NODES.add(best)
        client.switch(group, best)
        try:
            client.close_connections()
        except Exception:
            pass
        time.sleep(1.5)

        ip_after = None
        try:
            ip_after = _clash_verge.public_ip(timeout=5, mixed_port=mixed_port)
        except Exception:
            pass
        changed = bool(ip_before and ip_after and ip_before != ip_after)
        ok = bool(ip_after)
        log(f"clash rotate -> {best} ({best_d}ms) IP {ip_before}->{ip_after} "
            f"{'changed' if changed else 'UNCHANGED'} {'OK' if ok else 'BAD'}")
        return {"ok": ok, "next": best, "ip_changed": changed,
                "ip_before": ip_before, "ip_after": ip_after, "latency_ms": best_d}
    except Exception as e:
        log(f"clash rotate verified err: {type(e).__name__}: {e}", "WARN")
        return None


def clash_proxy_from_env():
    raw = (
        os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
        or ""
    ).strip()
    if not raw:
        return None
    for pfx in ("http://", "https://", "socks5://"):
        if raw.lower().startswith(pfx):
            raw = raw[len(pfx):]
            break
    return raw.rstrip("/") or None


BB_API = os.environ.get("BITBROWSER_API", "http://127.0.0.1:54345")
# Match bs_register_step1 — user's BitBrowser has Chromium 146 not 130.
BB_CORE_VERSION = os.environ.get("BB_CORE_VERSION", "146")


def _fingerprint_provider():
    return (
        os.environ.get("FINGERPRINT_BROWSER")
        or os.environ.get("BROWSER_PROVIDER")
        or "bitbrowser"
    ).strip().lower()


def _bb_call(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BB_API}{path}", data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def bb_create_for_outlook_reg(name):
    """Mirror bs_register_step1.bb_create_ephemeral so we share the working
    fingerprint config (proxyType=noproxy + IP-derived locale; routes through
    Clash via TUN). Standalone's hardcoded coreVersion=130 returns 502 on
    BitBrowser builds that only have Chromium 146 installed."""
    if _fingerprint_provider() in {"adspower", "ads_power", "ads"}:
        from bitbrowser import BitBrowser
        return BitBrowser().create_browser(
            name=name,
            remark="outlook reg loop auto-deleted after use",
            platform="https://outlook.live.com",
            platformIcon="outlook.live.com",
            proxyMethod=2,
            proxyType="noproxy",
            browserFingerPrint={
                "ostype": "PC",
                "os": "Win32",
                "coreVersion": BB_CORE_VERSION,
                "isIpCreateTimeZone": True,
                "isIpCreateLanguage": True,
                "isIpCreateDisplayLanguage": True,
                "isIpCreatePosition": True,
                "isIpCountry": True,
            },
        )
    body = {
        "name": name,
        "remark": "outlook reg loop — auto-deleted after use",
        "platform": "https://outlook.live.com",
        "platformIcon": "outlook.live.com",
        "proxyMethod": 2,
        "proxyType": "noproxy",
        "browserFingerPrint": {
            "ostype": "PC",
            "os": "Win32",
            "coreVersion": BB_CORE_VERSION,
            "isIpCreateTimeZone": True,
            "isIpCreateLanguage": True,
            "isIpCreateDisplayLanguage": True,
            "isIpCreatePosition": True,
            "isIpCountry": True,
        },
    }
    r = _bb_call("/browser/update", body)
    if not r.get("success"):
        raise RuntimeError(f"/browser/update failed: {r}")
    data = r.get("data") or {}
    pid = data.get("id") or data.get("browserId")
    if not pid:
        raise RuntimeError(f"/browser/update returned no id: {data}")
    return pid


def count_pool():
    if not os.path.isdir(POOL_DIR):
        return 0
    try:
        return sum(1 for f in os.listdir(POOL_DIR) if f.endswith(".json"))
    except Exception:
        return 0


def extract_graph_for_account(email, password, attempts=3):
    """Return Graph token data for a freshly registered Outlook account."""
    try:
        from extract_graph_tokens import get_graph_token
        for attempt in range(attempts):
            res = get_graph_token(email, password)
            if res and res.get("refresh_token"):
                graph = {
                    "refresh_token": res["refresh_token"],
                    "client_id": res.get("client_id") or "",
                }
                log(f"graph token extracted for {email}", "OK")
                return graph
            if attempt < attempts - 1:
                log(f"graph token attempt {attempt + 1}/{attempts} failed, rotate and retry: {email}", "WARN")
                try:
                    from common import proxy_switch as _ps
                    import random as _rnd
                    cur = _ps.current_node()
                    candidates = [n for n in _ps.concrete_nodes() if n != cur]
                    if candidates:
                        _ps.set_node(_rnd.choice(candidates))
                except Exception as exc:
                    log(f"graph retry node switch failed: {str(exc)[:50]}", "WARN")
                time.sleep(3 * (attempt + 1))
        log(f"graph token missing after {attempts} attempts: {email}", "WARN")
    except Exception as exc:
        log(f"graph token extraction error: {type(exc).__name__}: {exc}", "WARN")
    return None


def append_graph_account_to_emails_pool(email, password, graph):
    """Append only Graph-ready accounts to emails.txt."""
    token = (graph or {}).get("refresh_token") or ""
    client_id = (graph or {}).get("client_id") or ""
    if not token:
        log(f"emails.txt skip {email}: no graph refresh_token", "WARN")
        return False
    try:
        existing = set()
        if os.path.isfile(EMAILS_POOL):
            with open(EMAILS_POOL, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        existing.add(line.split("----")[0].strip().lower())
        if email.lower() in existing:
            return True
        with open(EMAILS_POOL, "a", encoding="utf-8") as f:
            f.write(f"{email}----{password}----{token}----{client_id}\n")
        log(f"emails.txt += {email} (token=yes)", "OK")
        return True
    except Exception as exc:
        log(f"append_to_emails_pool failed: {type(exc).__name__}: {exc}", "WARN")
        return False


def append_to_emails_pool(email, password):
    """把成功号桥接进 emails.txt 池，供账号注册侧 common/emails.next_email 消费。
    注册成功后立即用纯 HTTP OAuth 抽 Graph refresh_token（extract_graph_tokens.get_graph_token），
    写真 token/client_id —— 之后 ChatGPT 取码全走 Graph API，免浏览器登录/取码。
    抽取失败（偶发风控/网络）才回退占位符 fresh，消费侧届时退化到浏览器取码。"""
    token = client_id = "fresh"
    graph = globals().pop("_CURRENT_GRAPH_ACCOUNT", None)
    if graph is not None:
        return append_graph_account_to_emails_pool(email, password, graph)
    try:
        from extract_graph_tokens import get_graph_token
        # 抽取经代理偶发 TLS 抖动(SSLEOFError)，单试一次一抖就回退 fresh、白丢 token 快路；
        # 这里重试 3 次(短退避)，绝大多数抖动二/三次就过。
        res = None
        for _try in range(3):
            res = get_graph_token(email, password)
            if res and res.get("refresh_token"):
                break
            if _try < 2:
                # 抽取经代理偶发 TLS 抖动：第 2 次起先切 Clash 节点换出口再试(绕开坏节点)。
                log(f"graph token 抽取第{_try+1}次未成，切节点重试: {email}", "WARN")
                try:
                    from common import proxy_switch as _ps
                    import random as _rnd
                    _cur = _ps.current_node()
                    _cands = [n for n in _ps.concrete_nodes() if n != _cur]
                    if _cands:
                        _ps.set_node(_rnd.choice(_cands))
                except Exception as _e:
                    log(f"切节点失败(忽略): {str(_e)[:50]}", "WARN")
                time.sleep(3 * (_try + 1))
        if res and res.get("refresh_token"):
            token = res["refresh_token"]
            client_id = res.get("client_id") or "fresh"
            log(f"graph token extracted for {email}", "OK")
        else:
            log(f"graph token 抽取失败(3 次)，回退 fresh: {email}", "WARN")
    except Exception as e:
        log(f"graph token 抽取异常，回退 fresh: {type(e).__name__}: {e}", "WARN")
    try:
        existing = set()
        if os.path.isfile(EMAILS_POOL):
            with open(EMAILS_POOL, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        existing.add(line.split("----")[0].strip().lower())
        if email.lower() in existing:
            return
        with open(EMAILS_POOL, "a", encoding="utf-8") as f:
            f.write(f"{email}----{password}----{token}----{client_id}\n")
        log(f"emails.txt += {email} (token={'yes' if token != 'fresh' else 'fresh'})", "OK")
    except Exception as e:
        log(f"append_to_emails_pool failed: {type(e).__name__}: {e}", "WARN")


def append_no_graph_account(email, password):
    """注册成功但 Graph refresh_token 提取失败的号：邮箱+密码单独存到 NO_GRAPH_POOL，
    别丢弃。这些号本体有效(能登录/收码)，只是没抽到 RT，后续可用
    extract_graph_tokens.py 重跑补 token 再入池。去重按邮箱。格式 email----password。"""
    try:
        existing = set()
        if os.path.isfile(NO_GRAPH_POOL):
            with open(NO_GRAPH_POOL, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        existing.add(line.split("----")[0].strip().lower())
        if email.lower() in existing:
            return
        with open(NO_GRAPH_POOL, "a", encoding="utf-8") as f:
            f.write(f"{email}----{password}\n")
        log(f"outlook_no_graph.txt += {email} (无 RT，已存待补)", "OK")
    except Exception as e:
        log(f"append_no_graph_account failed: {type(e).__name__}: {e}", "WARN")


def write_record(record):
    os.makedirs(POOL_DIR, exist_ok=True)
    safe = record["email"].replace("@", "_at_").replace("/", "_")
    fname = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:18] + f"_{safe}.json"
    tmp = os.path.join(POOL_DIR, fname + ".tmp")
    dst = os.path.join(POOL_DIR, fname)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        os.rename(tmp, dst)
    except Exception as e:
        log(f"write_record FAILED: {type(e).__name__}: {e}  (tmp={tmp})", "ERR")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise
    # Verify it actually landed.
    if os.path.isfile(dst):
        sz = os.path.getsize(dst)
        log(f"write_record OK: {dst}  ({sz} bytes)", "OK")
    else:
        log(f"write_record sus: {dst} missing right after rename!", "ERR")
    return fname


async def _run_outlook_on_ctx(mod, ctx, idx):
    """Register, export cookies, then obtain Graph RT in the live login session."""
    # Scrub Chromium residual state so signup.live.com doesn't see a
    # stale identity from a previous session.
    try:
        await ctx.clear_cookies()
        for _pg in ctx.pages:
            try:
                c = await ctx.new_cdp_session(_pg)
                await c.send("Network.clearBrowserCookies")
                await c.send("Network.clearBrowserCache")
                try: await c.detach()
                except Exception: pass
                break
            except Exception:
                pass
    except Exception:
        pass
    page = await ctx.new_page()
    email, password = await mod.register_outlook(page, ctx, idx)
    cookies = []
    graph = None
    if email:
        try:
            all_cookies = await ctx.cookies()
            keep_domains = (
                "outlook.", "live.com", "microsoftonline.",
                "microsoft.com", "office.com", ".office365.",
                "msn.com", "bing.com", "mail.live.com",
            )
            cookies = [
                c for c in all_cookies
                if any(d in (c.get("domain") or "") for d in keep_domains)
            ]
        except Exception as e:
            log(f"cookie export failed: {e}", "WARN")
        try:
            log(f"graph browser-session extraction start: {email}")
            graph = await mod.extract_graph_token(page, ctx, email, password, idx)
            if graph and graph.get("refresh_token"):
                graph["client_id"] = graph.get("client_id") or mod.GRAPH_CLIENT_ID
                log(f"graph token extracted from live browser session: {email}", "OK")
            else:
                graph = None
                log(f"graph browser-session extraction returned no RT: {email}", "WARN")
        except Exception as e:
            log(f"graph browser-session extraction failed: {type(e).__name__}: {e}", "WARN")
            graph = None
    return email, password, cookies, graph


async def one_attempt(mod, proxy_str, idx):
    """Mirrors bs_register_step1.fetch_email_from_self_register's inline
    flow, but doesn't carry the breaker state — we're a dedicated loop and
    want to keep trying."""
    profile_id = None
    bb = mod.BitBrowserClient()
    try:
        ts = datetime.now().strftime("%m%d_%H%M%S")
        for _r in range(5):
            try:
                # Use our own create that picks coreVersion=146 (matches the
                # BitBrowser install on this machine). Standalone's hardcoded
                # 130 makes BB return 502.
                profile_id = bb_create_for_outlook_reg(f"outlook_loop_{ts}_{idx}")
                break
            except Exception as e:
                m = str(e)
                if "最大" in m or "超过" in m:
                    log("BitBrowser quota — cleanup_browsers(keep=2)", "WARN")
                    try: bb.cleanup_browsers(keep=2)
                    except Exception: pass
                    await asyncio.sleep(3)
                    continue
                if _r >= 4:
                    raise
                log(f"create_browser err (try {_r+1}/5): {m[:200]}", "WARN")
                await asyncio.sleep(3 + _r)
        if not profile_id:
            return None, None, [], None
        info = bb.open_browser(profile_id)
        ws = info.get("ws", "")
        if not ws:
            return None, None, [], None
        from playwright.async_api import async_playwright as _apw
        async with _apw() as p:
            browser = await p.chromium.connect_over_cdp(ws)
            ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
            email, password, cookies, graph = await _run_outlook_on_ctx(mod, ctx, idx)
        return email, password, cookies, graph
    finally:
        if profile_id:
            try:
                bb.close_browser(profile_id)
                await asyncio.sleep(2)
                bb.delete_browser(profile_id)
            except Exception:
                pass


def _playwright_shutdown_exception_handler(loop, context):
    """Ignore only detached Playwright futures after a timed-out profile closes."""
    error = context.get("exception")
    message = str(error or context.get("message") or "")
    if type(error).__name__ == "TargetClosedError" or (
        "Target page, context or browser has been closed" in message
    ):
        return
    loop.default_exception_handler(context)


async def _one_attempt_with_timeout(mod, proxy_str, idx, timeout):
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(_playwright_shutdown_exception_handler)
    return await asyncio.wait_for(one_attempt(mod, proxy_str, idx), timeout=timeout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=0,
                    help="run this many attempts then exit (0 = loop forever)")
    ap.add_argument("--target-pool", type=int, default=0,
                    help="stop registering once pool dir has this many records "
                         "(0 = no cap; producer always runs)")
    ap.add_argument("--max-press", default=DEFAULT_MAX_PRESS,
                    help="OUTLOOK_REG_MAX_PRESS — captcha press-and-hold cap")
    ap.add_argument("--confirm-before-register", action="store_true",
                    help="auto-click confirmation on the signup page before filling")
    ap.add_argument("--timeout", type=int, default=180,
                    help="hard cap per attempt (seconds)")
    ap.add_argument("--sleep", type=int, default=5,
                    help="seconds between attempts (after fail or success)")
    ap.add_argument("--sleep-when-full", type=int, default=60,
                    help="seconds to sleep when pool is at target")
    ap.add_argument("--no-rotate", action="store_true",
                    help="不轮换 Clash 节点：每次 attempt 都用当前节点，不切换/不探测。"
                         "也可用环境变量 OUTLOOK_NO_ROTATE=1 开启。")
    args = ap.parse_args()

    # 不轮换开关：命令行 --no-rotate 或 env OUTLOOK_NO_ROTATE 任一为真即生效。
    no_rotate = args.no_rotate or _env_truthy_norotate()

    os.environ.setdefault("OUTLOOK_REG_MAX_PRESS", args.max_press)
    if args.confirm_before_register:
        os.environ["OUTLOOK_CONFIRM_BEFORE_REGISTER"] = "1"
    if sys.platform == "win32":
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        except Exception:
            pass

    mod = load_standalone()
    injected_proxy = ensure_clash_proxy_env()
    if injected_proxy:
        log(f"proxy env ready: {injected_proxy}")
    proxy = clash_proxy_from_env()
    if not proxy:
        log("HTTP_PROXY not set — running without proxy (signup will likely fail)", "WARN")
    else:
        log(f"using clash proxy: {proxy}")

    # Initialize Clash controller for per-attempt node rotation. MS PerimeterX
    # learns the egress IP fast — without rotation we get ERR_CONNECTION_CLOSED
    # after 1-2 signups from the same node.
    # --no-rotate / OUTLOOK_NO_ROTATE 时不连 Clash 控制器，固定用当前节点。
    if no_rotate:
        clash_client, clash_group = None, None
        log("node rotation DISABLED (--no-rotate / OUTLOOK_NO_ROTATE) — 固定当前节点")
    else:
        clash_client, clash_group = init_clash()

    log(f"pool dir: {POOL_DIR}")
    os.makedirs(POOL_DIR, exist_ok=True)
    log(f"current pool size: {count_pool()}")

    n = 0
    succ = 0
    failed = 0
    while True:
        n += 1
        if args.count and n > args.count:
            log(f"reached --count {args.count}, exit (success={succ}, fail={failed})")
            break
        ps = count_pool()
        if args.target_pool and ps >= args.target_pool:
            log(f"pool at target ({ps}/{args.target_pool}) — sleep {args.sleep_when_full}s")
            time.sleep(args.sleep_when_full)
            continue
        # Rotate Clash node before each attempt so MS PX sees a fresh IP.
        # --no-rotate / OUTLOOK_NO_ROTATE 开启时跳过轮换，固定用当前节点。
        if not no_rotate:
            rotate_info = maybe_rotate_verified(clash_client, clash_group)
            if clash_client is not None and clash_group and rotate_info and not rotate_info.get("ok"):
                failed += 1
                log("skip attempt: no reachable Clash egress", "WARN")
                time.sleep(args.sleep)
                continue
        log(f"=== attempt #{n}  (pool={ps}, succ={succ}, fail={failed}) ===")
        t0 = time.time()
        email = password = None
        cookies = []
        graph = None
        try:
            email, password, cookies, graph = asyncio.run(
                _one_attempt_with_timeout(mod, proxy, n, args.timeout + 90)
            )
        except Exception as e:
            log(f"attempt raised {type(e).__name__}: {str(e)[:200]}", "WARN")
        elapsed = time.time() - t0
        if email and password:
            if not graph or not graph.get("refresh_token"):
                log(f"live browser RT unavailable; falling back to pure HTTP: {email}", "WARN")
                graph = extract_graph_for_account(email, password)
            if not graph or not graph.get("refresh_token"):
                failed += 1
                append_no_graph_account(email, password)  # 号有效但没抽到 RT：单独存待补
                log(f"registered but graph RT missing; saved to outlook_no_graph.txt: {email}", "WARN")
                time.sleep(args.sleep)
                continue
            fname = write_record({
                "email": email,
                "password": password,
                "refresh_token": graph["refresh_token"],
                "client_id": graph.get("client_id") or "",
                "graph": graph,
                "outlook_cookies": cookies,
                "source": "self-loop",
                "ts": datetime.now().isoformat(),
            })
            globals()["_CURRENT_GRAPH_ACCOUNT"] = graph
            append_to_emails_pool(email, password)   # 桥接进账号注册池
            succ += 1
            log(f"OK in {elapsed:.1f}s: {email} -> {fname} (pool now {count_pool()})", "OK")
        else:
            failed += 1
            log(f"FAIL in {elapsed:.1f}s (success rate {succ}/{n} = {100*succ/n:.0f}%)", "WARN")
        time.sleep(args.sleep)


if __name__ == "__main__":
    main()
