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
import random
import string
import sys
import time

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


# cookie 同意横幅按钮（中/英/日），弹出时不关会挡住邮箱输入
_COOKIE_BTNS = [
    "すべて受け入れる", "必須項目以外を拒否する",          # 日
    "Accept all", "Reject all", "Reject non-essential", "Accept", "Got it",  # 英
    "全部接受", "接受所有", "拒绝所有", "拒绝非必要", "同意", "知道了",          # 中
]


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
        bb, pid, browser, ctx, page = await open_and_connect(name=name, p=p)
        await ctx.clear_cookies()

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
        if email_pw:
            try:
                print("  [2.5] pre-login Outlook (独立窗口) before sending code...")
                mail_bb, mail_pid, _mb, _mctx, mail_page = await open_and_connect(
                    name=f"mail_{time.strftime('%m%d_%H%M%S')}_{index}", p=p)
                prelogged = await prelogin_outlook(mail_page, email, email_pw)
                print(f"  [2.5] outlook prelogin: {'ready' if prelogged else 'failed'}")
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
        code_input = page.locator('input[inputmode="numeric"], input[name="code"], input[autocomplete="one-time-code"], input[type="text"]')
        if await code_input.count() > 0 or "verify" in page.url.lower() or "check" in (await page.locator("body").inner_text()).lower():
            print("  [4] waiting for email verification code...")
            # 先试 Graph API(token)；token 多已过期，失败则浏览器登录 Outlook 取信
            code = await asyncio.get_event_loop().run_in_executor(
                None, get_code_by_token, email, refresh_token, client_id or None,
                OAI_SENDER, OAI_SUBJECT, r"\b(\d{6})\b", 40, 5
            )
            if not code and email_pw:
                # 复用 Step 2.5 预登录好的独立窗口（已在收件箱），skip_login 直接轮询取码；
                # 预登录失败/没开成则另开独立窗口走完整登录兜底（绝不用注册 ctx，避免干扰）。
                reuse = prelogged and mail_page is not None
                if reuse:
                    print("  [4] token failed, polling pre-logged Outlook inbox...")
                else:
                    print("  [4] token failed, opening Outlook window to get code...")
                    try:
                        mail_bb, mail_pid, _mb, _mctx, mail_page = await open_and_connect(
                            name=f"mail_{time.strftime('%m%d_%H%M%S')}_{index}", p=p)
                    except Exception as e:
                        print(f"  [4] open mail window failed: {str(e)[:60]}")
                        mail_page = None
                if mail_page is not None:
                    try:
                        code = await get_code_outlook_pw(
                            mail_page, email, email_pw,
                            sender_hint=("openai", "noreply", "no-reply"),
                            subject_hint=("code", "verify", "openai", "chatgpt", "验证"),
                            code_regex=r"\b(\d{6})\b", max_wait=150, poll=8,
                            skip_login=reuse,
                        )
                    finally:
                        # 关掉独立取码窗口（teardown 删 BitBrowser profile）
                        if mail_bb and mail_pid:
                            try:
                                await teardown(mail_bb, mail_pid, delete=True)
                            except Exception:
                                pass
                        mail_bb = mail_pid = mail_page = None
                # 切回注册标签
                await page.bring_to_front()
            if code:
                print(f"  got code: {code}")
                await dismiss_cookie_banner(page)
                code_sel = 'input[inputmode="numeric"], input[name="code"], input[autocomplete="one-time-code"], input[type="text"]'
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
        # 兜底：关掉可能残留的预登录邮箱独立窗口（如 token 路径直接拿到码、或没进验证码分支）
        if mail_bb and mail_pid:
            try:
                await teardown(mail_bb, mail_pid, delete=True)
            except Exception:
                pass
            mail_bb = mail_pid = mail_page = None
        check_timeout()

        # Step 5: onboarding（名字/生日）
        await handle_onboarding(page, index)
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


async def click_finish_button(page, index, age_sel, max_wait=12):
    """about-you 页专用：等 'Finish creating account' 按钮从 disabled 变可用后点击。
    返回是否点击成功。先尝试文案精确匹配，再退化为唯一非第三方登录按钮；
    若超时仍 disabled，dump 诊断（按钮 outerHTML + 各字段值 + 截图）便于排查。"""
    finish_labels = [
        "Finish creating account", "アカウントの作成を完了する",
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
                    await btn.click(timeout=6000)
                    print("  [onboarding] clicked Finish button")
                    await asyncio.sleep(3)
                    return True
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


async def handle_onboarding(page, index, max_rounds=6):
    """处理注册后的引导页：名字、生日/年龄、各种 Continue/Agree"""
    name_done = False  # about-you 名字只填一次，避免每轮重置成新随机名
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
        if on_about_you:
            if not name_done and await page.locator(name_sel).count() > 0:
                first, last = rand_name()
                # delay/settle 调低：名字/年龄是 onboarding 的本地字段，不像邮箱要防风控，快点键入即可
                if await react_fill(page, name_sel, f"{first} {last}", tries=2, delay=12, settle=0.15, verbose=False):
                    print(f"  [onboarding] name: {first} {last}")
                    name_done = True
                    await blur_field(page, name_sel)
                    await asyncio.sleep(0.2)
            if await react_fill(page, age_sel, str(random.randint(18, 40)), tries=2, delay=12, settle=0.15, verbose=False):
                print("  [onboarding] age filled")
                # 关键：失焦让 onBlur 校验跑起来，Finish 按钮才会解除 disabled
                await blur_field(page, age_sel)
                await asyncio.sleep(0.3)
            if await click_finish_button(page, index, age_sel):
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
    parser.add_argument("--keep-on-fail", action="store_true", help="失败时保留窗口便于排查")
    parser.add_argument("--email", default=None, help="指定邮箱(绕过邮箱池)")
    parser.add_argument("--password", default=None, help="指定邮箱密码")
    parser.add_argument("--refresh-token", default=None, help="指定 Outlook refresh_token")
    parser.add_argument("--client-id", default=None, help="指定 Outlook OAuth client_id")
    parser.add_argument("--import-c2a", action="store_true",
                        help="注册成功后即时把 token 导入 chatgpt2api (POST <host>/api/accounts)")
    parser.add_argument("--c2a-url", default=None, help="chatgpt2api host (默认取 config.CHATGPT2API_URL)")
    parser.add_argument("--c2a-key", default=None, help="chatgpt2api admin key (默认取 config.CHATGPT2API_KEY)")
    args = parser.parse_args()

    global REGISTER_TIMEOUT, KEEP_ON_FAIL, FIXED_EMAIL, FIXED_PASSWORD, FIXED_REFRESH_TOKEN, FIXED_CLIENT_ID
    global IMPORT_C2A, C2A_URL, C2A_KEY
    REGISTER_TIMEOUT = args.timeout
    KEEP_ON_FAIL = args.keep_on_fail
    FIXED_EMAIL = args.email
    FIXED_PASSWORD = args.password
    FIXED_REFRESH_TOKEN = args.refresh_token
    FIXED_CLIENT_ID = args.client_id
    IMPORT_C2A = args.import_c2a
    C2A_URL = args.c2a_url
    C2A_KEY = args.c2a_key

    if IMPORT_C2A and not ((C2A_URL or CHATGPT2API_URL) and (C2A_KEY or CHATGPT2API_KEY)):
        print("  [c2a][WARN] 已开 --import-c2a 但未配置 CHATGPT2API_URL/KEY（--c2a-url/--c2a-key 或 .env），导入会被跳过")

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


if __name__ == "__main__":
    asyncio.run(main())
