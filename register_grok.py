# -*- coding: utf-8 -*-
"""
Grok (x.ai) 自动注册
关键: grok.com 有 Cloudflare 全页拦截，必须走 Clash 干净节点(换节点绕过)。

流程: 切Clash节点 -> BitBrowser走代理 -> grok.com -> 新規登録 -> accounts.x.ai
       -> メールで登録 -> 填邮箱 -> 邮件验证码(浏览器登录Outlook) -> 保存 cookie

界面是日文(节点地区导致)，按钮文本用 日文+英文 双匹配。

用法:
    python register_grok.py --count 1
    python register_grok.py --count 5 --node "美国 02"
"""

import argparse
import asyncio
import random
import string
import sys
import time

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, ".")
from playwright.async_api import async_playwright

import requests

from bitbrowser import BitBrowser
from common.browser import inject_stealth, create_browser_with_retry, human_type
from common.mailbox import get_code_outlook_pw, prelogin_outlook
from common.cookies import save_platform_cookies
from common import emails as email_pool
from common import proxy_switch

# 打码平台 key（解 Cloudflare Turnstile）。config 顶部会加载 .env，真实环境变量优先。
try:
    from config import CAPSOLVER_API_KEY, EZCAPTCHA_API_KEY, EZCAPTCHA_API_BASE
except Exception:
    CAPSOLVER_API_KEY = ""
    EZCAPTCHA_API_KEY = ""
    EZCAPTCHA_API_BASE = "https://api.ez-captcha.com"

PLATFORM = "grok"
GROK_URL = "https://grok.com/"
CLASH_PROXY_HOST = "127.0.0.1"
CLASH_PROXY_PORT = "7897"
# 登录态关键 cookie（运行时确认，先放候选）
KEY_COOKIES = ["sso", "sso-rw", "__Secure-next-auth.session-token", "auth_token"]
REGISTER_TIMEOUT = 600
KEEP_ON_FAIL = False
FIXED_EMAIL = None
FIXED_PASSWORD = None

# 注册方式按钮（中文+日文+英文，不同节点地区界面语言不同）
SIGNUP_BTN = ["新規登録", "注册", "註冊", "Sign up", "サインアップ", "注册账号"]
EMAIL_SIGNUP_BTN = ["メールで登録", "用邮箱注册", "使用邮箱注册", "邮箱注册", "用電子郵件註冊", "Sign up with email", "Continue with email", "メールアドレスで登録", "使用电子邮件"]
CONTINUE_BTN = ["続行", "继续", "繼續", "Continue", "次へ", "下一步", "Next", "Sign up", "登録", "注册", "Verify", "確認", "确认", "验证"]
COOKIE_DISMISS = ["すべて拒否する", "全部拒絕", "全部拒绝", "拒绝所有", "Reject all", "接受所有 Cookie", "Accept all", "すべて許可する", "全部允許", "拒否", "同意"]
# 提交验证码按钮
VERIFY_BTN = ["メールを確認", "確認", "确认", "验证邮件", "验证", "驗證", "Verify", "Verify email", "続行", "继续", "Continue", "Submit"]
# 完成注册按钮（x.ai 验证码后的 givenName/familyName/password/Turnstile 页）
COMPLETE_BTN = ["登録を完了", "アカウントを作成", "Complete registration", "Complete sign up",
                "Create account", "Sign up", "完成注册", "完成註冊", "完成", "完了", "Done",
                "登録", "サインアップ", "Continue", "続行", "继续", "Next", "次へ"]

GROK_SENDER = ("x.ai", "grok", "noreply", "no-reply")
GROK_SUBJECT = ("code", "verify", "verification", "grok", "x.ai", "confirm", "確認", "認証", "コード", "验证", "驗證")


# 在 turnstile 脚本加载前 hook window.turnstile.render，截获 React 传入的 callback + sitekey。
# 这是给 React 表单灌打码 token 的关键:x.ai 用 callback(token) 更新组件 state 来解禁"完成注册",
# 只改隐藏的 cf-turnstile-response 值 React 收不到。用属性 setter 拦截 window.turnstile 赋值,
# 一旦 CF 脚本设置它就立刻包裹 render,把 opts.callback/sitekey/action/cData 存到 window.__cf*。
TURNSTILE_HOOK_JS = r"""
(() => {
  if (window.__cfHookInstalled) return;
  window.__cfHookInstalled = true;
  window.__cfCb = [];
  const wrap = (v) => {
    if (v && typeof v.render === 'function' && !v.__hooked) {
      const orig = v.render.bind(v);
      v.render = (el, opts) => {
        try {
          if (opts) {
            if (opts.callback) window.__cfCb.push(opts.callback);
            window.__cfSitekey = opts.sitekey || window.__cfSitekey;
            window.__cfAction  = opts.action  || window.__cfAction;
            window.__cfCdata   = opts.cData || opts.cdata || window.__cfCdata;
          }
        } catch (e) {}
        return orig(el, opts);
      };
      v.__hooked = true;
    }
    return v;
  };
  let _ts = window.turnstile ? wrap(window.turnstile) : undefined;
  try {
    Object.defineProperty(window, 'turnstile', {
      configurable: true,
      get() { return _ts; },
      set(v) { _ts = wrap(v); },
    });
  } catch (e) {}
})();
"""


async def _turnstile_token(page):
    """读当前 cf-turnstile-response 的值(有值=已过)。"""
    try:
        return await page.evaluate(
            "() => { const e=document.querySelector('input[name=\"cf-turnstile-response\"],textarea[name=\"cf-turnstile-response\"]'); return e ? e.value : null; }"
        )
    except Exception:
        return None


async def _has_turnstile_widget(page):
    """页面上是否存在 Turnstile widget（cf-turnstile 容器 / data-sitekey / 隐藏响应框 /
    challenges.cloudflare.com iframe）。用于决定要不要走 ensure_turnstile，避免无墙时空等。"""
    try:
        return await page.evaluate(r"""() => !!(
            document.querySelector('.cf-turnstile,[data-sitekey],input[name="cf-turnstile-response"],textarea[name="cf-turnstile-response"]')
            || [...document.querySelectorAll('iframe')].some(f => (f.src || '').includes('challenges.cloudflare.com'))
        )""")
    except Exception:
        return False


async def _extract_sitekey(page):
    """提取 Turnstile sitekey:优先 hook 截获的 window.__cfSitekey,
    再退化到 [data-sitekey] 属性,最后从 challenges.cloudflare.com iframe 的 url 里抠 0x... 串。"""
    try:
        return await page.evaluate(r"""() => {
            if (window.__cfSitekey) return window.__cfSitekey;
            const el = document.querySelector('[data-sitekey]');
            if (el && el.getAttribute('data-sitekey')) return el.getAttribute('data-sitekey');
            for (const f of document.querySelectorAll('iframe')) {
                const src = f.src || '';
                if (src.includes('challenges.cloudflare.com')) {
                    const m = src.match(/(0x[0-9A-Za-z_-]{10,})/);
                    if (m) return m[1];
                }
            }
            return null;
        }""")
    except Exception:
        return None


def _solve_turnstile_capsolver(sitekey, page_url, action=None, cdata=None, max_wait=130):
    """CapSolver 解 Cloudflare Turnstile,返回 token 或 None。"""
    if not CAPSOLVER_API_KEY:
        return None
    try:
        task = {"type": "AntiTurnstileTaskProxyLess", "websiteURL": page_url, "websiteKey": sitekey}
        meta = {}
        if action:
            meta["action"] = action
        if cdata:
            meta["cdata"] = cdata
        if meta:
            task["metadata"] = meta
        resp = requests.post("https://api.capsolver.com/createTask",
                             json={"clientKey": CAPSOLVER_API_KEY, "task": task}, timeout=30)
        data = resp.json()
        if data.get("errorId", 1) != 0:
            print(f"  [capsolver] create error: {data.get('errorDescription', data)}")
            return None
        task_id = data["taskId"]
        print(f"  [capsolver] turnstile task: {task_id}")
        start = time.time()
        while time.time() - start < max_wait:
            time.sleep(5)
            r = requests.post("https://api.capsolver.com/getTaskResult",
                              json={"clientKey": CAPSOLVER_API_KEY, "taskId": task_id}, timeout=30).json()
            st = r.get("status")
            if st == "ready":
                tok = r.get("solution", {}).get("token")
                print(f"  [capsolver] solved (token len={len(tok or '')})")
                return tok
            if st == "failed" or r.get("errorId"):
                print(f"  [capsolver] failed: {r.get('errorDescription', '')}")
                return None
        print("  [capsolver] timeout")
        return None
    except Exception as e:
        print(f"  [capsolver] error: {str(e)[:80]}")
        return None


def _solve_turnstile_ezcaptcha(sitekey, page_url, max_wait=130):
    """EZ-Captcha 解 Turnstile(备用),返回 token 或 None。"""
    if not EZCAPTCHA_API_KEY:
        return None
    try:
        resp = requests.post(f"{EZCAPTCHA_API_BASE}/createTask", json={
            "clientKey": EZCAPTCHA_API_KEY,
            "task": {"type": "TurnstileTaskProxyless", "websiteURL": page_url, "websiteKey": sitekey},
        }, timeout=30)
        data = resp.json()
        if data.get("errorId", 1) != 0:
            print(f"  [ezcaptcha] create error: {data.get('errorDescription', data)}")
            return None
        task_id = data["taskId"]
        print(f"  [ezcaptcha] turnstile task: {task_id}")
        start = time.time()
        while time.time() - start < max_wait:
            time.sleep(5)
            r = requests.post(f"{EZCAPTCHA_API_BASE}/getTaskResult",
                              json={"clientKey": EZCAPTCHA_API_KEY, "taskId": task_id}, timeout=30).json()
            st = r.get("status")
            if st == "ready":
                tok = r.get("solution", {}).get("token")
                print(f"  [ezcaptcha] solved (token len={len(tok or '')})")
                return tok
            if st == "failed" or r.get("errorId"):
                print(f"  [ezcaptcha] failed: {r.get('errorDescription', '')}")
                return None
        print("  [ezcaptcha] timeout")
        return None
    except Exception as e:
        print(f"  [ezcaptcha] error: {str(e)[:80]}")
        return None


async def _inject_turnstile_token(page, token):
    """把打码拿到的 token 灌回页面:调 hook 截获的 callback(让 React 更新 state)+ 写隐藏字段。"""
    try:
        n = await page.evaluate(r"""(token) => {
            let n = 0;
            (window.__cfCb || []).forEach(cb => { try { cb(token); n++; } catch(e){} });
            document.querySelectorAll('input[name="cf-turnstile-response"],textarea[name="cf-turnstile-response"]').forEach(e => {
                try {
                    const proto = e.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                    const set = Object.getOwnPropertyDescriptor(proto, 'value').set;
                    set.call(e, token);
                    e.dispatchEvent(new Event('input', {bubbles: true}));
                    e.dispatchEvent(new Event('change', {bubbles: true}));
                } catch (err) {}
            });
            return n;
        }""", token)
        print(f"  [turnstile] token injected (callbacks fired={n})")
        return True
    except Exception as e:
        print(f"  [turnstile] inject error: {str(e)[:80]}")
        return False


async def ensure_turnstile(page, page_url, passive_s=18):
    """确保拿到 Turnstile token。先被动等 managed/交互式自动过;过不了再上打码平台解+回灌。
    返回是否最终拿到 token。"""
    # 1) 被动等:managed 模式自动过,或交互式复选框点一次
    if await _wait_turnstile(page, max_s=passive_s):
        return True
    # 2) 打码兜底
    if not (CAPSOLVER_API_KEY or EZCAPTCHA_API_KEY):
        print("  [turnstile] 无打码 key(CAPSOLVER_API_KEY/EZCAPTCHA_API_KEY),跳过自动解码")
        return False
    sitekey = await _extract_sitekey(page)
    if not sitekey:
        print("  [turnstile] 取不到 sitekey,无法打码")
        return False
    action = await page.evaluate("() => window.__cfAction || null")
    cdata = await page.evaluate("() => window.__cfCdata || null")
    print(f"  [turnstile] solving via captcha service (sitekey={sitekey[:18]}...)")
    loop = asyncio.get_event_loop()
    token = await loop.run_in_executor(None, _solve_turnstile_capsolver, sitekey, page_url, action, cdata)
    if not token:
        token = await loop.run_in_executor(None, _solve_turnstile_ezcaptcha, sitekey, page_url)
    if not token:
        print("  [turnstile] 打码失败")
        return False
    await _inject_turnstile_token(page, token)
    # 回灌后等表单 state 更新
    for _ in range(6):
        await asyncio.sleep(1)
        if await _turnstile_token(page):
            return True
    return True  # 已调 callback,即使隐藏字段读不到也认为已灌入,交由提交校验


def rand_password():
    return "Aa1!" + "".join(random.choices(string.ascii_letters + string.digits, k=12))


async def wait_render(page, max_s=70):
    """grok 走代理渲染慢(可达30-40s)，轮询到出现交互元素"""
    for i in range(max_s // 3):
        await asyncio.sleep(3)
        try:
            cnt = await page.evaluate("() => document.querySelectorAll('button,input,textarea,a').length")
        except Exception:
            cnt = 0
        if cnt > 3:
            print(f"  SPA rendered ~{i*3}s (interactive={cnt})")
            return True
    print("  SPA render timeout")
    return False


async def click_any(page, labels, timeout=5000):
    """点任一匹配文本的按钮/链接(日文+英文)"""
    for label in labels:
        try:
            b = page.locator(f'button:has-text("{label}"), a:has-text("{label}"), [role=button]:has-text("{label}")').first
            if await b.count() > 0:
                await b.click(timeout=timeout)
                return label
        except Exception:
            pass
    return None


async def _human_click_turnstile(page):
    """用真实鼠标轨迹点 Turnstile 复选框（模拟手动点击）。
    复选框在跨域 iframe 内，但 page.mouse 按视口坐标点击，所以拿 iframe/容器的 bounding box，
    移动到左侧复选框位置（约 left+28、垂直居中）再点，比合成 .click() 更像真人、更易过交互式挑战。"""
    try:
        loc = None
        for sel in ['.cf-turnstile', '[data-sitekey]', 'iframe[src*="challenges.cloudflare.com"]']:
            cand = page.locator(sel).first
            try:
                if await cand.count() > 0 and await cand.is_visible():
                    loc = cand
                    break
            except Exception:
                continue
        if loc is None:
            return False
        box = await loc.bounding_box()
        if not box or box["width"] < 10:
            return False
        # 复选框一般在左侧；垂直居中。带点随机抖动
        tx = box["x"] + min(30, box["width"] * 0.12) + random.uniform(-3, 3)
        ty = box["y"] + box["height"] / 2 + random.uniform(-3, 3)
        # 人类轨迹：先到附近 → 分步靠近 → 停顿 → 按下/抬起
        await page.mouse.move(box["x"] - 40 + random.uniform(0, 20),
                              ty - 25 + random.uniform(0, 15), steps=8)
        await asyncio.sleep(random.uniform(0.2, 0.5))
        await page.mouse.move(tx, ty, steps=random.randint(12, 25))
        await asyncio.sleep(random.uniform(0.15, 0.4))
        await page.mouse.down()
        await asyncio.sleep(random.uniform(0.05, 0.13))
        await page.mouse.up()
        print(f"  [turnstile] human-click @ ({int(tx)},{int(ty)})")
        return True
    except Exception as e:
        print(f"  [turnstile] human-click err: {str(e)[:60]}")
        return False


async def _on_page_challenge(page):
    """grok.com 是否卡在 Cloudflare 页面级挑战（Just a moment / __cf_chl 重定向）。"""
    try:
        if "__cf_chl" in (page.url or ""):
            return True
        has_cf = await page.evaluate(
            "() => !!document.querySelector('iframe[src*=\"challenges.cloudflare.com\"]')")
        n = await page.evaluate("() => document.querySelectorAll('button,a,textarea').length")
        body = ""
        try:
            body = (await page.locator("body").inner_text())[:200].lower()
        except Exception:
            pass
        markers = ("just a moment", "verifying", "checking your browser", "请稍候", "正在验证")
        if (has_cf or any(m in body for m in markers)) and n < 3:
            return True
    except Exception:
        pass
    return False


async def pass_page_challenge(page, tries=3):
    """过 grok.com 页面级 CF 挑战：用真实鼠标点交互式 Turnstile（浏览器自身=节点IP 出 clearance），
    等页面重定向回正常 SPA。页面级挑战的 cf_clearance 必须由真实浏览器在节点 IP 上拿，打码代解
    的 proxyless token 对它无效，所以这里只靠模拟手动点击 + 等待 + 刷新。"""
    for t in range(tries):
        if not await _on_page_challenge(page):
            return True
        print(f"  [page-cf] challenge detected, human-click try {t+1}/{tries}")
        await _human_click_turnstile(page)
        for _ in range(12):  # 等离开挑战页 ~24s
            await asyncio.sleep(2)
            if not await _on_page_challenge(page):
                print("  [page-cf] cleared")
                await asyncio.sleep(2)
                return True
        try:
            await page.reload(timeout=40000, wait_until="domcontentloaded")
        except Exception:
            pass
        await asyncio.sleep(4)
    return not await _on_page_challenge(page)


async def _wait_turnstile(page, max_s=90, human_click=True):
    """等 Cloudflare Turnstile token：hidden input[name=cf-turnstile-response] 有值即过。
    返回是否拿到 token。

    x.ai 多为**托管(managed)模式**：能自动过就让它过；过不了/出现交互式挑战时，**用真实鼠标
    轨迹点一次复选框**（模拟手动点击，见 `_human_click_turnstile`）。token 能否拿到强依赖出口
    IP 信誉——数据中心节点常被判定需挑战；点击过不了的，再由上层 `ensure_turnstile` 走打码兜底。"""
    clicked = False
    deadline = time.time() + max_s
    while time.time() < deadline:
        try:
            val = await page.evaluate(
                "() => { const e=document.querySelector('input[name=\"cf-turnstile-response\"],textarea[name=\"cf-turnstile-response\"]'); return e ? e.value : null; }"
            )
        except Exception:
            val = None
        if val:
            print(f"  [turnstile] passed (token len={len(val)})")
            return True
        # 先给 managed 模式一点自动过的时间(~4s)，仍没过再模拟手动点一次
        if human_click and not clicked and time.time() - (deadline - max_s) > 4:
            if await _human_click_turnstile(page):
                clicked = True
        await asyncio.sleep(2)
    print("  [turnstile] token NOT obtained (IP 可能被 CF 判定需挑战；走打码或换节点)")
    return False


async def dump_state(page, tag=""):
    try:
        info = await page.evaluate("""() => ({
            btns:[...document.querySelectorAll('button')].map(b=>b.innerText.trim()).filter(t=>t).slice(0,15),
            inputs:[...document.querySelectorAll('input,textarea')].map(i=>i.type+'/'+(i.placeholder||i.name||'')),
            url:location.href
        })""")
        print(f"  --- state {tag} ---")
        print(f"    url: {info['url']}")
        print(f"    btns: {info['btns']}")
        print(f"    inputs: {info['inputs']}")
    except Exception as e:
        print(f"  dump_state err: {e}")


async def prelogin_via_direct_browser(email, email_pw, p):
    """在【提交邮箱发码之前】预登录 Outlook：单开 noproxy 窗口登录+过隐私协议+进收件箱。
    返回 (bb, pid, page) 句柄给后续 skip_login 轮询复用；失败返回 (None, None, None)。
    broker 模式不预登录（broker 自管），返回 (None, None, None)。"""
    import os
    if os.environ.get("MAILBOX_BROKER"):
        return None, None, None
    bb = BitBrowser()
    pid = None
    try:
        pid = create_browser_with_retry(bb, f"mail_{time.strftime('%H%M%S')}")
        if not pid:
            return None, None, None
        bb._post("/browser/update", {
            "id": pid, "proxyMethod": 2, "proxyType": "noproxy",
            "browserFingerPrint": {"coreVersion": "130"},
        })
        data = None
        for _ in range(8):
            try:
                data = bb.open_browser(pid)
                break
            except Exception:
                await asyncio.sleep(4)
        if not data:
            return None, None, None
        browser = await p.chromium.connect_over_cdp(data["ws"])
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await inject_stealth(ctx, page)
        ok = await prelogin_outlook(page, email, email_pw)
        if ok:
            return bb, pid, page
    except Exception as e:
        print(f"  [mail] prelogin error: {e}")
    # 失败：清理窗口
    if pid:
        try:
            bb.close_browser(pid)
        except Exception:
            pass
        await asyncio.sleep(1)
        try:
            bb.delete_browser(pid)
        except Exception:
            pass
    return None, None, None


async def get_code_via_direct_browser(email, email_pw, p, pre=None):
    """单开一个 noproxy BitBrowser 窗口(本机直连)登录 Outlook 取验证码。
    注册浏览器走代理过 Grok CF，但 Outlook 界面走代理刷不出，故取信用直连。
    pre=(bb,pid,page)：复用 prelogin_via_direct_browser 预登录好的窗口，skip_login 直接轮询。"""
    import os
    if os.environ.get("MAILBOX_BROKER"):
        # broker 模式：委托共享取码服务，不另开浏览器（Grok 用 outlook 注定超时，timeout 调短减少拖累）
        from common.mailbox import fetch_from_broker
        return await fetch_from_broker(
            email, email_pw, GROK_SENDER, GROK_SUBJECT,
            r"\b((?=[A-Z0-9-]*[A-Z])[A-Z0-9]{2,4}-[A-Z0-9]{2,4})\b", "code",
            int(os.environ.get("GROK_BROKER_TIMEOUT", "40")),
        )
    # 复用预登录窗口：已在收件箱，skip_login 直接轮询
    if pre and pre[2] is not None:
        bb, pid, page = pre
        try:
            return await get_code_outlook_pw(
                page, email, email_pw,
                sender_hint=GROK_SENDER, subject_hint=GROK_SUBJECT,
                code_regex=r"\b((?=[A-Z0-9-]*[A-Z])[A-Z0-9]{2,4}-[A-Z0-9]{2,4})\b",
                max_wait=160, poll=8, skip_login=True,
            )
        except Exception as e:
            print(f"  [mail] reuse prelogin error: {e}")
            return None
        finally:
            if pid:
                try:
                    bb.close_browser(pid)
                except Exception:
                    pass
                await asyncio.sleep(2)
                try:
                    bb.delete_browser(pid)
                except Exception:
                    pass
    bb = BitBrowser()
    pid = None
    try:
        pid = create_browser_with_retry(bb, f"mail_{time.strftime('%H%M%S')}")
        if not pid:
            return None
        bb._post("/browser/update", {
            "id": pid, "proxyMethod": 2, "proxyType": "noproxy",
            "browserFingerPrint": {"coreVersion": "130"},
        })
        data = None
        for _ in range(8):
            try:
                data = bb.open_browser(pid)
                break
            except Exception:
                await asyncio.sleep(4)
        if not data:
            return None
        browser = await p.chromium.connect_over_cdp(data["ws"])
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await inject_stealth(ctx, page)
        return await get_code_outlook_pw(
            page, email, email_pw,
            sender_hint=GROK_SENDER, subject_hint=GROK_SUBJECT,
            code_regex=r"\b((?=[A-Z0-9-]*[A-Z])[A-Z0-9]{2,4}-[A-Z0-9]{2,4})\b", max_wait=160, poll=8,
        )
    except Exception as e:
        print(f"  [mail] direct browser error: {e}")
        return None
    finally:
        if pid:
            try:
                bb.close_browser(pid)
            except Exception:
                pass
            await asyncio.sleep(2)
            try:
                bb.delete_browser(pid)
            except Exception:
                pass


async def register_one(index, total, p, node):
    start = time.time()

    def check_timeout():
        if time.time() - start > REGISTER_TIMEOUT:
            raise TimeoutError(f"timeout {REGISTER_TIMEOUT}s")

    if FIXED_EMAIL:
        email, email_pw, refresh_token, client_id = FIXED_EMAIL, FIXED_PASSWORD, "", ""
    else:
        em = email_pool.next_email(PLATFORM)
        if not em:
            print("  no email available")
            return None
        email, email_pw, refresh_token, client_id = em
    password = rand_password()
    print(f"\n#{index}/{total} email={email}")

    name = f"grok_{time.strftime('%m%d_%H%M%S')}_{index}"
    bb = BitBrowser()
    pid = None
    success = False
    try:
        # BitBrowser 走 Clash 代理
        pid = create_browser_with_retry(
            bb, name,
        )
        if not pid:
            print("  create browser failed")
            return None
        # 重新用代理配置更新窗口
        bb._post("/browser/update", {
            "id": pid, "name": name, "proxyMethod": 2, "proxyType": "http",
            "host": CLASH_PROXY_HOST, "port": CLASH_PROXY_PORT,
            "browserFingerPrint": {"coreVersion": "130"},
        })
        data = None
        for _ in range(8):
            try:
                data = bb.open_browser(pid)
                break
            except Exception:
                await asyncio.sleep(4)
        if not data:
            print("  open browser failed")
            return None

        browser = await p.chromium.connect_over_cdp(data["ws"])
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await inject_stealth(ctx, page)
        # 在任何页面脚本前 hook turnstile.render，截获 callback/sitekey（供打码回灌用）
        try:
            await ctx.add_init_script(TURNSTILE_HOOK_JS)
        except Exception as e:
            print(f"  turnstile hook inject failed: {str(e)[:60]}")

        # Step 1: 打开 grok.com，等渲染
        print("  [1] goto grok.com (via proxy node)")
        for attempt in range(3):
            try:
                await page.goto(GROK_URL, timeout=60000, wait_until="domcontentloaded")
                break
            except Exception as e:
                print(f"  goto retry {attempt+1}: {str(e)[:50]}")
                await asyncio.sleep(4)
        await wait_render(page)
        # 页面级 CF 挑战（Just a moment/__cf_chl）：模拟手动点击过墙后再等渲染
        if await _on_page_challenge(page):
            await pass_page_challenge(page)
            await wait_render(page, max_s=40)
        check_timeout()

        # 关 cookie 弹窗
        await asyncio.sleep(2)
        dismissed = await click_any(page, COOKIE_DISMISS, timeout=3000)
        if dismissed:
            print(f"  cookie banner dismissed: {dismissed}")
            await asyncio.sleep(2)

        # Step 2: 点 新規登録
        print("  [2] click signup")
        clicked = await click_any(page, SIGNUP_BTN, timeout=6000)
        if not clicked:
            print("  signup button not found")
            await dump_state(page, "no-signup")
            email_pool.mark_error(PLATFORM, email, email_pw, "no_signup_btn")
            return None
        await asyncio.sleep(6)  # 跳 accounts.x.ai
        await wait_render(page, max_s=40)
        await dump_state(page, "after-signup")
        check_timeout()

        # Step 3: 选 メールで登録 (email signup)
        print("  [3] choose email signup")
        clicked = await click_any(page, EMAIL_SIGNUP_BTN, timeout=6000)
        if clicked:
            print(f"  clicked: {clicked}")

        # 等邮箱输入框出现：grok 经代理 SPA 渲染慢(可达30-40s)，点完不能立即判定。
        # 坑1：OneTrust Cookie 横幅会遮挡并拦截 '用邮箱注册' 点击，导致页面不跳转——每轮先关横幅。
        # 坑2：横幅里有个隐藏搜索框 input#vendor-search-handler(placeholder=搜索...)，会污染
        #      input[type=text] 兜底选择器、点它直接 30s 超时——故排除它并只取可见的输入框。
        email_sel = ('input[type="email"], input[name="email"], input[autocomplete="email"], '
                     'input[type="text"]:not([name="vendor-search-handler"])'
                     ':not([placeholder*="搜索"]):not([placeholder*="検索"]):not([placeholder*="search" i])'
                     ':not([aria-label*="Cookie"]):not([aria-label*="搜索"])')

        async def _visible_email():
            loc = page.locator(email_sel)
            for j in range(await loc.count()):
                el = loc.nth(j)
                try:
                    if await el.is_visible():
                        return el
                except Exception:
                    pass
            return None

        email_input = None
        for i in range(16):  # ~50s
            await click_any(page, COOKIE_DISMISS, timeout=2000)  # 关 Cookie 横幅（拦截点击）
            email_input = await _visible_email()
            if email_input:
                break
            await asyncio.sleep(3)
            if i in (4, 9):  # 横幅关掉后补点邮箱注册（首次点击可能被横幅吃掉）
                again = await click_any(page, EMAIL_SIGNUP_BTN, timeout=4000)
                if again:
                    print(f"  re-clicked: {again}")
        await dump_state(page, "email-method")

        # Step 4: 填邮箱
        print("  [4] fill email")
        pre_mail = None   # 预登录的 Outlook 窗口句柄 (bb,pid,page)
        if email_input:
            await email_input.click()
            await email_input.fill(email)
            await asyncio.sleep(1)
            # 关键：在提交邮箱（触发 x.ai 发码）【之前】先预登录 Outlook、过隐私协议、进收件箱，
            # 这样发码后立刻能扫到，避免"发码后才登录、登录耗时错过码"（grok 收不到码的根因）。
            try:
                print("  [4] pre-login Outlook (noproxy) before sending code...")
                pre_mail = await prelogin_via_direct_browser(email, email_pw, p)
                print(f"  [4] outlook prelogin: {'ready' if pre_mail and pre_mail[2] else 'failed'}")
            except Exception as e:
                print(f"  [4] prelogin error: {str(e)[:60]}")
            # accounts.x.ai 邮箱提交这一步常带 Turnstile，**不过墙 x.ai 就不发码**
            # （表现为"邮件根本没到"）。检测到 widget 就先过墙，再点 Continue。
            if await _has_turnstile_widget(page):
                print("  [4] 邮箱步检测到 Turnstile，先过墙再提交")
                await ensure_turnstile(page, page.url, passive_s=14)
            await click_any(page, CONTINUE_BTN, timeout=5000)
            await asyncio.sleep(5)
            # 提交后若仍停在邮箱页且 Turnstile 还在（有的布局点击后才弹/token 过期），
            # 再过一次墙并重点 Continue，确保把发码请求打出去。
            for _ in range(2):
                still_email = await _visible_email() is not None
                if not still_email:
                    break
                if await _has_turnstile_widget(page):
                    print("  [4] 仍在邮箱页，重试过墙 + 提交")
                    await ensure_turnstile(page, page.url, passive_s=10)
                    await click_any(page, CONTINUE_BTN, timeout=4000)
                    await asyncio.sleep(5)
                else:
                    break
        else:
            print("  email input not found")
            await dump_state(page, "no-email-input")
            email_pool.mark_error(PLATFORM, email, email_pw, "no_email_input")
            return None
        await dump_state(page, "after-email")
        check_timeout()

        # Step 5: 邮件验证码
        # 关键架构：注册浏览器走代理(过Grok CF)，但 Outlook 界面走代理刷不出来，
        # 所以取信单开一个 noproxy 的 BitBrowser 窗口(本机直连)读邮件。
        print("  [5] get verification code via separate noproxy Outlook window")
        code = await get_code_via_direct_browser(email, email_pw, p, pre=pre_mail)

        if code:
            print(f"  got code: {code}")
            # 精确定位验证码框(name=code)，避免误填到搜索框(text/検索)
            ci = page.locator('input[name="code"]').first
            if await ci.count() == 0:
                ci = page.locator('input[inputmode="numeric"], input[autocomplete="one-time-code"]').first
            if await ci.count() == 0:
                # 兜底：排除搜索框的 text input
                ci = page.locator('input[type="text"]:not([placeholder*="検索"]):not([name="vendor-search-handler"])').first
            if await ci.count() > 0:
                await ci.click()
                await ci.fill("")
                # 逐字符输入触发 React onChange（fill 直接 setValue 不触发，x.ai 表单识别不到）
                await ci.type(code, delay=120)
                await asyncio.sleep(1.5)
                # 先试回车提交（验证码框常回车即提交），再点确认按钮
                try:
                    await ci.press("Enter")
                    await asyncio.sleep(2)
                except Exception:
                    pass
                submitted = await click_any(page, VERIFY_BTN, timeout=5000)
                print(f"  提交验证码按钮: {submitted}")
                await asyncio.sleep(6)
            await dump_state(page, "after-code")
        else:
            print("  no code received")
            email_pool.mark_error(PLATFORM, email, email_pw, "no_code")

        # Step 6: 完成注册页（x.ai 新流程：givenName/familyName + password + Cloudflare Turnstile + 登録を完了）
        def _rand_word():
            return random.choice("BCDFGHJKLMNPQRST") + "".join(random.choices("aeiou", k=1)) \
                   + "".join(random.choices(string.ascii_lowercase, k=random.randint(3, 6)))

        gname = page.locator('input[name="givenName"]').first
        fname = page.locator('input[name="familyName"]').first
        if await gname.count() > 0:
            first, last = _rand_word().capitalize(), _rand_word().capitalize()
            try:
                await gname.click(); await gname.type(first, delay=60)
                if await fname.count() > 0:
                    await fname.click(); await fname.type(last, delay=60)
                print(f"  [6] name: {first} {last}")
            except Exception as e:
                print(f"  [6] name fill err: {str(e)[:50]}")

        pw_input = page.locator('input[type="password"]').first
        if await pw_input.count() > 0:
            print("  [6] set password")
            try:
                await pw_input.click(); await pw_input.type(password, delay=50)
            except Exception:
                await pw_input.fill(password)
            await asyncio.sleep(1)

        # 等 Turnstile token + 点完成注册：拿到 token 才点（空 token 提交必被拦在原页）。
        # ensure_turnstile = 被动等(managed自动过) → 模拟手动点复选框 → 打码平台解+回灌。
        # 最多 3 轮：仍停在 accounts.x.ai/sign-up 说明没过，重等再点。
        page_url = page.url
        completed = False
        for attempt in range(3):
            has_token = await ensure_turnstile(page, page_url, passive_s=18)
            done = await click_any(page, COMPLETE_BTN, timeout=8000)
            print(f"  [6] complete: btn={done} turnstile={has_token} (attempt {attempt+1}/3)")
            await asyncio.sleep(6)
            cur = page.url
            # 离开 sign-up 页 = 注册推进成功
            if "/sign-up" not in cur:
                completed = True
                break
            await dump_state(page, f"after-complete-{attempt+1}")
            check_timeout()
        if not completed:
            print("  [6] 仍停在 sign-up 页（Turnstile 未过 / 提交被拦）")
        check_timeout()

        # 回到 grok.com 确保 cookie 落到主域
        try:
            await page.goto("https://grok.com/", timeout=45000, wait_until="domcontentloaded")
            await wait_render(page, max_s=40)
        except Exception:
            pass
        await dump_state(page, "final")

        key_val, _ = await save_platform_cookies(
            ctx, PLATFORM, pid, email=email, password=password, key_cookie_names=KEY_COOKIES
        )
        if key_val:
            # 导出标准 sso token（给 webchat2api/grok2api 用），失败不影响成功判定
            try:
                from common.session_export import save_grok_token
                save_grok_token(key_val, email)
                print("  [OK] grok sso token 已保存")
            except Exception as e:
                print(f"  [WARN] 保存 grok token 失败: {e}")
            email_pool.mark_used(PLATFORM, email, email_pw)
            success = True
            print("  [OK] session cookie saved")
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
        if pid:
            keep = KEEP_ON_FAIL and not success
            try:
                bb.close_browser(pid)
            except Exception:
                pass
            await asyncio.sleep(2)
            if not keep:
                try:
                    bb.delete_browser(pid)
                except Exception:
                    pass
            else:
                print(f"  [debug] window kept: {name} (id={pid})")


async def main():
    parser = argparse.ArgumentParser(description="Grok Auto Register")
    parser.add_argument("--count", "-n", type=int, default=1)
    parser.add_argument("--concurrency", "-c", type=int, default=1)
    parser.add_argument("--timeout", "-t", type=int, default=600)
    parser.add_argument("--node", default="auto", help="Clash 出口节点(过grok CF)")
    parser.add_argument("--keep-on-fail", action="store_true")
    parser.add_argument("--email", default=None, help="指定邮箱(绕过邮箱池)")
    parser.add_argument("--password", default=None, help="指定邮箱密码")
    args = parser.parse_args()

    global REGISTER_TIMEOUT, KEEP_ON_FAIL, FIXED_EMAIL, FIXED_PASSWORD
    REGISTER_TIMEOUT = args.timeout
    KEEP_ON_FAIL = args.keep_on_fail
    FIXED_EMAIL = args.email
    FIXED_PASSWORD = args.password

    print("=" * 50)
    print(f"  Grok Auto Register  count={args.count} node={args.node}")
    print("=" * 50)

    # 选节点过 grok CF：--node 指定则用它，否则自动探测能过的节点
    try:
        if args.node and args.node.lower() != "auto":
            proxy_switch.set_node(args.node)
            time.sleep(2)
            print(f"  使用指定节点 -> {proxy_switch.current_node()}")
        else:
            print("  自动探测能过 grok CF 的节点...")
            node = proxy_switch.find_working_node(test_url="https://grok.com/")
            if not node:
                print("  没找到能过 grok CF 的节点(可能 CF 高防护时段，稍后重试)")
                return
            print(f"  选用节点: {node}")
    except Exception as e:
        print(f"  切节点失败(确认 Clash 在跑): {e}")
        return

    sem = asyncio.Semaphore(args.concurrency)
    results = []

    async def run_one(i):
        async with sem:
            if i > 1:
                await asyncio.sleep(random.uniform(3, 8) * (i - 1))
            async with async_playwright() as p:
                try:
                    sk = await register_one(i, args.count, p, args.node)
                    results.append(sk)
                except Exception as e:
                    print(f"  #{i} fatal: {e}")
                    results.append(None)

    await asyncio.gather(*[run_one(i) for i in range(1, args.count + 1)])

    ok = sum(1 for r in results if r)
    print(f"\n{'='*50}\n  success: {ok}/{len(results)}\n{'='*50}")


if __name__ == "__main__":
    asyncio.run(main())
