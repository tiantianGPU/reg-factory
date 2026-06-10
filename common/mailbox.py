# -*- coding: utf-8 -*-
"""
common/mailbox.py — 通用 Outlook 取信（Graph API + 浏览器兜底，均扫 inbox + junk）

支持两种提取目标：
  - magic link（Claude 用）
  - 验证码 code（ChatGPT / Grok 用 6 位数字）

emails.txt 行格式: email----password----refresh_token----client_id
"""

import os
import re
import sys
import time
import asyncio

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

import requests

DEFAULT_CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
GRAPH_FOLDERS = ["inbox", "junkemail"]


def _get_access_token(refresh_token, client_id=DEFAULT_CLIENT_ID, scope="https://graph.microsoft.com/Mail.Read"):
    try:
        resp = requests.post(
            "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
            data={
                "client_id": client_id or DEFAULT_CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": scope,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"  [mail] token refresh failed: {resp.status_code} {resp.text[:120]}")
            return None
        return resp.json().get("access_token")
    except Exception as e:
        print(f"  [mail] token error: {e}")
        return None


def fetch_messages(access_token, folder, top=10):
    """拉取某文件夹最新邮件，返回 [{subject, from, body, received}]"""
    headers = {"Authorization": f"Bearer {access_token}"}
    url = (
        f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder}/messages"
        f"?$top={top}&$orderby=receivedDateTime desc"
        f"&$select=subject,from,body,bodyPreview,receivedDateTime"
    )
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            return []
        out = []
        for m in r.json().get("value", []):
            out.append({
                "subject": m.get("subject", ""),
                "from": (m.get("from", {}).get("emailAddress", {}) or {}).get("address", ""),
                "body": (m.get("body", {}) or {}).get("content", "") or m.get("bodyPreview", ""),
                "received": m.get("receivedDateTime", ""),
            })
        return out
    except Exception as e:
        print(f"  [mail] fetch {folder} error: {e}")
        return []


def get_code_by_token(
    email,
    refresh_token,
    client_id=DEFAULT_CLIENT_ID,
    sender_contains=("openai.com", "anthropic", "x.ai", "grok"),
    subject_contains=("code", "verify", "verification", "confirm", "登录", "验证"),
    code_regex=r"\b(\d{6})\b",
    max_wait=120,
    poll=5,
):
    """轮询 inbox+junk，匹配发件人/主题后用正则提取验证码。返回 code 字符串或 None。
    sender_contains / subject_contains 任一命中即视为目标邮件（宽松匹配，二者满足其一）。"""
    token = _get_access_token(refresh_token, client_id)
    if not token:
        return None

    pat = re.compile(code_regex)
    start = time.time()
    while time.time() - start < max_wait:
        for folder in GRAPH_FOLDERS:
            for m in fetch_messages(token, folder, top=10):
                subj = (m["subject"] or "").lower()
                frm = (m["from"] or "").lower()
                hit_sender = any(s.lower() in frm for s in sender_contains) if sender_contains else False
                hit_subject = any(s.lower() in subj for s in subject_contains) if subject_contains else False
                if not (hit_sender or hit_subject):
                    continue
                # 优先在主题里找验证码（很多服务把 code 放主题），再到正文
                for text in (m["subject"] or "", m["body"] or ""):
                    mm = pat.search(text)
                    if mm:
                        code = _first_group(mm)
                        print(f"  [mail] code found in {folder} (from={m['from']}): {code}")
                        return code
        elapsed = int(time.time() - start)
        print(f"  [mail] waiting for code (inbox+junk)... ({elapsed}s/{max_wait}s)")
        # token 可能在长轮询中过期，过半时刷新一次
        if elapsed > max_wait // 2 and elapsed % (poll * 4) < poll:
            nt = _get_access_token(refresh_token, client_id)
            if nt:
                token = nt
        time.sleep(poll)

    print("  [mail] timeout, no code found")
    return None


def get_link_by_token(
    email,
    refresh_token,
    client_id=DEFAULT_CLIENT_ID,
    link_regex=r"https://[^\s\"'<>]+",
    sender_contains=("openai.com", "anthropic", "x.ai", "grok"),
    subject_contains=("verify", "confirm", "sign in", "magic", "login", "登录", "验证"),
    must_contain=None,
    max_wait=120,
    poll=5,
):
    """轮询 inbox+junk，匹配邮件后提取链接（可用 must_contain 过滤目标链接，如 'verify_email'）。"""
    token = _get_access_token(refresh_token, client_id)
    if not token:
        return None
    pat = re.compile(link_regex)
    start = time.time()
    while time.time() - start < max_wait:
        for folder in GRAPH_FOLDERS:
            for m in fetch_messages(token, folder, top=10):
                subj = (m["subject"] or "").lower()
                frm = (m["from"] or "").lower()
                hit = (any(s.lower() in frm for s in sender_contains) if sender_contains else False) or \
                      (any(s.lower() in subj for s in subject_contains) if subject_contains else False)
                if not hit:
                    continue
                for link in pat.findall(m["body"] or ""):
                    if must_contain and must_contain not in link:
                        continue
                    print(f"  [mail] link found in {folder}: {link[:80]}...")
                    return link
        elapsed = int(time.time() - start)
        print(f"  [mail] waiting for link (inbox+junk)... ({elapsed}s/{max_wait}s)")
        time.sleep(poll)
    print("  [mail] timeout, no link found")
    return None


# ========== 浏览器登录取信（refresh_token 失效时的兜底，已验证可用）==========

async def _outlook_login(page, email, password):
    """用邮箱密码登录 Outlook。返回是否成功进入邮箱。
    已验证：登录后可能跳 passkey 设置页，直接导航 inbox URL 可绕过。"""
    try:
        await page.goto("https://login.live.com/", timeout=60000)
        await asyncio.sleep(3)
        ei = page.locator('input[type="email"], input[name="loginfmt"]').first
        if await ei.count() > 0:
            await ei.fill(email)
            await asyncio.sleep(0.5)
            await page.keyboard.press("Enter")
            await asyncio.sleep(3)
        pi = page.locator('input[type="password"], input[name="passwd"]').first
        if await pi.count() > 0:
            await pi.fill(password)
            await asyncio.sleep(0.5)
            await page.keyboard.press("Enter")
            await asyncio.sleep(5)

        # 密码提交后会出现若干中间页，逐个处理（轮询几轮，每轮点掉一个）：
        #  - 隐私/服务协议同意页 (Review your privacy / 隐私声明 / Accept and continue): 点 接受/同意/继续
        #  - "保持登录状态吗?" (Stay signed in / 保持登录): 点 是/Yes/继续/Continue
        #  - passkey 设置页 (Set up passkey / パスキー): 点 跳过/Skip/稍后/Not now
        #  - 其它确认页: 点 继续/确认/OK
        affirm = ["Accept and continue", "Accept", "Agree and continue", "I agree", "Agree",
                  "Looks good", "接受并继续", "接受", "同意并继续", "同意", "繼續使用", "继续使用",
                  "Yes", "是", "はい", "Sí", "Continue", "继续", "繼續", "確認", "确认",
                  "OK", "确定", "Next", "下一步", "Got it", "知道了"]
        skip = ["Skip", "跳过", "跳過", "Not now", "稍后", "稍後", "後で", "Maybe later", "今はしない", "暂时跳过"]
        for _ in range(6):
            await asyncio.sleep(2)
            # 已经进入邮箱就停
            if "outlook" in (page.url or "") and "live.com/mail" in (page.url or ""):
                break
            cur_url = (page.url or "").lower()
            body = ""
            try:
                body = (await page.locator("body").inner_text())[:400].lower()
            except Exception:
                pass
            clicked = False
            # 隐私/服务协议同意页（URL 或正文命中）：必须先点同意才能进邮箱
            if ("privacynotice" in cur_url or "privacy" in cur_url
                    or any(k in body for k in ["privacy statement", "review your privacy", "terms of use",
                                               "隐私声明", "隐私权", "查看您的隐私", "服务协议", "服務協定",
                                               "我们重视您的隐私", "review the updated"])):
                for label in ["Accept and continue", "Accept", "Agree and continue", "I agree", "Agree",
                              "接受并继续", "接受", "同意并继续", "同意", "Continue", "继续", "繼續", "Next", "OK"]:
                    b = page.locator(f'button:has-text("{label}"), input[value="{label}"], a:has-text("{label}")').first
                    if await b.count() > 0:
                        try:
                            await b.click(timeout=3000); clicked = True; await asyncio.sleep(2); break
                        except Exception:
                            pass
            # passkey/设置页优先点跳过
            if not clicked and any(k in body for k in ["passkey", "パスキー", "通行密钥", "密钥", "set up", "设置"]):
                for label in skip:
                    b = page.locator(f'button:has-text("{label}"), a:has-text("{label}")').first
                    if await b.count() > 0:
                        try:
                            await b.click(timeout=3000); clicked = True; await asyncio.sleep(2); break
                        except Exception:
                            pass
            if not clicked:
                for label in affirm:
                    b = page.locator(f'button:has-text("{label}"), input[value="{label}"]').first
                    if await b.count() > 0:
                        try:
                            await b.click(timeout=3000); clicked = True; await asyncio.sleep(2); break
                        except Exception:
                            pass
            if not clicked:
                # 没有可点的中间页按钮，尝试直接去邮箱
                break
        return True
    except Exception as e:
        print(f"  [mail-pw] login error: {e}")
        return False


async def _dismiss_inbox_popup(page):
    """关掉进收件箱后弹出的对话框/横幅：
    - 通知权限 "允许/Allow"、"打开/Turn on"，或 "稍后/Not now/暂时不/Dismiss/关闭/Got it"。
    - Outlook 的 "立即体验新版/继续/Skip" 引导弹窗。
    弹窗会盖住邮件列表导致扫不到验证码，先点掉。命中一个就返回。"""
    labels = ["Allow", "允许", "Turn on", "打开", "Yes", "是",
              "Not now", "稍后", "稍後", "暂时不", "暫時不", "Maybe later", "後で",
              "Dismiss", "关闭", "關閉", "Got it", "知道了", "OK", "Skip", "跳过", "跳過",
              "Continue", "继续", "繼續", "No thanks", "不用了"]
    try:
        for label in labels:
            b = page.locator(
                f'[role="dialog"] button:has-text("{label}"), '
                f'button:has-text("{label}"), a:has-text("{label}")'
            ).first
            if await b.count() > 0 and await b.is_visible():
                try:
                    await b.click(timeout=2500)
                    await asyncio.sleep(1)
                    return True
                except Exception:
                    pass
    except Exception:
        pass
    return False


async def _click_folder(page, folder_names):
    """点击左侧导航的文件夹（收件箱/垃圾邮件）触发列表加载。
    关键：Outlook 直接 goto junkemail URL 会得到空列表，必须点文件夹。"""
    try:
        clicked = await page.evaluate(
            """(names) => {
                const els = [...document.querySelectorAll('div[draggable=true], span, div[role=treeitem], [title], [role=option]')];
                for (const e of els) {
                    const t = (e.textContent || '').trim();
                    if (names.some(n => t === n || t.startsWith(n))) { e.click(); return t.slice(0, 30); }
                }
                return null;
            }""",
            folder_names,
        )
        return clicked
    except Exception:
        return None


def _first_group(m):
    """取正则第一个非空捕获组（支持 (A|B) 多格式 alternation 正则）。"""
    if not m:
        return None
    for g in m.groups():
        if g:
            return g
    return m.group(0)


async def _scan_current_folder(page, pat, sender_hint, subject_hint):
    """在当前已打开的 Outlook 文件夹里找目标邮件并提验证码。
    关键修复：必须先命中发件人/主题(hints)的邮件，再从它提码 —— 不能无差别扫顶部邮件，
    否则会抓到收件箱里旧邮件/欢迎邮件里的数字，返回错误验证码。
    策略：
      1) 只在「命中 hints 的列表项」预览文本里找码（命中即返回，避免点开等待）；
      2) 没在预览里找到 -> 点开命中 hints 的最新一封，读正文取码；
      3) 完全没有命中 hints 的邮件 -> 返回 None（让外层继续轮询等目标邮件到达），
         不再用'点开最顶部一封'兜底（那是抓错码的元凶）。"""
    hints = [h.lower() for h in (sender_hint + subject_hint) if h]
    # 等邮件列表渲染（[role=option] 是收件箱+垃圾箱通用的邮件项）
    for _ in range(8):
        await asyncio.sleep(2)
        try:
            n = await page.evaluate("() => document.querySelectorAll('[role=\"option\"]').length")
        except Exception:
            n = 0
        if n > 0:
            break
    else:
        return None

    # 1) 只在命中 hints 的列表项预览里取码（顶部最新 8 封内）
    try:
        hit_previews = await page.evaluate(
            """(hints) => {
                const items = [...document.querySelectorAll('[role="option"]')].slice(0, 8);
                const out = [];
                for (const it of items) {
                    const t = (it.textContent || '');
                    const tl = t.toLowerCase();
                    if (hints.some(h => tl.includes(h))) out.push(t);
                }
                return out;
            }""",
            hints,
        )
    except Exception:
        hit_previews = []
    for txt in hit_previews:
        m = pat.search(txt)
        if m:
            return _first_group(m)

    # 2) 点开命中 hints 的【最新】一封，读正文取码（列表按时间倒序，第一封命中即最新）
    try:
        clicked = await page.evaluate(
            """(hints) => {
                const items = [...document.querySelectorAll('[role="option"]')];
                for (const it of items) {
                    const t = (it.textContent || '').toLowerCase();
                    if (hints.some(h => t.includes(h))) { it.click(); return (it.textContent||'').slice(0,80); }
                }
                return null;   // 没有命中 hints 的邮件：不点开任何邮件，返回让外层继续轮询
            }""",
            hints,
        )
    except Exception:
        clicked = None
    if not clicked:
        return None

    # 等阅读窗格渲染，从正文(优先 [role=main] 阅读窗格)取码
    for _ in range(6):
        await asyncio.sleep(2)
        try:
            body = await page.evaluate(
                "() => { const m=document.querySelector('[role=main]'); return (m?m.innerText:document.body.innerText)||''; }"
            )
        except Exception:
            body = ""
        m = pat.search(body)
        if m:
            return _first_group(m)
    return None


async def fetch_from_broker(email, password, sender_hint, subject_hint, regex, kind, timeout):
    """broker 模式：把取码委托给共享取码服务 mailbox_broker（设了环境变量 MAILBOX_BROKER 时启用）。
    返回 code/link 字符串或 None。三个注册脚本并行时靠它共用一个 Outlook 会话、避免并发登录被拦。"""
    base = os.environ.get("MAILBOX_BROKER")
    if not base:
        return None
    import aiohttp
    url = base.rstrip("/") + "/fetch"
    payload = {
        "email": email, "password": password,
        "sender_hint": list(sender_hint), "subject_hint": list(subject_hint),
        "regex": regex, "kind": kind, "timeout": timeout,
    }
    try:
        cfg = aiohttp.ClientTimeout(total=timeout + 60)
        async with aiohttp.ClientSession(timeout=cfg) as sess:
            async with sess.post(url, json=payload) as resp:
                data = await resp.json()
        val = data.get("value")
        if val:
            print(f"  [broker] got {kind}: {val[:50]}")
        else:
            print(f"  [broker] no {kind} ({data.get('error', 'timeout')})")
        return val
    except Exception as e:
        print(f"  [broker] fetch error: {e}")
        return None


async def prelogin_outlook(page, email, password):
    """预登录 Outlook：登录 + 过隐私协议/passkey + 进收件箱，停在邮箱就绪状态。
    用法：在触发发码（如 chatgpt 提交邮箱）【之前】先调用它，等码一到立刻能扫到，
    避免"发码后才登录、登录+过协议耗时导致错过/超时"。返回是否就绪。"""
    if not await _outlook_login(page, email, password):
        return False
    try:
        await page.goto("https://outlook.live.com/mail/0/", timeout=60000)
        await asyncio.sleep(6)
    except Exception:
        pass
    await _dismiss_inbox_popup(page)   # 关掉通知权限/引导弹窗，免得盖住邮件列表
    return True


async def get_code_outlook_pw(
    page,
    email,
    password,
    sender_hint=("openai", "anthropic", "grok", "x.ai", "noreply", "no-reply"),
    subject_hint=("code", "verify", "verification", "openai", "chatgpt", "confirm", "验证"),
    code_regex=r"\b(\d{6})\b",
    max_wait=150,
    poll=8,
    skip_login=False,
):
    """浏览器登录 Outlook 取 6 位验证码（refresh_token 失效时用）。
    通过点击左侧文件夹切换 inbox/junk（直接 goto junk URL 列表为空）。
    page: BitBrowser 里新开的一个标签。返回 code 或 None。
    skip_login=True 时跳过登录+进收件箱（已用 prelogin_outlook 预登录），直接轮询。"""
    if os.environ.get("MAILBOX_BROKER"):
        return await fetch_from_broker(email, password, sender_hint, subject_hint, code_regex, "code", max_wait)
    pat = re.compile(code_regex)
    if not skip_login:
        if not await _outlook_login(page, email, password):
            return None
        # 进收件箱让整个邮箱界面完整加载一次
        try:
            await page.goto("https://outlook.live.com/mail/0/", timeout=60000)
            await asyncio.sleep(6)
        except Exception:
            pass
        await _dismiss_inbox_popup(page)   # 关掉通知权限/引导弹窗

    INBOX_NAMES = ["收件箱", "Inbox", "受信トレイ"]
    JUNK_NAMES = ["垃圾邮件", "Junk Email", "Junk", "迷惑メール"]

    start = time.time()
    while time.time() - start < max_wait:
        # 收件箱
        await _click_folder(page, INBOX_NAMES)
        await asyncio.sleep(2)
        code = await _scan_current_folder(page, pat, sender_hint, subject_hint)
        if code:
            print(f"  [mail-pw] code found in inbox: {code}")
            return code
        # 垃圾箱（点击文件夹触发加载）
        await _click_folder(page, JUNK_NAMES)
        await asyncio.sleep(2)
        code = await _scan_current_folder(page, pat, sender_hint, subject_hint)
        if code:
            print(f"  [mail-pw] code found in junk: {code}")
            return code
        elapsed = int(time.time() - start)
        print(f"  [mail-pw] waiting for code (inbox+junk)... ({elapsed}s/{max_wait}s)")
        await asyncio.sleep(poll)
    print("  [mail-pw] timeout, no code found")
    return None
