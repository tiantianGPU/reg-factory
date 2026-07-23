# -*- coding: utf-8 -*-
"""
ChatGPT (OpenAI) 自动注册
复用 common/ 基建: BitBrowser + stealth + Outlook 取验证码 + cookie 保存

流程: chatgpt.com/auth/login -> 填邮箱 -> Continue -> 验证码/密码 -> Arkose -> onboarding -> 保存 cookie

用法:
    python register_chatgpt.py --count 1
    python register_chatgpt.py --count 10 --concurrency 2
"""

import argparse
import asyncio
import functools
import json
import os as _os
import random
import string
import sys
import time
from urllib.parse import unquote, urlsplit

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, ".")
from playwright.async_api import async_playwright

from common.browser import open_and_connect, teardown, human_type, react_fill
from common.mailbox import get_code_by_token, get_code_outlook_pw, prelogin_outlook
from common.cookies import save_platform_cookies
from common import emails as email_pool

try:
    from config import CHATGPT2API_URL, CHATGPT2API_KEY
except Exception:
    CHATGPT2API_URL, CHATGPT2API_KEY = "", ""

try:
    from config import (
        SUB2API_URL, SUB2API_EMAIL, SUB2API_PASSWORD, SUB2API_GROUP,
        CPA_URL, CPA_MGMT_KEY,
    )
except Exception:
    SUB2API_URL = SUB2API_EMAIL = SUB2API_PASSWORD = ""
    SUB2API_GROUP = "codex"
    CPA_URL = CPA_MGMT_KEY = ""

PLATFORM = "chatgpt"
SIGNUP_URL = "https://chatgpt.com/auth/login"
KEY_COOKIES = ["__Secure-next-auth.session-token", "__Secure-next-auth.session-token.0"]
REGISTER_TIMEOUT = 480
KEEP_ON_FAIL = False  # 调试：失败时保留窗口便于排查
FIXED_EMAIL = None
FIXED_PASSWORD = None
FIXED_REFRESH_TOKEN = None
FIXED_CLIENT_ID = None
IMPORT_C2A = False  # 注册成功后即时把 token 导入 chatgpt2api（--import-c2a 开启）
C2A_URL = None  # chatgpt2api host（默认取 config.CHATGPT2API_URL）
C2A_KEY = None  # chatgpt2api admin key（默认取 config.CHATGPT2API_KEY）
EXTRACT_CODEX = False  # 注册成功后顺手走 Codex OAuth 提取 rt 导入 SUB2API（--codex 开启）
CODEX_GROUP = None  # SUB2API 目标分组（默认取 config.SUB2API_GROUP）
CODEX_MANUAL_PHONE = False  # add-phone 手动模式（不接码，自己在浏览器填号收码）
CODEX_TIMEOUT = 120  # Codex 授权捕获超时秒
CHATGPT_NODE = "auto"
ACTIVE_CHATGPT_NODE = None


def _env_int(name, default):
    raw = _os.environ.get(name, "")
    try:
        return int(raw or default)
    except (TypeError, ValueError):
        print(f"  [config] {name}={raw!r} 无效，使用默认值 {default}")
        return int(default)


# CF 友好节点池。显式配置时按给定顺序使用；auto 默认从 Clash 当前代理组
# 动态读取，避免订阅更新/节点改名后继续尝试已经不存在的历史名称。
CF_NODES = [
    node.strip()
    for node in (_os.environ.get("CHATGPT_CF_NODES") or "").split(",")
    if node.strip()
]
_active_cf_nodes = []
_cf_node_idx = [0]  # 轮换游标


def _order_chatgpt_nodes(candidates):
    """Interleave preferred regions so a small probe budget covers varied exits."""
    region_markers = (
        ("🇯🇵", "日本", "Japan"),
        ("🇸🇬", "新加坡", "Singapore"),
        ("🇰🇷", "韩国", "Korea"),
        ("🇫🇷", "法国", "France"),
        ("🇺🇸", "美国", "United States", "USA"),
        ("🇬🇧", "英国", "United Kingdom"),
        ("🇩🇪", "德国", "Germany"),
        ("🇨🇦", "加拿大", "Canada"),
        ("🇦🇺", "澳大利亚", "Australia"),
        ("🇹🇼", "台湾", "Taiwan"),
    )
    buckets = [[] for _ in region_markers]
    remaining = []
    for node in candidates:
        bucket = next(
            (
                index
                for index, markers in enumerate(region_markers)
                if any(marker.lower() in node.lower() for marker in markers)
            ),
            None,
        )
        if bucket is None:
            remaining.append(node)
        else:
            buckets[bucket].append(node)

    ordered = []
    while any(buckets):
        for bucket in buckets:
            if bucket:
                ordered.append(bucket.pop(0))
    return ordered + remaining


def _discover_chatgpt_nodes():
    """Return real leaf proxies from the configured Clash selector group."""
    import _clash_verge as cv

    api = _os.environ.get("CLASH_API", "http://127.0.0.1:9097")
    secret = _os.environ.get("CLASH_SECRET", "")
    group = _os.environ.get("CLASH_GROUP", "GLOBAL") or "GLOBAL"
    client = cv.ClashClient(api, secret)
    catalog = (client.proxies().get("proxies") or {})
    group_info = catalog.get(group)
    if not group_info:
        group_info = client.group(group)

    group_types = {"selector", "urltest", "fallback", "loadbalance"}
    candidates = []
    for name in group_info.get("all") or []:
        info = catalog.get(name) or {}
        if name in cv.SPECIAL_NAMES or cv.is_fake_node(name):
            continue
        if (info.get("type") or "").lower() in group_types:
            continue
        candidates.append(name)

    if not candidates:
        raise RuntimeError(f"Clash 代理组 {group!r} 中没有可用的叶子节点")
    return _order_chatgpt_nodes(candidates)


def _chatgpt_node_candidates():
    """Resolve and cache the candidate pool shared by preflight and CF rotation."""
    global _active_cf_nodes
    if _active_cf_nodes:
        return list(_active_cf_nodes)

    candidates = list(CF_NODES) if CF_NODES else _discover_chatgpt_nodes()
    limit = max(1, _env_int("CHATGPT_NODE_PROBE_LIMIT", 12))
    _active_cf_nodes = candidates[:limit]
    _cf_node_idx[0] = 0
    return list(_active_cf_nodes)


async def _is_cf_blocked(page):
    """CF 全页拦截判定：无 email 输入框 且 (页面只有 cf-turnstile 隐藏域 / body 基本空)。"""
    try:
        if await page.locator('input[type="email"], input[name="email"]').count() > 0:
            return False
        body = (await page.locator("body").inner_text()).strip()
        has_ts = await page.locator('input[name="cf-turnstile-response"], .cf-turnstile, iframe[src*=challenges.cloudflare]').count() > 0
        return has_ts or len(body) < 5
    except Exception:
        # reload 中 locator 抛 DOMException：当作仍被拦(还在挑战页)
        return True


async def _click_turnstile(page):
    """尝试点 Turnstile 勾选框（临界 IP 上会降级成可点的 'Verify you are human'）。
    iframe 内 checkbox 优先；不行就按容器坐标点。点到返回 True（不保证过，过没过由调用方轮询判定）。"""
    # 1) challenges.cloudflare iframe 内的 checkbox/label
    for sel in ('iframe[src*=challenges.cloudflare]', 'iframe[src*=turnstile]'):
        try:
            if await page.locator(sel).count() > 0:
                fr = page.frame_locator(sel).first
                for inner in ('input[type=checkbox]', 'label', 'body'):
                    loc = fr.locator(inner)
                    if await loc.count() > 0:
                        await loc.first.click(timeout=3000)
                        return True
        except Exception:
            pass
    # 2) .cf-turnstile 容器左侧勾选框位置（容器内偏左中部）
    try:
        if await page.locator('.cf-turnstile').count() > 0:
            box = await page.locator('.cf-turnstile').first.bounding_box()
            if box:
                await page.mouse.click(box["x"] + 28, box["y"] + box["height"] / 2)
                return True
    except Exception:
        pass
    return False


def _activate_cf_node(node):
    """切换 Clash 节点并断开旧连接，避免新注册会话沿用旧出口。"""
    global ACTIVE_CHATGPT_NODE
    try:
        import _clash_verge as cv
        api = _os.environ.get("CLASH_API", "http://127.0.0.1:9097")
        secret = _os.environ.get("CLASH_SECRET", "")
        group = _os.environ.get("CLASH_GROUP", "GLOBAL") or "GLOBAL"
        client = cv.ClashClient(api, secret)
        client.switch(group, node)
        client.close_connections()
        ACTIVE_CHATGPT_NODE = node
        return node
    except Exception as e:
        print(f"  [cf] 切节点失败: {str(e)[:80]}")
        return None


def _switch_cf_node():
    """把 Clash 代理组切到候选池中的下一个节点。"""
    candidates = _chatgpt_node_candidates()
    node = candidates[_cf_node_idx[0] % len(candidates)]
    _cf_node_idx[0] += 1
    return _activate_cf_node(node)


def clash_browser_proxy_fields():
    """把 CLASH_PROXY 转成 BitBrowser/AdsPower profile 代理字段。"""
    raw = _os.environ.get("CLASH_PROXY", "http://127.0.0.1:7897").strip()
    parsed = urlsplit(raw if "://" in raw else "http://" + raw)
    if not parsed.hostname or not parsed.port:
        raise ValueError(f"CLASH_PROXY 格式无效: {raw}")
    fields = {
        "proxyMethod": 2,
        "proxyType": "socks5" if parsed.scheme.lower() == "socks5" else "http",
        "host": parsed.hostname,
        "port": str(parsed.port),
    }
    if parsed.username:
        fields["proxyUserName"] = unquote(parsed.username)
        fields["proxyPassword"] = unquote(parsed.password or "")
    return fields


def _probe_chatgpt_node():
    """验证当前 Clash 出口能访问 ChatGPT，并返回 Cloudflare 识别地区。"""
    from curl_cffi import requests as creq

    proxy = _os.environ.get("CLASH_PROXY", "http://127.0.0.1:7897")
    session = creq.Session(impersonate="chrome131", http_version="v2")
    session.proxies = {"http": proxy, "https": proxy}
    try:
        trace = session.get("https://auth.openai.com/cdn-cgi/trace", timeout=15)
        loc = next(
            (line.split("=", 1)[1] for line in trace.text.splitlines() if line.startswith("loc=")),
            "?",
        )
        response = session.get(SIGNUP_URL, allow_redirects=True, timeout=25)
        body = response.text[:100000].lower()
        blocked = (
            response.status_code != 200
            or "unsupported_country_region_territory" in body
            or "just a moment" in body
            or "performing security verification" in body
        )
        return not blocked, loc, response.status_code
    finally:
        session.close()


def select_chatgpt_node(requested, allow_blocked=False):
    """注册开始前选定一个节点；账号会话建立后不再静默换出口。"""
    global ACTIVE_CHATGPT_NODE
    value = (requested or "auto").strip()
    if value.lower() in {"none", "off", "direct"}:
        ACTIVE_CHATGPT_NODE = None
        print("  [node] ChatGPT 使用直连模式")
        return None

    if value.lower() == "auto":
        candidates = _chatgpt_node_candidates()
        print(f"  [node] ChatGPT auto 从 Clash 读取 {len(candidates)} 个候选节点")
    else:
        candidates = [value]
    last_error = ""
    activated = []
    for index, node in enumerate(candidates):
        if not _activate_cf_node(node):
            continue
        activated.append(node)
        time.sleep(2)
        try:
            ok, loc, status = _probe_chatgpt_node()
            print(f"  [node] ChatGPT probe {node}: HTTP {status} loc={loc} {'PASS' if ok else 'BLOCK'}")
            if ok:
                _cf_node_idx[0] = index + 1
                return node
        except Exception as e:
            last_error = str(e)
            print(f"  [node] ChatGPT probe {node}: {last_error[:80]}")
    if allow_blocked and activated:
        fallback = activated[0]
        _activate_cf_node(fallback)
        print(f"  [node] 无 Cookie 预检均被拦，OAuth 使用已有登录态继续验证: {fallback}")
        return fallback
    raise RuntimeError(f"没有可用的 ChatGPT 节点: {last_error or value}")


def assert_chatgpt_node(stage):
    """检测其他任务是否在注册中途改了 GLOBAL 出口。"""
    if not ACTIVE_CHATGPT_NODE:
        return
    from common import proxy_switch

    current = proxy_switch.current_node()
    if current != ACTIVE_CHATGPT_NODE:
        raise RuntimeError(
            f"chatgpt_node_changed:{stage}: expected={ACTIVE_CHATGPT_NODE}, current={current}"
        )


class OnboardingRejected(RuntimeError):
    pass


def _openai_error_from_text(text, status=0, url=""):
    raw = (text or "").strip()
    lower = raw.lower()
    region_markers = (
        "unsupported_country_region_territory",
        "country, region, or territory not supported",
        "not available in your country",
        "你的国家和地区不提供服务",
        "您的國家或地區不受支援",
    )
    if any(marker in lower for marker in region_markers):
        return {
            "code": "unsupported_country_region_territory",
            "message": "Country, region, or territory not supported",
            "status": status,
            "url": url,
        }
    if status < 400:
        return None
    try:
        payload = json.loads(raw)
        error = payload.get("error", payload) if isinstance(payload, dict) else {}
        if isinstance(error, dict):
            return {
                "code": str(error.get("code") or error.get("type") or f"http_{status}"),
                "message": str(error.get("message") or raw[:180]),
                "status": status,
                "url": url,
            }
    except Exception:
        pass
    return {"code": f"http_{status}", "message": raw[:180], "status": status, "url": url}


class AuthResponseMonitor:
    """Collect failed auth POST responses without printing tokens or query strings."""

    def __init__(self):
        self.errors = []
        self._tasks = []

    def observe(self, response):
        try:
            parsed = urlsplit(response.url)
            if response.request.method.upper() != "POST" or parsed.hostname != "auth.openai.com":
                return
            if not any(marker in parsed.path.lower() for marker in ("account", "onboarding", "about-you")):
                return
            self._tasks.append(asyncio.create_task(self._record(response, parsed.path)))
        except Exception:
            pass

    async def _record(self, response, path):
        try:
            text = await response.text()
        except Exception:
            text = ""
        error = _openai_error_from_text(text, response.status, path)
        if error:
            self.errors.append(error)

    async def _drain(self):
        tasks, self._tasks = self._tasks, []
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def clear(self):
        await self._drain()
        self.errors.clear()

    async def latest(self):
        await self._drain()
        return self.errors[-1] if self.errors else None


# OpenAI 发件人 / 验证码邮件特征
OAI_SENDER = ("openai.com", "noreply@", "no-reply@")
OAI_SUBJECT = ("code", "verify", "verification", "openai", "chatgpt", "confirm")


def rand_password():
    return "Aa1!" + "".join(random.choices(string.ascii_letters + string.digits, k=12))


# 常见英文名/姓，短且自然（比随机字母串更像真人，键入也快）
_FIRST_NAMES = ["James", "Mary", "John", "Anna", "David", "Laura", "Mike", "Emma",
                "Chris", "Sara", "Paul", "Lucy", "Mark", "Nina", "Tom", "Kate",
                "Alex", "Ella", "Sam", "Lily", "Ben", "Zoe", "Leo", "Ruby"]
_LAST_NAMES = ["Smith", "Jones", "Brown", "Davis", "Evans", "Clark", "Hall", "Lee",
               "Walker", "Young", "King", "Wright", "Green", "Baker", "Adams", "Carter",
               "Reed", "Cook", "Bell", "Ward", "Gray", "Hughes", "Price", "Wood"]


def rand_name():
    first = random.choice(_FIRST_NAMES)
    last = random.choice(_LAST_NAMES)
    return first, last


async def dump_state(page, tag=""):
    """打印当前页面状态，便于首跑适配"""
    try:
        print(f"  --- state {tag} ---")
        print(f"  url: {page.url}")
        n = await page.locator("input").count()
        for i in range(min(n, 6)):
            el = page.locator("input").nth(i)
            try:
                print(f"    input[{i}] type={await el.get_attribute('type')} "
                      f"name={await el.get_attribute('name')} "
                      f"placeholder={await el.get_attribute('placeholder')}")
            except Exception:
                pass
        nb = await page.locator("button").count()
        btxt = []
        for i in range(min(nb, 10)):
            try:
                t = (await page.locator("button").nth(i).inner_text()).strip()[:30]
                if t:
                    btxt.append(t)
            except Exception:
                pass
        print(f"    buttons: {btxt}")
        body = (await page.locator("body").inner_text())[:300].replace("\n", " | ")
        print(f"    body: {body}")
    except Exception as e:
        print(f"  dump_state error: {e}")


async def click_exact(page, label, timeout=5000):
    """精确点击文本完全等于 label 的按钮（避免 has-text 子串误匹配，
    如 'Continue' 误点 'Continue with Google'）。返回是否点击成功。"""
    try:
        btn = page.get_by_role("button", name=label, exact=True)
        if await btn.count() > 0:
            await btn.first.click(timeout=timeout)
            return True
    except Exception:
        pass
    # 退化：用 CSS 但排除 "with" 字样
    try:
        cand = page.locator(f'button:has-text("{label}")')
        n = await cand.count()
        for i in range(n):
            t = (await cand.nth(i).inner_text()).strip()
            if t == label:
                await cand.nth(i).click(timeout=timeout)
                return True
    except Exception:
        pass
    return False


async def click_any_exact(page, labels):
    """依次尝试精确点击一组候选标签，命中任一即返回 True。"""
    for label in labels:
        if await click_exact(page, label):
            return True
    return False


# cookie 同意横幅按钮（中/英/日/德），弹出时不关会挡住邮箱输入
_COOKIE_BTNS = [
    "すべて受け入れる", "必須項目以外を拒否する",          # 日
    "Accept all", "Reject all", "Reject non-essential", "Accept", "Got it",  # 英
    "全部接受", "接受所有", "拒绝所有", "拒绝非必要", "同意", "知道了",          # 中
    "Alle akzeptieren", "Annehmen",                       # 德
]


async def _click_resend_code(page):
    """验证码页找「重新发送」入口点一下（多语言）。点到返回 True。"""
    labels = ["Resend code", "Resend email", "Resend", "Send again", "Send a new code",
              "再送信", "再送", "コードを再送", "重新发送", "重新發送", "重发",
              "重新傳送電郵", "重新传送电邮", "重新傳送", "重新传送", "重新傳送驗證碼", "重新獲取",
              "Kirim semula", "Hantar semula"]
    for lbl in labels:
        for getter in (
            lambda l=lbl: page.get_by_role("button", name=l, exact=False),
            lambda l=lbl: page.get_by_role("link", name=l, exact=False),
            lambda l=lbl: page.locator(f'button:has-text("{l}")'),
            lambda l=lbl: page.locator(f'a:has-text("{l}")'),
        ):
            try:
                loc = getter()
                if await loc.count() > 0 and await loc.first.is_visible():
                    await loc.first.click(timeout=3000)
                    print(f"  [4] resend clicked（匹配 '{lbl}'）")
                    await asyncio.sleep(2)
                    return True
            except Exception:
                pass
    return False


async def dismiss_cookie_banner(page):
    """关闭 cookie 同意横幅（命中一个即可）。"""
    for label in _COOKIE_BTNS:
        try:
            b = page.get_by_role("button", name=label, exact=True)
            if await b.count() > 0:
                await b.first.click(timeout=2000)
                print(f"  [cookie] dismissed: {label}")
                await asyncio.sleep(1)
                return True
        except Exception:
            pass
    return False


async def fill_email_verified(page, email_input, email, tries=4):
    """填邮箱（React 受控输入：键盘逐字+JS setter 兜底，见 common.browser.react_fill）。
    fill() 只改 DOM .value 不触发 React onChange -> 提交空邮箱 ?email=。

    坑：cookie 同意横幅常在打开页面后、填邮箱当下才异步弹出，盖住输入框抢焦点：
    键盘输入落空（React onChange 收不到值），但 JS setter 兜底把 DOM .value 写进去了
    -> react_fill 回读 input_value() 匹配 -> 误报成功 -> 不重试不关横幅 -> 空提交。
    所以这里每轮**先关横幅再填**，填完若横幅仍在则再关一次并重填。"""
    sel = 'input[type="email"], input[name="email"]'
    for i in range(tries):
        # 1) 先关横幅（可能这轮才弹出来）
        await dismiss_cookie_banner(page)
        # 2) 等横幅真正消失/页面稳定再填——横幅抢焦点会让键盘输入落空，必须等它落定
        await asyncio.sleep(0.8)
        await dismiss_cookie_banner(page)
        # 3) 填邮箱（React 受控输入）
        if await react_fill(page, sel, email, tries=2, verbose=False):
            # 4) 填完立即确认：横幅若此刻才冒出来盖住，关掉它并回读校验，防 setter 误报
            await dismiss_cookie_banner(page)
            await asyncio.sleep(0.3)
            try:
                if (await page.locator(sel).first.input_value()).strip() == email:
                    return True
            except Exception:
                return True
        print(f"  [2] email not committed, retry {i+1}/{tries}")
        await asyncio.sleep(1)
    return False


async def chatgpt_email_submission_advanced(page):
    """Return false while the visible email form still owns the auth flow."""
    try:
        email_input = page.locator(
            'input[type="email"], input[name="email"]'
        ).first
        if await email_input.count() > 0 and await email_input.is_visible():
            return False
    except Exception:
        pass
    return True


def should_use_browser_mail_fallback(has_graph_token, code_try, total_tries=3):
    """Use the slower Outlook UI only after the final Graph attempt fails."""
    return bool(has_graph_token and code_try >= total_tries - 1)


async def detect_challenge(page):
    """检测 Arkose / Turnstile / hCaptcha 是否出现"""
    sel = ("iframe[src*=arkose], #arkose, [data-pkey], #FunCaptcha, "
           ".cf-turnstile, iframe[src*=turnstile], iframe[src*=challenges.cloudflare], "
           "iframe[src*=hcaptcha]")
    try:
        return await page.locator(sel).count() > 0
    except Exception:
        return False


def import_chatgpt2api(session, email):
    """注册成功后把单个号的 token 导入 chatgpt2api（--import-c2a）。
    用注册时已抓到的 session 直接构造导入对象并 POST，避免再抓一次。
    失败只打印告警，不影响注册成功判定。"""
    if not session:
        print("  [c2a] 无 session，跳过导入")
        return
    host = C2A_URL or CHATGPT2API_URL
    key = C2A_KEY or CHATGPT2API_KEY
    if not (host and key):
        print("  [c2a] 未配置 CHATGPT2API_URL/KEY（--c2a-url/--c2a-key 或 .env），跳过导入")
        return
    try:
        from common.session_export import build_chatgpt2api_account
        from export_chatgpt2api import import_accounts
        account = build_chatgpt2api_account(session, email=email)
        ok, msg = import_accounts(host, key, [account])
        print(f"  [c2a] import {email}: {'OK' if ok else 'FAIL'} - {msg}")
    except Exception as e:
        print(f"  [c2a] 导入失败: {str(e)[:120]}")


async def extract_codex(page, email, p=None, ctx=None, release_current=None):
    """注册成功后顺手走 Codex OAuth 提取 refresh_token 导入 SUB2API（--codex）。
    复用刚注册完已登录的 page（无需像 oauth_codex.py 那样重载 cookie 再登），
    直接在该窗口打开 SUB2API 生成的授权链接 -> 同意 -> 捕获 localhost:1455 回调 -> 换码建号。
    带真 refresh_token 的 OAuth 凭据，SUB2API 当 oauth 账号可续期（网页 session 无 rt 会 401）。
    失败只打印告警，不影响注册成功判定。返回是否成功。"""
    if not (SUB2API_URL and SUB2API_EMAIL and SUB2API_PASSWORD):
        print("  [codex] 未配置 SUB2API_URL/EMAIL/PASSWORD（.env），跳过")
        return False
    try:
        from common.uploaders import _origin
        from common import oauth_codex as ox
    except Exception as e:
        print(f"  [codex] 模块加载失败: {str(e)[:120]}")
        return False

    group = CODEX_GROUP or SUB2API_GROUP
    origin = _origin(SUB2API_URL)
    # 注册完页面可能停在 "You're all set / Continue" 欢迎拦层（或各种 onboarding 弹层），
    # 不清掉就直接开授权，auth_url 会被拦/重定向，drive_authorize 在循环里卡死。
    # 先尽力点掉 Continue、并导航到干净首页，给 OAuth 一个干净起点。
    try:
        for lbl in ["Continue", "続行", "继续", "繼續", "Okay, let's go", "Get started", "Done", "完成"]:
            try:
                b = page.get_by_role("button", name=lbl, exact=False)
                if await b.count() > 0 and await b.first.is_visible():
                    await b.first.click(timeout=2500)
                    print(f"  [codex] 关注册后拦层: {lbl}")
                    await asyncio.sleep(1.5)
                    break
            except Exception:
                pass
        await page.goto("https://chatgpt.com/", timeout=30000, wait_until="domcontentloaded")
        await asyncio.sleep(2)
    except Exception as e:
        print(f"  [codex] 清拦层/导航首页异常(忽略): {str(e)[:60]}")
    # 手动填号给足人操作时间(≥300)；自动接码换号多次(CODEX_ADDPHONE_ATTEMPTS×CODEX_SMS_TIMEOUT)
    # 可能数分钟，超时按换号预算抬足（过 add-phone 后 drive_authorize 还会再续期捕获窗口）。
    _ph_budget = _env_int("CODEX_ADDPHONE_ATTEMPTS", 2) * _env_int("CODEX_SMS_TIMEOUT", 150)
    timeout = max(CODEX_TIMEOUT, 300, _ph_budget + 120)
    # 免手机直连尝试次数：>0 时每次重开窗口+cookie重登+重新生成 auth_url(全新会话=重摇风控)，
    # 弹手机就跳过本次换下一次赌，N 次都弹才在最后一次真接码。默认 0=直接一次性接码(不赌免手机)。
    # 实测部分新号 OAuth 必弹手机(手机要求偏向绑账号)，赌免手机多半白跑，故默认直接接码；想赌设 N>0。
    skip_n = _env_int("CODEX_PHONE_SKIP_ATTEMPTS", 0)
    try:
        # SUB2API: 登录 + 找 openai 分组（PKCE/换码由 SUB2API 包办）
        token = ox.sub2api_login(origin, SUB2API_EMAIL, SUB2API_PASSWORD)
        group_id = ox.find_group_id(origin, token, group)
        _skipmsg = f"免手机直连先试 {skip_n} 次，弹手机才接码" if skip_n > 0 else "直接一次性接码(不赌免手机)"
        print(f"  [codex] SUB2API: group={group}(#{group_id})，{_skipmsg}")

        # 每次尝试关窗口重开+重登(cookie)，确保是 OpenAI 眼里全新会话(同窗口重发 auth_url
        # 不改变其"要不要手机"的风控决定)，且避开刚注册窗口的促销弹层(Claim offer 等会挡住
        # 授权页 goto/consent，实测 in-register 复用脏窗口卡死、standalone 重开干净窗口就过)。
        # 故只要有 p+ctx 就总是重开窗口授权(不再限 skip_n>0)，让首次/唯一一次尝试也走干净窗口。
        reset_fn = None
        if p is not None and ctx is not None:
            try:
                cookies = await ctx.cookies()
                use_clash = (CHATGPT_NODE or "auto").lower() not in {"none", "off", "direct"}
                reset_fn = ox.make_reset_page(
                    p,
                    cookies,
                    account_email=email,
                    before_open=release_current,
                    browser_options=clash_browser_proxy_fields() if use_clash else None,
                )
            except Exception as e:
                print(f"  [codex] 构造窗口重置器失败(退化为复用窗口): {str(e)[:60]}")
                reset_fn = None

        # 驱动授权（先免手机 N 次，最后一次放开手机），捕获 localhost:1455 回调
        code, session_id, cb_state, msg = await ox.authorize_with_retry(
            page, lambda: ox.generate_auth_url(origin, token),
            account_email=email, phone_skip_attempts=skip_n,
            skip_timeout=120, phone_timeout=timeout, manual_phone=CODEX_MANUAL_PHONE,
            reset_page=reset_fn)
        if reset_fn is not None:
            try:
                await reset_fn.cleanup()
            except Exception:
                pass
        if not code:
            print(f"  [codex] 授权未完成: {msg}")
            return False

        # 换码 + 建 oauth 账号（带 refresh_token）
        exch = ox.exchange_code(origin, token, session_id, code, cb_state)
        cred = ox.build_oauth_credentials(exch)
        print(f"  [codex] exchange-code OK: refresh_token={'YES' if cred.get('refresh_token') else 'NO'} "
              f"plan={cred.get('plan_type')}")
        acct = ox.create_oauth_account(origin, token, cred, [group_id],
                                       name=cred.get("email") or email)
        acct_id = (acct or {}).get("id")
        print(f"  [codex] [OK] SUB2API 账号已创建 #{acct_id}（type=oauth，带 refresh_token）✅")

        # 同一份带真 rt 的凭据顺手推到 CPA（best-effort）
        if CPA_URL and CPA_MGMT_KEY:
            try:
                from common.session_export import build_cpa_codex_json_from_oauth
                from common.uploaders import upload_cpa
                cpa = build_cpa_codex_json_from_oauth(cred, email=cred.get("email") or email)
                cok, cmsg = upload_cpa(CPA_URL, CPA_MGMT_KEY, cpa["auth_json"], cpa["file_name"])
                print(f"  [codex][CPA] {'OK' if cok else 'FAIL'} {cpa['file_name']} - {cmsg}")
            except Exception as e:
                print(f"  [codex][CPA] 推送异常: {str(e)[:80]}")
        return True
    except Exception as e:
        print(f"  [codex] 提取失败: {str(e)[:120]}")
        return False


async def register_one(index, total, p):
    start = time.time()

    def check_timeout():
        if time.time() - start > REGISTER_TIMEOUT:
            raise TimeoutError(f"timeout {REGISTER_TIMEOUT}s")

    # 取邮箱。调试同一邮箱注册多平台时可通过 CLI 指定，避免邮箱池自动分配。
    if FIXED_EMAIL:
        email = FIXED_EMAIL
        email_pw = FIXED_PASSWORD or ""
        refresh_token = FIXED_REFRESH_TOKEN or ""
        client_id = FIXED_CLIENT_ID or ""
    else:
        em = email_pool.next_email(PLATFORM)
        if not em:
            print("  no email available")
            return None
        email, email_pw, refresh_token, client_id = em
    password = rand_password()
    print(f"\n#{index}/{total} email={email}")

    name = f"chatgpt_{time.strftime('%m%d_%H%M%S')}_{index}"
    bb = pid = None
    success = False
    try:
        use_clash = (CHATGPT_NODE or "auto").lower() not in {"none", "off", "direct"}
        bb, pid, browser, ctx, page = await open_and_connect(
            name=name,
            p=p,
            browser_options=clash_browser_proxy_fields() if use_clash else None,
        )
        await ctx.clear_cookies()
        auth_monitor = AuthResponseMonitor()
        page.on("response", auth_monitor.observe)

        # Step 1: 打开注册页（带重试，应对 ERR_CONNECTION_CLOSED 等偶发）
        print("  [1] goto signup")
        goto_ok = False
        for attempt in range(4):
            try:
                await page.goto(SIGNUP_URL, timeout=60000, wait_until="domcontentloaded")
                goto_ok = True
                break
            except Exception as e:
                print(f"  goto retry {attempt+1}/4: {str(e)[:70]}")
                await asyncio.sleep(4)
        if not goto_ok:
            print("  goto failed after retries")
            email_pool.mark_error(PLATFORM, email, email_pw, "goto_failed")
            return None
        await asyncio.sleep(5)
        await dump_state(page, "after-load")

        # Step 1.2: CF 全页拦截处理。先尝试点 Turnstile 勾选框(临界 IP 上有可点框，点了能过)，
        # 点几次仍不放行(AWS 等死锁转圈)再换 CF 友好节点重载。两手都试，覆盖不同 IP 信誉档。
        async def _try_pass_turnstile(rounds=4, wait=4):
            for _ in range(rounds):
                if not await _is_cf_blocked(page):
                    return True
                await _click_turnstile(page)
                await asyncio.sleep(wait)
            return not await _is_cf_blocked(page)

        if await _is_cf_blocked(page):
            print("  [cf] 检测到 Cloudflare 拦截，先尝试点 Turnstile 勾选框...")
            if await _try_pass_turnstile():
                print("  [cf] Turnstile 点击后放行 ✅")
            else:
                # 点不动/死锁 -> 轮换 CF 友好节点重载，每个节点再试点一次
                passed = False
                cf_candidates = _chatgpt_node_candidates()
                for cf_try in range(len(cf_candidates)):
                    node = _switch_cf_node()
                    print(f"  [cf] 点击未过，切节点 -> {node or '失败'} 重载({cf_try+1}/{len(cf_candidates)})...")
                    if not node:
                        break
                    await asyncio.sleep(3)
                    try:
                        await page.goto(SIGNUP_URL, timeout=60000, wait_until="domcontentloaded")
                    except Exception:
                        pass
                    await asyncio.sleep(5)
                    if await _try_pass_turnstile(rounds=2):
                        print(f"  [cf] 节点 {node} 放行 ✅")
                        passed = True
                        break
                if not passed:
                    print("  [cf] 点击+换遍节点仍被拦，放弃本号")
                    await dump_state(page, "cf-blocked")
                    email_pool.mark_error(PLATFORM, email, email_pw, "cf_blocked")
                    return None

        assert_chatgpt_node("before_email")

        # Step 1.5: 先关 cookie 同意横幅（弹出时会挡住/抢焦点，导致邮箱填不进去 -> "邮箱必填"）
        await dismiss_cookie_banner(page)
        # 关横幅后等页面稳定：横幅消失会触发重排/下拉渲染，等它落定再填，避免填到一半被重排打断
        await asyncio.sleep(1.2)
        await dismiss_cookie_banner(page)   # 横幅有时分两次弹，再补关一次
        await asyncio.sleep(0.6)

        # Step 2: 填邮箱 -> Continue
        print("  [2] fill email")
        email_input = page.locator('input[type="email"], input[name="email"]').first
        if await email_input.count() == 0:
            print("  email input not found")
            await page.screenshot(path=f"screenshots/chatgpt_noemail_{index}.png")
            email_pool.mark_error(PLATFORM, email, email_pw, "no_email_input")
            return None
        # 填邮箱（内部：每轮先关横幅再填，填完回读确认；见 fill_email_verified）
        if not await fill_email_verified(page, email_input, email):
            print("  [2] email fill failed after retries")
        # 提交前最后一道：关横幅（可能此刻才弹），并回读确认邮箱真在框里，否则再补填一次
        await dismiss_cookie_banner(page)
        try:
            if (await email_input.input_value()).strip() != email:
                print("  [2] email empty before submit, refilling once...")
                await fill_email_verified(page, email_input, email, tries=2)
        except Exception:
            pass
        # 关键优化：在提交邮箱（触发 OpenAI 发码）【之前】先把 Outlook 登录好、过隐私协议、
        # 停在收件箱。这样提交后码一到立刻能扫到，避免"发码后才登录、登录+过协议耗时错过码"。
        # 注意：必须用【独立 BitBrowser 窗口】预登录，绝不能在注册 ctx 里 new_page —— 同 context
        # 开 Outlook + bring_to_front 会干扰注册标签的 auth.openai.com 会话，导致点 Continue 后
        # ERR_CONNECTION_CLOSED。故另开窗口隔离（与 grok 的 noproxy 取码窗口同理）。
        mail_bb = mail_pid = mail_page = None
        prelogged = False
        mail_logged_in = False  # 取码窗口是否已登录(prelogin 成功 或 取码时登过)；跨 resend 复用，别反复关窗重登
        # 有可用 Graph token 时跳过浏览器预登录：取码首选 Graph API(get_code_by_token)直收，
        # 不必开浏览器登 Outlook。只有没 token(fresh/空)才预登录走浏览器取码兜底。
        has_token = bool(refresh_token) and refresh_token.strip().lower() != "fresh"
        if has_token:
            print(f"  [2.5] 有 Graph token，跳过浏览器预登录，取码走 Graph API")
        if email_pw and not has_token:
            try:
                print("  [2.5] pre-login Outlook (独立窗口) before sending code...")
                mail_bb, mail_pid, _mb, _mctx, mail_page = await open_and_connect(
                    name=f"mail_{time.strftime('%m%d_%H%M%S')}_{index}", p=p)
                prelogged = await prelogin_outlook(mail_page, email, email_pw)
                mail_logged_in = prelogged
                print(f"  [2.5] outlook prelogin: {'ready' if prelogged else 'failed'}")
                # 登录后稍等 10s 再发码：刚登录 Outlook 收件箱/同步还没就绪，立刻发码易"码到了却没同步进来"。
                if prelogged:
                    print("  [2.5] prelogin ready, 等 10s 让收件箱就绪再发码...")
                    await asyncio.sleep(10)
            except Exception as e:
                print(f"  [2.5] prelogin error: {str(e)[:60]}")
        # 提交：按钮文本中/英/日多语言精确匹配，避免点到 Continue with Google/Apple
        if not await click_any_exact(page, ["Continue", "続行", "继续", "繼續", "Next", "下一步", "Teruskan"]):
            sub = page.locator('button[type="submit"]')
            if await sub.count() > 0:
                await sub.first.click()
            else:
                await email_input.press("Enter")
        await asyncio.sleep(5)
        check_timeout()
        await dump_state(page, "after-email")
        # 若仍停在登录页报"邮箱必填/required"，补填再交一次
        try:
            body_l = (await page.locator("body").inner_text()).lower()
        except Exception:
            body_l = ""
        if any(k in body_l for k in ["必須", "必填", "required", "is required"]):
            print("  [2] still on login (email required), refilling once...")
            await dismiss_cookie_banner(page)
            await fill_email_verified(page, email_input, email)
            if not await click_any_exact(page, ["Continue", "続行", "继续", "繼續", "Teruskan"]):
                sub = page.locator('button[type="submit"]')
                if await sub.count() > 0:
                    await sub.first.click()
            await asyncio.sleep(5)
            await dump_state(page, "after-email-retry")

        # 部分登录页无错误提示，只把 ?email= 写进 URL 并留在原邮箱表单。
        # 这种状态不能进入 onboarding，否则会反复点击同一个 Continue 后误到游客首页。
        for submit_retry in range(2):
            if await chatgpt_email_submission_advanced(page):
                break
            print(f"  [2] email form did not advance, retrying submit {submit_retry + 1}/2...")
            await dismiss_cookie_banner(page)
            email_input = page.locator(
                'input[type="email"], input[name="email"]'
            ).first
            await fill_email_verified(page, email_input, email, tries=2)
            if not await click_any_exact(
                page,
                ["Continue", "続行", "继续", "繼續", "Next", "下一步", "Teruskan", "Weiter"],
            ):
                sub = page.locator('button[type="submit"]')
                if await sub.count() > 0:
                    await sub.first.click()
                else:
                    await email_input.press("Enter")
            await asyncio.sleep(5)
            await dump_state(page, f"after-email-stuck-retry-{submit_retry + 1}")
        if not await chatgpt_email_submission_advanced(page):
            print("  [2][FAIL] email form remained on login after retries")
            email_pool.mark_error(PLATFORM, email, email_pw, "email_submit_stuck")
            return None

        # Step 3: 可能出现密码页 / 验证码页 / challenge
        # 先检测 challenge
        if await detect_challenge(page):
            print("  [!] challenge detected after email (Arkose/Turnstile)")
            await page.screenshot(path=f"screenshots/chatgpt_challenge_{index}.png")
            # 等待自动过（真实指纹有时能过），最多 30s
            for _ in range(6):
                await asyncio.sleep(5)
                if not await detect_challenge(page):
                    print("  challenge cleared")
                    break

        # 密码输入（注册流程会让设密码）
        pw_input = page.locator('input[type="password"], input[name="password"], input[name="new-password"]')
        if await pw_input.count() > 0:
            print("  [3] fill password")
            await human_type(page, 'input[type="password"]', password)
            await asyncio.sleep(1)
            if not await click_exact(page, "Continue"):
                sub = page.locator('button[type="submit"]')
                if await sub.count() > 0:
                    await sub.first.click()
            await asyncio.sleep(5)
            await dump_state(page, "after-password")
        check_timeout()

        # Step 4: 邮件验证码
        # ChatGPT 通常发 6 位验证码或确认链接
        verification_code_failed = False
        code_input = page.locator('input[inputmode="numeric"], input[name="code"], input[autocomplete="one-time-code"], input[type="text"]')
        if await code_input.count() > 0 or "verify" in page.url.lower() or "check" in (await page.locator("body").inner_text()).lower():
            code_sel = 'input[inputmode="numeric"], input[name="code"], input[autocomplete="one-time-code"], input[type="text"]'

            async def _fetch_email_code(received_after=None, allow_browser_fallback=False):
                """取一次码：先 Graph token，失败再浏览器登录 Outlook 取信。
                取码窗口**跨 resend 复用**：已登录就只刷新收件箱轮询(skip_login)，不关窗不重登。
                窗口统一在 Step 4 结束后的兜底处一次性 teardown。
                有可用 Graph token(has_token)时**只走 Graph API、不开浏览器**：API 取码已直连可靠，
                浏览器兜底只是去查同一个收件箱、纯浪费；取不到码该靠上层 resend 重发，而非开窗口。
                received_after: resend 后传重发时刻，只收该时刻后到的邮件(旧码已被 OpenAI 作废)。"""
                nonlocal mail_bb, mail_pid, mail_page, mail_logged_in
                c = await asyncio.get_event_loop().run_in_executor(
                    None, functools.partial(
                        get_code_by_token, email, refresh_token, client_id or None,
                        OAI_SENDER, OAI_SUBJECT, r"\b(\d{6})\b", 40, 5,
                        received_after=received_after)
                )
                if c or (has_token and not allow_browser_fallback):
                    return c
                if not c and email_pw:
                    # 窗口没了才开新窗(首次没 prelogin、或窗口意外掉线)；否则复用同一窗口
                    if mail_page is None:
                        print("  [4] token failed, opening Outlook window to get code...")
                        try:
                            mail_bb, mail_pid, _mb, _mctx, mail_page = await open_and_connect(
                                name=f"mail_{time.strftime('%m%d_%H%M%S')}_{index}", p=p)
                            mail_logged_in = False
                        except Exception as e:
                            print(f"  [4] open mail window failed: {str(e)[:60]}")
                            mail_page = None
                    elif mail_logged_in:
                        print("  [4] token failed, 复用已登录 Outlook 窗口轮询收件箱...")
                    else:
                        print("  [4] token failed, polling Outlook inbox...")
                    if mail_page is not None:
                        try:
                            c = await get_code_outlook_pw(
                                mail_page, email, email_pw,
                                sender_hint=("openai", "noreply", "no-reply"),
                                subject_hint=("code", "verify", "openai", "chatgpt", "验证"),
                                code_regex=r"\b(\d{6})\b", max_wait=150, poll=8,
                                skip_login=mail_logged_in,
                            )
                            # 跑过一次 get_code_outlook_pw 即已登录(其内部会登)，后续 resend 复用免重登
                            mail_logged_in = True
                        except Exception as e:
                            print(f"  [4] 取码窗口异常: {str(e)[:60]}")
                    # 主注册页可能在 150s 取码等待期间被关/掉线，bring_to_front 会抛；
                    # 这里必须吞掉，否则异常会冲出 for code_try 重试循环、直奔外层 except，
                    # 让 resend 兜底永远没机会跑(实测一次 timeout 就整号失败的根因)。
                    try:
                        await page.bring_to_front()
                    except Exception as e:
                        print(f"  [4] 主页 bring_to_front 失败(忽略): {str(e)[:60]}")
                return c

            async def _renavigate_resend():
                """收不到码且页面无 Resend：回退 signup 重输邮箱重新发码（用户建议的兜底）。"""
                print("  [4] 回退 ChatGPT signup，重输邮箱重新发码...")
                try:
                    await page.goto(SIGNUP_URL, timeout=60000, wait_until="domcontentloaded")
                    await asyncio.sleep(4)
                    await dismiss_cookie_banner(page)
                    ei = page.locator('input[type="email"], input[name="email"]').first
                    if await ei.count() == 0:
                        print("  [4] 回退后无邮箱框，放弃重发")
                        return
                    await fill_email_verified(page, ei, email)
                    await dismiss_cookie_banner(page)
                    if not await click_any_exact(page, ["Continue", "続行", "继续", "繼續", "Next", "下一步", "Teruskan"]):
                        sub = page.locator('button[type="submit"]')
                        if await sub.count() > 0:
                            await sub.first.click()
                    await asyncio.sleep(5)
                    # 可能落到密码页（已注册一半），填密码推进回验证码页
                    pw = page.locator('input[type="password"]')
                    if await pw.count() > 0:
                        await human_type(page, 'input[type="password"]', password)
                        await asyncio.sleep(1)
                        if not await click_exact(page, "Continue"):
                            sub = page.locator('button[type="submit"]')
                            if await sub.count() > 0:
                                await sub.first.click()
                        await asyncio.sleep(4)
                except Exception as e:
                    print(f"  [4] 回退重发异常: {str(e)[:80]}")

            code = None
            resend_at = None  # 最近一次 resend 时刻；传给取码只收此后到的新码(旧码 resend 后失效)
            for code_try in range(3):
                # 主页已关就别再空转：resend/_renavigate 都要在活页上操作，死页只会再耗 2×150s。
                try:
                    if page.is_closed():
                        print("  [4] 主注册页已关闭，无法 resend，提前结束取码")
                        break
                except Exception:
                    pass
                if code_try == 0:
                    print("  [4] waiting for email verification code...")
                else:
                    print(f"  [4] 收不到码，重试 {code_try}/2：先点 Resend，没有则回退重输邮箱...")
                    if not await _click_resend_code(page):
                        await _renavigate_resend()
                    resend_at = time.time()  # 重发后只认此刻之后的新码
                    await asyncio.sleep(2)
                code = await _fetch_email_code(
                    received_after=resend_at,
                    allow_browser_fallback=should_use_browser_mail_fallback(
                        has_token, code_try
                    ),
                )
                if code:
                    break

            if code:
                print(f"  got code: {code}")
                await dismiss_cookie_banner(page)
                ci = page.locator(code_sel).first
                # 填码（React 受控输入：键盘逐字+JS setter 兜底；fill 不触发 onChange 会停在验证页）
                if not await react_fill(page, code_sel, code, tries=3):
                    print("  [4] code fill not committed after retries")
                # 提交（中/英/日多语言精确匹配）
                if not await click_any_exact(page, ["Continue", "続行", "Verify", "確認", "确认", "继续", "Submit", "次へ", "Teruskan", "Sahkan"]):
                    sub = page.locator('button[type="submit"]')
                    if await sub.count() > 0:
                        await sub.first.click()
                await asyncio.sleep(5)
                await dump_state(page, "after-code")
                # 若仍停在验证页（码没被接受/没提交成功），补填再交一次
                if any(k in page.url.lower() for k in ["verification", "verify", "email-verification"]):
                    print("  [4] still on verification page, re-submitting code once...")
                    await react_fill(page, code_sel, code, tries=2, verbose=False)
                    if not await click_any_exact(page, ["Continue", "続行", "Verify", "確認", "确认", "Teruskan", "Sahkan"]):
                        sub = page.locator('button[type="submit"]')
                        if await sub.count() > 0:
                            await sub.first.click()
                    await asyncio.sleep(5)
                    await dump_state(page, "after-code-retry")
            else:
                print("  no code received")
                # 收不到码：只从 chatgpt 平台拉黑（记 emails_error_chatgpt.txt），其它平台仍可取
                email_pool.mark_error(PLATFORM, email, email_pw, "no_code")
                verification_code_failed = True
        # 兜底：关掉可能残留的预登录邮箱独立窗口（如 token 路径直接拿到码、或没进验证码分支）
        if mail_bb and mail_pid:
            try:
                await teardown(mail_bb, mail_pid, delete=True)
            except Exception:
                pass
            mail_bb = mail_pid = mail_page = None
        if verification_code_failed:
            print("  [4][FAIL] verification code unavailable; stopping before onboarding")
            return None
        check_timeout()

        # Step 5: onboarding（名字/生日）。账号 auth session 建立后禁止切换出口。
        assert_chatgpt_node("before_onboarding")
        await handle_onboarding(page, index, auth_monitor=auth_monitor)
        if "about-you" in page.url.lower():
            raise RuntimeError("onboarding_not_completed")
        check_timeout()

        # Step 6: 跳到 chatgpt.com 确保 cookie 落到主域
        try:
            await page.goto("https://chatgpt.com/", timeout=45000, wait_until="domcontentloaded")
            await asyncio.sleep(5)
        except Exception:
            pass
        await dump_state(page, "final")

        # 保存 cookie
        key_val, _ = await save_platform_cookies(
            ctx, PLATFORM, pid, email=email, password=password, key_cookie_names=KEY_COOKIES
        )

        # 导出标准 token（CPA codex / SUB2API content），失败不影响成功判定
        try:
            from common.session_export import fetch_chatgpt_session, save_chatgpt_tokens
            sess = await fetch_chatgpt_session(page)
            if sess and save_chatgpt_tokens(sess, email):
                print("  [OK] chatgpt 标准 token 已保存")
            else:
                print("  [WARN] 未取到 chatgpt session（可能未完全登录）")
        except Exception as e:
            print(f"  [WARN] 保存标准 token 失败: {e}")
            sess = None

        # 即时导入 chatgpt2api（--import-c2a；用刚抓到的 session 直接 POST，单号失败不影响注册成功）
        if IMPORT_C2A:
            import_chatgpt2api(sess, email)

        # 顺手走 Codex OAuth 提取 rt 导入 SUB2API（--codex；复用已登录窗口，失败不影响注册成功）
        if EXTRACT_CODEX and key_val:
            try:
                async def _release_registration_profile():
                    nonlocal bb, pid
                    if bb and pid:
                        print("  [codex] 释放注册窗口，为 OAuth 重试窗口腾出 profile 配额")
                        await teardown(bb, pid, delete=True)
                        bb = pid = None

                await extract_codex(
                    page,
                    email,
                    p=p,
                    ctx=ctx,
                    release_current=_release_registration_profile,
                )
            except Exception as e:
                print(f"  [codex] 异常: {str(e)[:120]}")
        elif EXTRACT_CODEX:
            print("  [codex] 无 ChatGPT 登录态，跳过 OAuth")

        if key_val:
            email_pool.mark_used(PLATFORM, email, email_pw)
            success = True
            print(f"  [OK] session cookie saved")
            return key_val
        else:
            print("  [FAIL] no session cookie")
            email_pool.mark_error(PLATFORM, email, email_pw, "no_session_cookie")
            return None

    except Exception as e:
        print(f"  ERROR: {e}")
        if email:
            email_pool.mark_error(PLATFORM, email, email_pw, str(e)[:50])
        return None
    finally:
        if bb and pid:
            keep = KEEP_ON_FAIL and not success
            await teardown(bb, pid, delete=not keep)
            if keep:
                print(f"  [debug] window kept for inspection: {name} (id={pid})")


async def blur_field(page, selector):
    """让输入框失焦：触发 React 的 onBlur 校验。
    坑：about-you 页 age 是最后填的字段，keyboard.type/JS setter 只发 input/change，
    从不失焦 -> onBlur 校验不跑 -> 'Finish creating account' 按钮一直 disabled，
    既点不动也匹配不到唯一按钮，于是 handle_onboarding 空转卡死。"""
    try:
        el = page.locator(selector).first
        if await el.count() == 0:
            return
        await el.evaluate(
            """(node) => {
                node.dispatchEvent(new Event('blur', {bubbles: true}));
                node.dispatchEvent(new Event('focusout', {bubbles: true}));
                if (typeof node.blur === 'function') node.blur();
            }"""
        )
    except Exception:
        pass


async def _raise_onboarding_error(page, index, auth_monitor=None):
    error = await auth_monitor.latest() if auth_monitor else None
    if not error:
        try:
            body = await page.locator("body").inner_text()
        except Exception:
            body = ""
        error = _openai_error_from_text(body, 400, "/about-you")
        if error and error["code"] == "http_400":
            error = None
    if not error:
        return
    print(
        f"  [onboarding] service rejected: code={error['code']} "
        f"status={error['status']} path={error['url']} message={error['message'][:120]}"
    )
    try:
        await page.screenshot(path=f"screenshots/chatgpt_onboarding_rejected_{index}.png")
    except Exception:
        pass
    raise OnboardingRejected(f"{error['code']}: {error['message']}")


async def recover_stuck_onboarding_session(page):
    """Verify a completed account in a separate tab when about-you fails to navigate."""
    probe = None
    try:
        from common.session_export import fetch_chatgpt_session

        probe = await page.context.new_page()
        await probe.goto(
            "https://chatgpt.com/", timeout=45000, wait_until="domcontentloaded"
        )
        await asyncio.sleep(3)
        session = await fetch_chatgpt_session(probe)
        if not session:
            return False
        main_ui = probe.locator(
            '[data-testid="composer-speech-button"], textarea, #prompt-textarea'
        )
        if await main_ui.count() == 0:
            return False
        print("  [onboarding] account session is valid despite stuck about-you URL")
        await page.goto(
            "https://chatgpt.com/", timeout=45000, wait_until="domcontentloaded"
        )
        return True
    except Exception as error:
        print(f"  [onboarding] session recovery check failed: {str(error)[:80]}")
        return False
    finally:
        if probe is not None:
            try:
                await probe.close()
            except Exception:
                pass


_REQUIRED_ONBOARDING_CONSENT_SELECTOR = ", ".join((
    'input[type="checkbox"][name="personalInfoConsent"]',
    'input[type="checkbox"][name="thirdPartyConsent"]',
    'input[type="checkbox"][name="overseasTransferConsent"]',
    'input[type="checkbox"][required]',
    'input[type="checkbox"][aria-required="true"]',
    '[role="checkbox"][aria-required="true"]',
))


async def ensure_required_onboarding_consents(page):
    """Check required about-you consents without opting into optional choices."""
    boxes = page.locator(_REQUIRED_ONBOARDING_CONSENT_SELECTOR)
    total = await boxes.count()
    checked = 0
    changed = 0

    async def is_checked(box):
        try:
            return bool(await box.is_checked())
        except Exception:
            return (await box.get_attribute("aria-checked")) == "true"

    for i in range(total):
        box = boxes.nth(i)
        if await is_checked(box):
            checked += 1
            continue
        try:
            await box.check(force=True, timeout=4000)
        except Exception:
            try:
                await box.click(force=True, timeout=4000)
            except Exception:
                try:
                    await box.evaluate("""el => {
                        if (el instanceof HTMLInputElement) {
                            const setter = Object.getOwnPropertyDescriptor(
                                HTMLInputElement.prototype, 'checked'
                            )?.set;
                            if (setter) setter.call(el, true);
                            else el.checked = true;
                            el.dispatchEvent(new Event('input', {bubbles: true}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                        } else {
                            el.click();
                        }
                    }""")
                except Exception:
                    continue
        await asyncio.sleep(0.15)
        if await is_checked(box):
            checked += 1
            changed += 1

    if changed:
        print(f"  [onboarding] accepted required consents: {checked}/{total}")
    return total, checked


async def click_finish_button(page, index, age_sel, auth_monitor=None, max_wait=12):
    """about-you 页专用：等 'Finish creating account' 按钮从 disabled 变可用后点击。
    返回是否点击成功。先尝试文案精确匹配，再退化为唯一非第三方登录按钮；
    若超时仍 disabled，dump 诊断（按钮 outerHTML + 各字段值 + 截图）便于排查。"""
    finish_labels = [
        "Finish creating account", "アカウントの作成を完了する",
        "\uacc4\uc815 \uc0dd\uc131 \ub05d\ub0b4\uae30",
        "完成建立帳戶", "完成建立帳號", "完成創建帳戶", "完成創建帳號",
        "完成创建账户", "完成创建账号", "完成建立账户",
        "Selesaikan penciptaan akaun", "Selesaikan penciptaan",
    ]

    async def find_btn():
        # 1) 文案精确匹配
        for label in finish_labels:
            try:
                b = page.get_by_role("button", name=label, exact=True)
                if await b.count() > 0:
                    return b.first
            except Exception:
                pass
        # 2) 退化：唯一的非第三方登录/返回按钮
        try:
            cand = page.locator("button").filter(
                has_not_text="Google").filter(has_not_text="Apple").filter(has_not_text="Back")
            if await cand.count() == 1:
                return cand.first
        except Exception:
            pass
        return None

    # 轮询等待按钮可用（onBlur 校验通过后 disabled 才解除）
    deadline = time.time() + max_wait
    while time.time() < deadline:
        btn = await find_btn()
        if btn is not None:
            try:
                disabled = await btn.get_attribute("disabled")
                aria_dis = await btn.get_attribute("aria-disabled")
            except Exception:
                disabled = aria_dis = None
            if disabled is None and aria_dis != "true":
                try:
                    if auth_monitor:
                        await auth_monitor.clear()
                    await btn.click(timeout=6000)
                    print("  [onboarding] clicked Finish button")
                    # 关键：点了不等于提交成功。about-you 表单常出现"按钮可点但 submit 被
                    # 服务端拒/未导航"——必须验证是否真离开 about-you 页；没走就升级提交手段
                    # (age 框回车 + form.requestSubmit)，否则上层会误判成功、再 re-fill 把按钮搞回 disabled。
                    for _ in range(4):
                        await asyncio.sleep(1.5)
                        await _raise_onboarding_error(page, index, auth_monitor)
                        if "about-you" not in page.url.lower():
                            return True
                    print("  [onboarding] 点了 Finish 但仍在 about-you，升级提交(Enter + requestSubmit)...")
                    try:
                        ae = page.locator(age_sel).first
                        if await ae.count() > 0:
                            await ae.press("Enter", timeout=2000)
                    except Exception:
                        pass
                    try:
                        await btn.evaluate("(b) => { const f = b.closest('form'); if (f) f.requestSubmit ? f.requestSubmit(b) : f.submit(); }")
                    except Exception:
                        pass
                    for _ in range(4):
                        await asyncio.sleep(1.5)
                        await _raise_onboarding_error(page, index, auth_monitor)
                        if "about-you" not in page.url.lower():
                            return True
                    if await recover_stuck_onboarding_session(page):
                        return True
                    # 仍没走：返回 False，让上层别再 re-fill(会重置 React 态、按钮重新 disabled)，
                    # 而是下一轮检测到还在 about-you 时只重试点击。
                    print("  [onboarding] 升级提交后仍在 about-you")
                    return False
                except Exception as e:
                    print(f"  [onboarding] Finish click failed: {str(e)[:60]}")
        await asyncio.sleep(1)

    # 仍未点动：dump 诊断
    print("  [onboarding] Finish button still disabled after wait, dumping diagnostics:")
    try:
        btn = await find_btn()
        if btn is not None:
            html = await btn.evaluate("(n) => n.outerHTML")
            print(f"    button: {html[:200]}")
    except Exception:
        pass
    try:
        for s in [age_sel, 'input[name="name"]']:
            el = page.locator(s).first
            if await el.count() > 0:
                print(f"    {s} value = '{await el.input_value()}'")
    except Exception:
        pass
    try:
        await page.screenshot(path=f"screenshots/chatgpt_onboarding_stuck_{index}.png")
    except Exception:
        pass
    return False


async def dump_onboarding_fields(page, tag=""):
    """dump onboarding 页的所有 input/select 结构，便于适配未知布局（age 页 / birthday 页）。"""
    try:
        print(f"  [onboarding-dump {tag}] url={page.url}")
        n = await page.locator("input").count()
        for i in range(min(n, 10)):
            el = page.locator("input").nth(i)
            try:
                print(f"    input[{i}] type={await el.get_attribute('type')} "
                      f"name={await el.get_attribute('name')} "
                      f"placeholder={await el.get_attribute('placeholder')} "
                      f"inputmode={await el.get_attribute('inputmode')} "
                      f"aria-label={await el.get_attribute('aria-label')}")
            except Exception:
                pass
        ns = await page.locator("select").count()
        for i in range(min(ns, 6)):
            el = page.locator("select").nth(i)
            try:
                print(f"    select[{i}] name={await el.get_attribute('name')} "
                      f"aria-label={await el.get_attribute('aria-label')}")
            except Exception:
                pass
        # combobox/listbox（自定义下拉，非原生 select）
        nc = await page.get_by_role("combobox").count()
        if nc:
            print(f"    comboboxes: {nc}")
    except Exception as e:
        print(f"  [onboarding-dump] error: {e}")


async def handle_onboarding(page, index, max_rounds=6, auth_monitor=None):
    """处理注册后的引导页：名字、生日/年龄、各种 Continue/Agree"""
    name_done = False  # about-you 名字只填一次，避免每轮重置成新随机名
    age_done = False   # 年龄同理只填一次：re-fill 会重置 React 态、把已解禁的 Finish 按钮搞回 disabled
    bday_done = False
    for r in range(max_rounds):
        await asyncio.sleep(2)
        body = (await page.locator("body").inner_text()).lower()
        url = page.url.lower()
        if r == 0:
            await dump_onboarding_fields(page, tag=f"round{r}")  # 首轮 dump 结构，便于排查未知布局

        name_sel = 'input[name="name"], input[placeholder*="name" i], input[placeholder*="全名"], input[placeholder*="姓名"], input[autocomplete="name"]'
        age_sel = 'input[name="age"], input[type="number"], input[placeholder*="age" i], input[placeholder*="年齢"], input[placeholder*="年龄"]'
        on_about_you = await page.locator(age_sel).count() > 0

        # about-you 页（名字+年龄）：填一次 -> 失焦触发校验 -> 等按钮可用后点 Finish。
        # 这里独立处理，不走下面的泛化 Continue 匹配（会被 disabled 按钮卡住空转）。
        # 名字/年龄都只填一次(name_done/age_done)：re-fill 会重置 React 态、把按钮搞回 disabled，
        # 导致 round0 点了没走、round1 re-fill 后反而点不动的死循环(实测根因)。
        if on_about_you:
            if not name_done and await page.locator(name_sel).count() > 0:
                first, last = rand_name()
                # delay/settle 调低：名字/年龄是 onboarding 的本地字段，不像邮箱要防风控，快点键入即可
                if await react_fill(page, name_sel, f"{first} {last}", tries=2, delay=12, settle=0.15, verbose=False):
                    print(f"  [onboarding] name: {first} {last}")
                    name_done = True
                    await blur_field(page, name_sel)
                    await asyncio.sleep(0.2)
            if not age_done:
                if await react_fill(page, age_sel, str(random.randint(18, 40)), tries=2, delay=12, settle=0.15, verbose=False):
                    print("  [onboarding] age filled")
                    age_done = True
                    # 关键：失焦让 onBlur 校验跑起来，Finish 按钮才会解除 disabled
                    await blur_field(page, age_sel)
                    await asyncio.sleep(0.3)
            consent_total, consent_checked = await ensure_required_onboarding_consents(page)
            if consent_total and consent_checked < consent_total:
                print(
                    "  [onboarding] required consents are not ready: "
                    f"{consent_checked}/{consent_total}"
                )
                await asyncio.sleep(1)
                continue
            if await click_finish_button(
                page, index, age_sel, auth_monitor=auth_monitor
            ):
                await asyncio.sleep(3)
                continue  # 进入下一轮看是否还有后续引导页
            # 没点动则继续往下走泛化兜底（极少数布局）

        # 名字（其它引导页：input name=name placeholder=全名/Full name，多语言界面）
        if not on_about_you and await page.locator(name_sel).count() > 0:
            first, last = rand_name()
            if await react_fill(page, name_sel, f"{first} {last}", tries=2, verbose=False):
                print(f"  [onboarding] name: {first} {last}")
                await asyncio.sleep(1)

        # 生日页：仅当存在**可见**生日输入框时才处理（另一种 onboarding 布局）。
        # 注意 about-you 页有个 name=birthday 的 type=hidden 字段，是 OpenAI 前端按 age 自动算的，
        # 绝不能碰 —— 故这里排除 hidden，用 :visible 限定，避免误填隐藏框导致卡死。
        bday = page.locator(
            'input[type="date"]:visible, '
            'input[name="birthday"]:not([type="hidden"]):visible, '
            'input[name="dob"]:visible, '
            'input[placeholder*="birth" i]:visible, input[placeholder*="生日"]:visible, '
            'input[placeholder*="出生"]:visible, '
            'input[placeholder*="DD" i]:visible, input[placeholder*="MM" i]:visible, '
            'input[placeholder*="YYYY" i]:visible')
        if not bday_done and not on_about_you and await bday.count() > 0:
            filled = False
            # 1) 原生 date：fill ISO 即可
            try:
                first_bday = bday.first
                btype = await first_bday.get_attribute("type")
                if btype == "date":
                    await first_bday.fill("1995-06-15")
                    filled = (await first_bday.input_value()).strip() != ""
            except Exception:
                pass
            # 2) React 受控文本/分段（MM/DD/YYYY 等）：逐个填
            if not filled:
                cnt = await bday.count()
                if cnt >= 3:
                    # 分段 month/day/year 三框：按 placeholder 判断填 06 / 15 / 1995
                    for i in range(min(cnt, 3)):
                        seg = bday.nth(i)
                        ph = (await seg.get_attribute("placeholder") or "").lower()
                        v = "1995" if ("y" in ph or "年" in ph) else ("15" if ("d" in ph or "日" in ph) else "06")
                        try:
                            await seg.click(timeout=4000)
                            await seg.press("Control+A", timeout=2000)
                            await seg.press("Delete", timeout=2000)
                            await page.keyboard.type(v, delay=12)
                        except Exception:
                            pass
                    filled = True
                else:
                    # 单框文本日期：试 ISO，再试 MM/DD/YYYY
                    for v in ["1995-06-15", "06/15/1995"]:
                        if await react_fill(page, 'input[type="date"]:visible, input[name="dob"]:visible',
                                            v, tries=1, delay=12, settle=0.15, verbose=False):
                            filled = True
                            break
            if filled:
                print("  [onboarding] birthday filled")
                bday_done = True
                await blur_field(page, 'input[type="date"]:visible, input[name="dob"]:visible')
                await asyncio.sleep(0.3)

        # 点完成/续行（多语言：中/繁/英/日）。具体"完成创建账号"按钮优先于泛化 Continue，
        # 否则 about-you 页只有 'Finish creating account' 这一个按钮会被泛化匹配漏掉。
        clicked = False
        for label in [
                # 具体完成按钮(优先)：英 / 日 / 繁(港台) / 简 / 马来(代理走马来节点时 OpenAI 返回 Bahasa Melayu)
                "Finish creating account", "アカウントの作成を完了する",
                "\uacc4\uc815 \uc0dd\uc131 \ub05d\ub0b4\uae30",
                "完成建立帳戶", "完成建立帳號", "完成創建帳戶", "完成創建帳號",
                "完成创建账户", "完成创建账号", "完成建立账户",
                "Selesaikan penciptaan akaun", "Selesaikan penciptaan",
                # 泛化续行/同意：英/中/繁/日/马来
                "Continue", "继续", "繼續", "Agree", "同意", "I agree", "Next", "下一步",
                "Get started", "开始", "Confirm", "确认", "確認", "Submit", "提交", "保存", "完成",
                "続行", "完了", "次へ", "同意する", "はい", "始める",
                "Teruskan", "Setuju", "Mula"]:
            if await click_exact(page, label):
                print(f"  [onboarding] clicked {label}")
                clicked = True
                await asyncio.sleep(3)
                break

        # 结构化兜底：标签没命中（如代理切到马来/法语/日语等界面，文本对不上）时，
        # about-you 页通常只有一个主按钮 —— 直接回车提交 + 点唯一可用按钮，不依赖文案。
        if not clicked and await page.locator(age_sel).count() > 0:
            try:
                await page.locator(age_sel).first.press("Enter")
                await asyncio.sleep(2)
            except Exception:
                pass
            try:
                # 选页面上唯一“可点”的非返回按钮（排除 Google/Apple/手机第三方登录、返回）
                btn = page.locator(
                    'button:not([disabled]):not([aria-disabled="true"])'
                ).filter(has_not_text="Google").filter(has_not_text="Apple").filter(has_not_text="Back")
                n = await btn.count()
                if n == 1:
                    await btn.first.click(timeout=8000)
                    print("  [onboarding] clicked sole submit button (structural fallback)")
                    clicked = True
                    await asyncio.sleep(3)
                else:
                    # 多按钮时点最后一个可用按钮（主操作通常在最后）
                    sub = page.locator('button[type="submit"]:not([disabled])')
                    if await sub.count() > 0:
                        await sub.last.click(timeout=8000)
                        print("  [onboarding] clicked submit[type] (structural fallback)")
                        clicked = True
                        await asyncio.sleep(3)
            except Exception as e:
                print(f"  [onboarding] structural fallback failed: {str(e)[:60]}")

        # 已进入主界面
        if "chatgpt.com" in url and "auth" not in url and "onboarding" not in url:
            if await page.locator('[data-testid="composer-speech-button"], textarea, #prompt-textarea').count() > 0:
                print("  [onboarding] reached main UI")
                return
        if not clicked and await page.locator(name_sel).count() == 0 and await bday.count() == 0:
            # 没有可操作元素，可能已完成
            break


async def main():
    parser = argparse.ArgumentParser(description="ChatGPT Auto Register")
    parser.add_argument("--count", "-n", type=int, default=1)
    parser.add_argument("--concurrency", "-c", type=int, default=1)
    parser.add_argument("--timeout", "-t", type=int, default=480)
    parser.add_argument("--node", default="auto",
                        help="固定 ChatGPT Clash 节点；auto 自动探测，none 直连")
    parser.add_argument("--keep-on-fail", action="store_true", help="失败时保留窗口便于排查")
    parser.add_argument("--email", default=None, help="指定邮箱(绕过邮箱池)")
    parser.add_argument("--password", default=None, help="指定邮箱密码")
    parser.add_argument("--refresh-token", default=None, help="指定 Outlook refresh_token")
    parser.add_argument("--client-id", default=None, help="指定 Outlook OAuth client_id")
    parser.add_argument("--import-c2a", action="store_true",
                        help="注册成功后即时把 token 导入 chatgpt2api (POST <host>/api/accounts)")
    parser.add_argument("--c2a-url", default=None, help="chatgpt2api host (默认取 config.CHATGPT2API_URL)")
    parser.add_argument("--c2a-key", default=None, help="chatgpt2api admin key (默认取 config.CHATGPT2API_KEY)")
    parser.add_argument("--codex", action="store_true",
                        help="注册成功后顺手走 Codex OAuth 提取 refresh_token 导入 SUB2API (oauth 账号可续期)")
    parser.add_argument("--codex-group", default=None,
                        help="SUB2API 目标分组名 (默认取 config.SUB2API_GROUP)")
    parser.add_argument("--codex-manual-phone", action="store_true",
                        help="Codex add-phone 手动模式: 不接码, 自己在浏览器填号收码")
    parser.add_argument("--codex-timeout", type=int, default=120,
                        help="Codex 授权捕获超时秒 (手动填号会自动抬到至少 300)")
    args = parser.parse_args()

    global REGISTER_TIMEOUT, KEEP_ON_FAIL, FIXED_EMAIL, FIXED_PASSWORD, FIXED_REFRESH_TOKEN, FIXED_CLIENT_ID
    global IMPORT_C2A, C2A_URL, C2A_KEY
    global EXTRACT_CODEX, CODEX_GROUP, CODEX_MANUAL_PHONE, CODEX_TIMEOUT, CHATGPT_NODE
    REGISTER_TIMEOUT = args.timeout
    KEEP_ON_FAIL = args.keep_on_fail
    FIXED_EMAIL = args.email
    FIXED_PASSWORD = args.password
    FIXED_REFRESH_TOKEN = args.refresh_token
    FIXED_CLIENT_ID = args.client_id
    IMPORT_C2A = args.import_c2a
    C2A_URL = args.c2a_url
    C2A_KEY = args.c2a_key
    EXTRACT_CODEX = args.codex
    CODEX_GROUP = args.codex_group
    CODEX_MANUAL_PHONE = args.codex_manual_phone
    CODEX_TIMEOUT = args.codex_timeout
    CHATGPT_NODE = args.node

    if IMPORT_C2A and not ((C2A_URL or CHATGPT2API_URL) and (C2A_KEY or CHATGPT2API_KEY)):
        print("  [c2a][WARN] 已开 --import-c2a 但未配置 CHATGPT2API_URL/KEY（--c2a-url/--c2a-key 或 .env），导入会被跳过")

    if EXTRACT_CODEX and not (SUB2API_URL and SUB2API_EMAIL and SUB2API_PASSWORD):
        print("  [codex][WARN] 已开 --codex 但未配置 SUB2API_URL/EMAIL/PASSWORD（.env），Codex 提取会被跳过")

    try:
        select_chatgpt_node(CHATGPT_NODE)
    except Exception as e:
        print(f"  [node][FAIL] {e}")
        return 2

    if args.concurrency > 1:
        print("  [node] ChatGPT 使用全局 Clash 出口，为避免注册中途换 IP，并发强制为 1")
        args.concurrency = 1

    print("=" * 50)
    print(f"  ChatGPT Auto Register  count={args.count} concurrency={args.concurrency}")
    print("=" * 50)

    sem = asyncio.Semaphore(args.concurrency)
    results = []

    async def run_one(i):
        async with sem:
            if i > 1:
                await asyncio.sleep(random.uniform(2, 6) * (i - 1))
            async with async_playwright() as p:
                try:
                    sk = await register_one(i, args.count, p)
                    results.append(sk)
                except Exception as e:
                    print(f"  #{i} fatal: {e}")
                    results.append(None)

    await asyncio.gather(*[run_one(i) for i in range(1, args.count + 1)])

    ok = sum(1 for r in results if r)
    print(f"\n{'='*50}\n  success: {ok}/{len(results)}\n{'='*50}")
    return 0 if results and ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
