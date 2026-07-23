# -*- coding: utf-8 -*-
"""
验证 accounts 文件里的 sessionKey 是否有效
用法: python validate_keys.py accounts-3.24.txt
"""
import asyncio
import sys
import os
from datetime import datetime
from urllib.parse import urlparse

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from playwright.async_api import async_playwright
from bitbrowser import BitBrowser

INPUT_FILE = sys.argv[1] if len(sys.argv) > 1 else "cookies/accounts-3.24.txt"
OUTPUT_VALID = INPUT_FILE.replace(".txt", "_valid.txt")
OUTPUT_INVALID = INPUT_FILE.replace(".txt", "_invalid.txt")


def validation_browser_options():
    options = {
        "browserFingerPrint": {
            "coreVersion": os.environ.get(
                "CLAUDE_BROWSER_CORE_VERSION",
                os.environ.get("BB_CORE_VERSION", "146"),
            ),
            "isIpCreateTimeZone": True,
            "isIpCreateLanguage": True,
            "isIpCreateDisplayLanguage": True,
            "isIpCreatePosition": True,
            "isIpCountry": True,
        }
    }
    proxy_url = os.environ.get(
        "CLASH_PROXY", "http://127.0.0.1:7897"
    ).strip()
    if not proxy_url or proxy_url.lower() in {"none", "off", "direct"}:
        return options
    parsed = urlparse(proxy_url if "://" in proxy_url else f"http://{proxy_url}")
    if not parsed.hostname or not parsed.port:
        return options
    options.update({
        "proxyMethod": 2,
        "proxyType": "http",
        "host": parsed.hostname,
        "port": str(parsed.port),
    })
    if parsed.username:
        options["proxyUserName"] = parsed.username
    if parsed.password:
        options["proxyPassword"] = parsed.password
    return options


async def validate_key(sk: str, bb: BitBrowser) -> bool:
    """用 BitBrowser 浏览器验证 sessionKey：打开 claude.ai，发一条消息，收到回复才算有效"""
    name = f"validate_{datetime.now().strftime('%H%M%S')}"
    profile_id = None
    try:
        browser_options = validation_browser_options()
        profile_id = bb.create_browser(name=name, **browser_options)
        if browser_options.get("proxyType") == "http":
            print(
                "  validation window via "
                f"{browser_options['host']}:{browser_options['port']}"
            )
        info = bb.open_browser(profile_id)
        ws = info.get("ws", "")

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(ws)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()

            # 设置 sessionKey cookie
            await context.add_cookies([{
                "name": "sessionKey",
                "value": sk,
                "domain": ".claude.ai",
                "path": "/",
            }])

            # 导航到 claude.ai
            await page.goto("https://claude.ai", timeout=30000)
            await asyncio.sleep(3)

            # 检查是否被重定向到登录页
            if '/login' in page.url or '/logout' in page.url:
                print(f"  redirected to login — banned")
                return False

            # 发消息验证
            result = await page.evaluate("""
                async () => {
                    try {
                        // 1) 拿 org_uuid
                        const ar = await fetch('https://claude.ai/api/account', {credentials: 'include'});
                        if (!ar.ok) return {step: 'account', status: ar.status};
                        const ad = await ar.json();
                        const org_uuid = (ad.memberships||[])[0]?.organization?.uuid;
                        if (!org_uuid) return {step: 'account', error: 'no org'};

                        // 2) 建会话
                        const cr = await fetch(`https://claude.ai/api/organizations/${org_uuid}/chat_conversations`, {
                            method: 'POST',
                            credentials: 'include',
                            headers: {'content-type': 'application/json'},
                            body: JSON.stringify({uuid: crypto.randomUUID(), name: ''}),
                        });
                        if (!cr.ok) return {step: 'create_conv', status: cr.status};
                        const cd = await cr.json();
                        const conv_uuid = cd.uuid;

                        // 3) 发消息
                        const mr = await fetch(`https://claude.ai/api/organizations/${org_uuid}/chat_conversations/${conv_uuid}/completion`, {
                            method: 'POST',
                            credentials: 'include',
                            headers: {'content-type': 'application/json'},
                            body: JSON.stringify({
                                prompt: 'hi',
                                timezone: 'Asia/Shanghai',
                                attachments: [],
                                files: [],
                            }),
                        });
                        if (!mr.ok) return {step: 'send_msg', status: mr.status};

                        // 4) 读 SSE 流
                        const reader = mr.body.getReader();
                        const decoder = new TextDecoder();
                        let text = '';
                        for (let i = 0; i < 30; i++) {
                            const {done, value} = await reader.read();
                            if (done) break;
                            text += decoder.decode(value);
                            if (text.includes('"completion"')) return {step: 'done', ok: true};
                        }
                        return {step: 'done', ok: false, text: text.substring(0, 100)};
                    } catch(e) {
                        return {step: 'error', error: e.message};
                    }
                }
            """)
            ok = result.get('ok', False)
            step = result.get('step', '')
            if ok:
                print(f"  chat reply received — valid")
            else:
                print(f"  failed at step={step} status={result.get('status','')} {result.get('error','')}")
            return ok
    except Exception as e:
        print(f"  error: {e}")
        return False
    finally:
        try:
            if profile_id:
                bb.close_browser(profile_id)
                await asyncio.sleep(1)
                bb.delete_browser(profile_id)
        except Exception:
            pass


async def main():
    lines = []
    with open(INPUT_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                lines.append(line)

    print(f"Validating {len(lines)} keys from {INPUT_FILE}...")

    bb = BitBrowser()
    valid = []
    invalid = []

    for i, line in enumerate(lines, 1):
        parts = line.split("|")
        if len(parts) < 3:
            print(f"[{i}/{len(lines)}] skip (bad format): {line[:40]}")
            continue
        email, password, sk = parts[0], parts[1], parts[2]
        print(f"[{i}/{len(lines)}] {email} {sk[:30]}...")
        ok = await validate_key(sk, bb)
        if ok:
            valid.append(line)
        else:
            invalid.append(line)

    with open(OUTPUT_VALID, "w", encoding="utf-8") as f:
        f.write("\n".join(valid) + ("\n" if valid else ""))
    with open(OUTPUT_INVALID, "w", encoding="utf-8") as f:
        f.write("\n".join(invalid) + ("\n" if invalid else ""))

    print(f"\nDone: {len(valid)} valid, {len(invalid)} invalid")
    print(f"Valid: {OUTPUT_VALID}")
    print(f"Invalid: {OUTPUT_INVALID}")


if __name__ == "__main__":
    asyncio.run(main())
