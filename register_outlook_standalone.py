# -*- coding: utf-8 -*-
"""
Standalone Outlook Email Registration Script
Uses BitBrowser + Playwright + Proxy to register Outlook accounts
Independent from the main register.py — only registers Outlook accounts

Usage:
  python register_outlook_standalone.py --count 10
  python register_outlook_standalone.py --count 5 --concurrency 2
  python register_outlook_standalone.py --proxy-file proxies.txt
"""

import argparse
import asyncio
import json
import math
import os
import random
import re
import string
import sys
import time
from datetime import datetime

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")

import requests
from playwright.async_api import async_playwright
try:
    from playwright_stealth import Stealth as _StealthCls
    _HAS_STEALTH = True
    _stealth_obj = _StealthCls()
except ImportError:
    _HAS_STEALTH = False
    _stealth_obj = None

try:
    from check_outlook_status import check_account_api
except Exception:
    check_account_api = None

# ======================== Configuration ========================

# 导入 config 以触发 .env 加载（密钥来自 .env / 真实环境变量）。
try:
    import config  # noqa: F401
except Exception:
    pass

# BitBrowser local API
BITBROWSER_API = os.environ.get("BITBROWSER_API", "http://127.0.0.1:54345")

# CAPTCHA solver keys（环境变量，默认空）
CAPSOLVER_API_KEY = os.environ.get("CAPSOLVER_API_KEY", "")
EZCAPTCHA_API_KEY = os.environ.get("EZCAPTCHA_API_KEY", "")
EZCAPTCHA_API_BASE = os.environ.get("EZCAPTCHA_API_BASE", "https://api.ez-captcha.com")

# Arkose Labs public key for Microsoft signup
MS_SIGNUP_ARKOSE_KEY = "B7D8911C-5CC8-A9A3-35B0-554ACEE604DA"

# Output
OUTPUT_DIR = "outlook_accounts"
SCREENSHOT_DIR = "screenshots_outlook"

# Registration timeout per account (seconds)
REGISTER_TIMEOUT = 300
VERIFY_AFTER_REGISTER = True


def verify_registered_outlook(email, password, tag=""):
    """Verify the saved password can actually log in before exporting the account."""
    if not VERIFY_AFTER_REGISTER:
        return True
    if check_account_api is None:
        print(f"  {tag} verify skipped: check_outlook_status unavailable")
        return True
    result = check_account_api(email, password)
    status = result.get("status")
    code = result.get("code") or ""
    msg = result.get("message") or ""
    print(f"  {tag} post-register verify: {status} {code} {msg[:80]}")
    return status == "ok"

# Default proxies (user:pass@host:port)
# 住宅代理账密池来自环境变量 OUTLOOK_PROXIES（多个用换行或逗号分隔），默认空。
# 也可用 --proxy-file 指定文件；两者都为空时不走代理。
def _load_default_proxies():
    raw = os.environ.get("OUTLOOK_PROXIES", "")
    if not raw:
        return []
    parts = [p.strip() for p in raw.replace(",", "\n").splitlines()]
    return [p for p in parts if p and not p.startswith("#")]


DEFAULT_PROXIES = _load_default_proxies()


# ======================== BitBrowser API ========================

class BitBrowserClient:
    """BitBrowser local API client with proxy support"""

    def __init__(self, api_base=None):
        self.api_base = api_base or BITBROWSER_API

    def _post(self, path, data=None):
        url = f"{self.api_base}{path}"
        resp = requests.post(url, json=data or {}, timeout=120)
        resp.raise_for_status()
        result = resp.json()
        if not result.get("success"):
            raise Exception(f"BitBrowser API error: {result.get('msg', 'unknown')}")
        return result

    def create_browser(self, name="outlook_reg", proxy_str=None):
        """Create a new browser profile with optional proxy.
        proxy_str format: user:pass@host:port
        """
        data = {
            "name": name,
            "remark": "outlook standalone registration",
            "proxyMethod": 2,  # custom proxy
            "browserFingerPrint": {
                "coreVersion": "130",
            },
        }

        if proxy_str:
            parsed = self._parse_proxy(proxy_str)
            if parsed:
                data["proxyType"] = parsed.get("type", "http")
                data["host"] = parsed["host"]
                data["port"] = parsed["port"]
                if parsed.get("username"):
                    data["proxyUserName"] = parsed["username"]
                if parsed.get("password"):
                    data["proxyPassword"] = parsed["password"]
                print(f"  proxy: [{data['proxyType']}] {parsed['host']}:{parsed['port']} (user={parsed.get('username', 'none')[:20]}...)")
            else:
                data["proxyType"] = "noproxy"
                print(f"  proxy: invalid format, using noproxy")
        else:
            data["proxyType"] = "noproxy"

        result = self._post("/browser/update", data)
        profile_id = result["data"]["id"]
        print(f"  browser created: {name} (ID: {profile_id})")
        return profile_id

    def open_browser(self, profile_id):
        """Open browser window, returns WebSocket debug URL"""
        result = self._post("/browser/open", {"id": profile_id})
        return result["data"]

    def close_browser(self, profile_id):
        """Close browser window"""
        try:
            self._post("/browser/close", {"id": profile_id})
        except Exception:
            pass

    def delete_browser(self, profile_id):
        """Delete browser profile"""
        try:
            self._post("/browser/delete", {"id": profile_id})
        except Exception:
            pass

    def cleanup_browsers(self, keep=0):
        """Delete all browser profiles (release quota)"""
        result = self._post("/browser/list", {"page": 0, "pageSize": 200})
        browsers = result["data"]["list"]
        if not browsers:
            return 0
        browsers.sort(key=lambda b: b.get("seq", 0), reverse=True)
        to_delete = browsers[keep:]
        deleted = 0
        for b in to_delete:
            try:
                self.close_browser(b["id"])
            except Exception:
                pass
            time.sleep(1)
            try:
                self.delete_browser(b["id"])
                deleted += 1
            except Exception:
                pass
        print(f"  cleanup: deleted {deleted}/{len(to_delete)} browsers")
        return deleted

    @staticmethod
    def _parse_proxy(proxy_str):
        """Parse proxy string into dict.
        Supported formats:
          socks5://user:pass@host:port
          socks5://host:port
          user:pass@host:port          (defaults to http)
          host:port                    (defaults to http)
        """
        # Strip protocol prefix
        proxy_type = "http"
        lower = proxy_str.lower()
        if lower.startswith("socks5://"):
            proxy_type = "socks5"
            proxy_str = proxy_str[len("socks5://"):]
        elif lower.startswith("http://"):
            proxy_str = proxy_str[len("http://"):]
        elif lower.startswith("https://"):
            proxy_str = proxy_str[len("https://"):]

        # Handle comma-separated format: user:pass,host:port
        proxy_str = proxy_str.replace(",", "@", 1) if "@" not in proxy_str and "," in proxy_str else proxy_str

        match = re.match(r'^(.+):(.+)@(.+):(\d+)$', proxy_str)
        if match:
            return {
                "type": proxy_type,
                "username": match.group(1),
                "password": match.group(2),
                "host": match.group(3),
                "port": match.group(4),
            }
        match2 = re.match(r'^(.+):(\d+)$', proxy_str)
        if match2:
            return {
                "type": proxy_type,
                "host": match2.group(1),
                "port": match2.group(2),
            }
        return None


# ======================== Helper Functions ========================

def generate_birthday():
    """Generate a random birthday (25-40 years old)"""
    current_year = datetime.now().year
    year = random.randint(current_year - 40, current_year - 25)
    month = random.randint(1, 12)
    if month in (1, 3, 5, 7, 8, 10, 12):
        max_day = 31
    elif month in (4, 6, 9, 11):
        max_day = 30
    else:
        max_day = 28
    day = random.randint(1, max_day)
    return year, month, day


def generate_name():
    """Generate a random English name"""
    first_names = [
        "James", "John", "Robert", "Michael", "David", "William", "Richard", "Joseph",
        "Thomas", "Charles", "Mary", "Patricia", "Jennifer", "Linda", "Barbara",
        "Elizabeth", "Susan", "Jessica", "Sarah", "Karen", "Emily", "Emma", "Olivia",
        "Daniel", "Matthew", "Anthony", "Mark", "Steven", "Andrew", "Brian",
    ]
    last_names = [
        "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
        "Davis", "Rodriguez", "Martinez", "Anderson", "Taylor", "Thomas", "Moore",
        "Jackson", "Martin", "Lee", "Thompson", "White", "Harris", "Clark",
    ]
    return random.choice(first_names), random.choice(last_names)


def generate_email_password():
    """Generate random Outlook email and password"""
    prefix = random.choice(string.ascii_lowercase) + "".join(
        random.choices(string.ascii_lowercase + string.digits, k=11)
    )
    email = f"{prefix}@outlook.com"
    password = "Aa1!" + "".join(random.choices(string.ascii_letters + string.digits, k=12))
    return email, password, prefix


# ======================== CAPTCHA Solvers ========================

def solve_arkose_capsolver(public_key=MS_SIGNUP_ARKOSE_KEY, page_url="https://signup.live.com/", max_wait=120):
    """Use CapSolver to solve Arkose Labs (FunCaptcha) challenge."""
    if not CAPSOLVER_API_KEY:
        print("  [capsolver] no API key, skipping")
        return None
    try:
        payload = {
            "clientKey": CAPSOLVER_API_KEY,
            "task": {
                "type": "FunCaptchaTaskProxyLess",
                "websiteURL": page_url,
                "websitePublicKey": public_key,
            }
        }
        resp = requests.post("https://api.capsolver.com/createTask", json=payload, timeout=30)
        data = resp.json()
        if data.get("errorId", 1) != 0:
            print(f"  [capsolver] create error: {data.get('errorDescription', data)}")
            return None
        task_id = data["taskId"]
        print(f"  [capsolver] task: {task_id}")
        start = time.time()
        while time.time() - start < max_wait:
            time.sleep(5)
            resp = requests.post("https://api.capsolver.com/getTaskResult", json={
                "clientKey": CAPSOLVER_API_KEY, "taskId": task_id,
            }, timeout=30)
            result = resp.json()
            if result.get("status") == "ready":
                token = result.get("solution", {}).get("token")
                print(f"  [capsolver] solved! {token[:60]}...")
                return token
            elif result.get("status") == "failed":
                print(f"  [capsolver] failed")
                return None
        print("  [capsolver] timeout")
        return None
    except Exception as e:
        print(f"  [capsolver] error: {e}")
        return None


def solve_funcaptcha_ezcaptcha(public_key=MS_SIGNUP_ARKOSE_KEY, page_url="https://signup.live.com/", max_wait=120,
                               proxy_host="proxy.proxyshare.com", proxy_port=5959,
                               proxy_user="ps-s46az41wfrmk_area-US", proxy_pass="hhChjubcVIpCfLo0"):
    """Use EZ-Captcha to solve FunCaptcha (with proxy for better success rate)."""
    if not EZCAPTCHA_API_KEY:
        return None
    try:
        task = {
            "type": "FunCaptchaTask",
            "websiteURL": page_url,
            "websitePublicKey": public_key,
            "proxyType": "http",
            "proxyAddress": proxy_host,
            "proxyPort": proxy_port,
            "proxyLogin": proxy_user,
            "proxyPassword": proxy_pass,
        }
        resp = requests.post(f"{EZCAPTCHA_API_BASE}/createTask", json={
            "clientKey": EZCAPTCHA_API_KEY,
            "task": task,
        }, timeout=30)
        data = resp.json()
        if data.get("errorId", 1) != 0:
            print(f"  [ezcaptcha] create error: {data.get('errorDescription', data)}")
            return None
        task_id = data["taskId"]
        print(f"  [ezcaptcha] task: {task_id}")
        start = time.time()
        while time.time() - start < max_wait:
            time.sleep(5)
            resp = requests.post(f"{EZCAPTCHA_API_BASE}/getTaskResult", json={
                "clientKey": EZCAPTCHA_API_KEY, "taskId": task_id,
            }, timeout=30)
            result = resp.json()
            if result.get("status") == "ready":
                token = result.get("solution", {}).get("token")
                print(f"  [ezcaptcha] solved! {token[:60]}...")
                return token
            elif result.get("status") == "failed":
                return None
        return None
    except Exception as e:
        print(f"  [ezcaptcha] error: {e}")
        return None


async def solve_perimeterx_capsolver(page, context, page_url="https://signup.live.com/", max_wait=120):
    """Use CapSolver to solve PerimeterX human challenge (press-and-hold).
    Returns a dict with _px2 / _pxhd keys on success, None on failure.
    Configure CAPSOLVER_API_KEY to enable.

    CapSolver task type: AntiPerimeterXTaskProxyless
    Docs: https://docs.capsolver.com/guide/antibots/perimeter_x.html
    """
    if not CAPSOLVER_API_KEY:
        return None
    try:
        # Gather PX cookies/tokens to help the solver
        cookies = await context.cookies()
        cookie_map = {c["name"]: c["value"] for c in cookies}
        pxvid = cookie_map.get("_pxvid", "")
        pxde  = cookie_map.get("_pxde", "")
        pxcts = cookie_map.get("pxcts", "")

        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"

        payload = {
            "clientKey": CAPSOLVER_API_KEY,
            "task": {
                "type": "AntiPerimeterXTaskProxyless",
                "websiteURL": page_url,
                "userAgent": ua,
                "_pxvid": pxvid,
                "_pxde": pxde,
                "pxcts": pxcts,
            },
        }
        resp = requests.post("https://api.capsolver.com/createTask", json=payload, timeout=30)
        data = resp.json()
        if data.get("errorId", 1) != 0:
            print(f"  [capsolver-px] create error: {data.get('errorDescription', data)}")
            return None
        task_id = data["taskId"]
        print(f"  [capsolver-px] task: {task_id}")
        start = time.time()
        while time.time() - start < max_wait:
            time.sleep(5)
            resp = requests.post("https://api.capsolver.com/getTaskResult",
                                 json={"clientKey": CAPSOLVER_API_KEY, "taskId": task_id},
                                 timeout=30)
            result = resp.json()
            if result.get("status") == "ready":
                solution = result.get("solution", {})
                print(f"  [capsolver-px] solved! keys: {list(solution.keys())}")
                return solution
            elif result.get("status") == "failed":
                print(f"  [capsolver-px] failed: {result.get('errorDescription', '')}")
                return None
        print("  [capsolver-px] timeout")
        return None
    except Exception as e:
        print(f"  [capsolver-px] error: {e}")
        return None


def solve_perimeterx_ezcaptcha(page_url="https://signup.live.com/", app_id="PXzC5j78di", max_wait=60):
    """Use EZ-Captcha to solve PerimeterX."""
    if not EZCAPTCHA_API_KEY:
        return None
    try:
        resp = requests.post(f"{EZCAPTCHA_API_BASE}/createTask", json={
            "clientKey": EZCAPTCHA_API_KEY,
            "task": {"type": "PerimeterX", "websiteURL": page_url, "websiteKey": app_id}
        }, timeout=30)
        data = resp.json()
        if data.get("errorId", 1) != 0:
            print(f"  [ezcaptcha-px] create error: {data.get('errorDescription', data)}")
            return None
        task_id = data["taskId"]
        print(f"  [ezcaptcha-px] task: {task_id}")
        start = time.time()
        while time.time() - start < max_wait:
            time.sleep(5)
            resp = requests.post(f"{EZCAPTCHA_API_BASE}/getTaskResult", json={
                "clientKey": EZCAPTCHA_API_KEY, "taskId": task_id,
            }, timeout=30)
            result = resp.json()
            if result.get("status") == "ready":
                solution = result.get("solution", {})
                print(f"  [ezcaptcha-px] solved! keys: {list(solution.keys())}")
                return solution
            elif result.get("status") == "failed":
                return None
        return None
    except Exception as e:
        print(f"  [ezcaptcha-px] error: {e}")
        return None


async def inject_arkose_token(page, token):
    """Inject solved Arkose token into the page."""
    try:
        injected = await page.evaluate(f"""
            () => {{
                const frames = document.querySelectorAll('iframe[id*="enforcement"], iframe[data-e2e="enforcement-frame"]');
                if (frames.length > 0 && window.CE_READY) {{
                    window.CE_READY("{token}");
                    return "ce_ready";
                }}
                const hidden = document.querySelector('input[name="fc-token"], input[name="FunCaptcha"]');
                if (hidden) {{ hidden.value = "{token}"; return "hidden_field"; }}
                if (typeof window.fcCallback === 'function') {{ window.fcCallback("{token}"); return "fc_callback"; }}
                if (typeof window.ArkoseEnforcement !== 'undefined') {{
                    try {{ window.ArkoseEnforcement.setConfig({{data: {{token: "{token}"}}}}) }} catch(e) {{}}
                    return "arkose_enforcement";
                }}
                return "no_method";
            }}
        """)
        print(f"  [arkose] inject: {injected}")
        return injected != "no_method"
    except Exception as e:
        print(f"  [arkose] inject error: {e}")
        return False


# ======================== Graph API Token ========================

# Microsoft public client for personal accounts (consumers tenant)
# Using Outlook Mobile client ID which supports personal accounts
GRAPH_CLIENT_ID = "27922004-5251-4030-b22d-91ecd9a37ea4"
GRAPH_REDIRECT_URI = "https://login.microsoftonline.com/common/oauth2/nativeclient"
GRAPH_SCOPE = "offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.Send https://graph.microsoft.com/User.Read"


async def extract_graph_token(page, context, email, password, idx=0):
    """Extract Microsoft Graph API refresh_token after registration.
    Uses OAuth2 authorization code flow with a native client (no secret needed).
    Uses 'consumers' tenant for personal Microsoft accounts (Outlook.com).
    Returns dict with access_token, refresh_token, or None on failure.
    """
    tag = f"[#{idx}]"
    try:
        import urllib.parse
        auth_url = (
            f"https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
            f"?client_id={GRAPH_CLIENT_ID}"
            f"&response_type=code"
            f"&redirect_uri={urllib.parse.quote(GRAPH_REDIRECT_URI, safe='')}"
            f"&scope={urllib.parse.quote(GRAPH_SCOPE)}"
            f"&prompt=consent"
        )
        print(f"  {tag} [graph] navigating to OAuth consent...")
        await page.goto(auth_url, timeout=30000, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        # May need to click accept/consent buttons
        for attempt in range(15):
            current_url = page.url
            # Check if redirected with auth code
            if GRAPH_REDIRECT_URI in current_url and "code=" in current_url:
                break

            # Click accept/yes/consent buttons
            for sel in [
                '#idBtn_Accept', 'input[id="idBtn_Accept"]',
                'button:has-text("Accept")', 'button:has-text("Yes")',
                'button:has-text("Accepter")', 'button:has-text("同意")',
                'input[type="submit"][value="Yes"]',
                'input[type="submit"][value="Accept"]',
                'input[type="submit"]', 'button[type="submit"]',
            ]:
                btn = page.locator(sel).first
                if await btn.count() > 0:
                    try:
                        await btn.click(timeout=3000)
                        print(f"  {tag} [graph] clicked: {sel}")
                        await asyncio.sleep(3)
                        break
                    except Exception:
                        pass

            # Login if needed (shouldn't be since we just registered)
            pwd_input = page.locator('input[type="password"]').first
            if await pwd_input.count() > 0:
                await pwd_input.fill(password)
                await page.keyboard.press("Enter")
                await asyncio.sleep(3)

            email_input = page.locator('input[type="email"], input[name="loginfmt"]').first
            if await email_input.count() > 0:
                await email_input.fill(email)
                await page.keyboard.press("Enter")
                await asyncio.sleep(3)

            await asyncio.sleep(2)

        current_url = page.url
        if "code=" not in current_url:
            print(f"  {tag} [graph] no auth code in URL: {current_url[:80]}")
            await page.screenshot(path=f"{SCREENSHOT_DIR}/outlook_{idx}_graph_fail.png")
            return None

        # Extract authorization code
        parsed = urllib.parse.urlparse(current_url)
        params = urllib.parse.parse_qs(parsed.query)
        auth_code = params.get("code", [None])[0]
        if not auth_code:
            print(f"  {tag} [graph] could not parse auth code")
            return None

        print(f"  {tag} [graph] got auth code: {auth_code[:30]}...")

        # Exchange code for tokens (consumers tenant for personal accounts)
        token_resp = requests.post(
            "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
            data={
                "client_id": GRAPH_CLIENT_ID,
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": GRAPH_REDIRECT_URI,
                "scope": GRAPH_SCOPE,
            },
            timeout=30,
        )
        token_data = token_resp.json()

        if "access_token" in token_data:
            print(f"  {tag} [graph] OK! refresh_token={('yes' if token_data.get('refresh_token') else 'no')}")
            return {
                "access_token": token_data["access_token"],
                "refresh_token": token_data.get("refresh_token"),
                "expires_in": token_data.get("expires_in"),
            }
        else:
            print(f"  {tag} [graph] token error: {token_data.get('error_description', token_data.get('error', '?'))[:100]}")
            return None

    except Exception as e:
        print(f"  {tag} [graph] error: {e}")
        return None


# ======================== Outlook Registration ========================

async def register_outlook(page, context, idx=0, captcha_early_abort=False):
    """
    Register a new Outlook email account.
    Returns (email, password) on success, (None, None) on failure.

    captcha_early_abort: when True (headless mode), abort immediately after captcha
    solvers fail so the caller can fall back faster. When False (browser/BitBrowser
    mode), keep the loop running — PX presses sometimes pass after 10–30 s naturally.
    """
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    tag = f"[#{idx}]"

    try:
        print(f"  {tag} navigating to signup page...")
        await page.goto("https://signup.live.com/signup?lic=1", timeout=60000, wait_until="domcontentloaded")
        await asyncio.sleep(3)
        await page.screenshot(path=f"{SCREENSHOT_DIR}/outlook_{idx}_start.png")

        # Handle privacy/consent pages (Chinese "个人数据导出许可", "同意并继续", etc.)
        for _consent_try in range(5):
            page_text = await page.evaluate("() => document.body.innerText")
            current_url = page.url.lower()
            # Check if on a consent/privacy page (not the actual signup form)
            # Only trigger for actual privacy/consent standalone pages, not signup pages with footer links
            is_signup_form = "signup.live.com" in current_url and "privacynotice" not in current_url
            if not is_signup_form and (
                any(kw in page_text for kw in ["同意并继续", "个人数据", "数据导出"]) or \
                any(kw in page_text.lower() for kw in [
                    "agree and continue", "consent", "data export",
                    "accepter et continuer", "consentement",
                ]) or "privacynotice" in current_url
            ):
                print(f"  {tag} privacy/consent page detected, clicking accept...")
                clicked = False
                # Try various accept buttons
                for sel in [
                    'button:has-text("同意并继续")', 'input[value="同意并继续"]',
                    'button:has-text("同意")', 'a:has-text("同意并继续")',
                    'button:has-text("Agree and continue")', 'button:has-text("Accept")',
                    'button:has-text("Continue")', 'button:has-text("OK")',
                    'button:has-text("Accepter et continuer")', 'button:has-text("Accepter")',
                    'button:has-text("Continuer")', 'button:has-text("Suivant")',
                    'input[type="submit"]', 'button[type="submit"]',
                    '#iNext', '#iAgree', '#acceptButton',
                ]:
                    btn = page.locator(sel).first
                    if await btn.count() > 0:
                        try:
                            await btn.click(timeout=5000)
                            print(f"  {tag} clicked consent: {sel}")
                            clicked = True
                            break
                        except Exception:
                            pass
                if not clicked:
                    # Fallback: click any visible button
                    try:
                        await page.evaluate("""() => {
                            const btns = document.querySelectorAll('button, input[type="submit"], a.btn');
                            for (const b of btns) {
                                if (b.offsetParent !== null && b.textContent.length < 30) {
                                    b.click(); return true;
                                }
                            }
                            return false;
                        }""")
                        print(f"  {tag} JS-clicked consent button")
                    except Exception:
                        pass
                await asyncio.sleep(3)
                await page.screenshot(path=f"{SCREENSHOT_DIR}/outlook_{idx}_after_consent_{_consent_try}.png")
            else:
                break

        # Generate email and password
        email, password, prefix = generate_email_password()
        print(f"  {tag} registering: {email}")

        # Step 1: Enter email
        email_ok = False
        for retry in range(5):
            email_input = page.locator(
                'input[type="email"], input[name="MemberName"], input[id="MemberName"], '
                'input[id="usernameInput"], input[name="Username"]'
            ).first
            if await email_input.count() == 0:
                print(f"  {tag} email input not found")
                await page.screenshot(path=f"{SCREENSHOT_DIR}/outlook_{idx}_no_email.png")
                return None, None

            domain_dropdown = page.locator(
                'select[id="LiveDomainBoxList"], select[name="LiveDomainBoxList"], #LiveDomainBoxList'
            ).first
            has_domain_dropdown = await domain_dropdown.count() > 0

            await email_input.fill("")
            await asyncio.sleep(0.3)
            if has_domain_dropdown:
                await email_input.fill(prefix)
                try:
                    await domain_dropdown.select_option("outlook.com")
                except Exception:
                    pass
                print(f"  {tag} filled prefix: {prefix} (dropdown)")
            else:
                await email_input.fill(email)
                print(f"  {tag} filled email: {email}")

            await asyncio.sleep(0.5)
            for sel in ['input[type="submit"]', 'button[type="submit"]', '#iSignupAction', 'button[id="iSignupAction"]']:
                btn = page.locator(sel).first
                if await btn.count() > 0:
                    await btn.click(timeout=3000)
                    break
            await asyncio.sleep(3)

            page_text = await page.evaluate("() => document.body.innerText")
            page_lower = page_text.lower()

            if ("already" in page_lower and "email" in page_lower) or "taken" in page_lower:
                prefix = random.choice(string.ascii_lowercase) + "".join(
                    random.choices(string.ascii_lowercase + string.digits, k=11)
                )
                email = f"{prefix}@outlook.com"
                print(f"  {tag} email taken, retry: {email}")
                continue

            if "needs to start" in page_lower or "in the format" in page_lower or "enter a valid" in page_lower or "use letters" in page_lower:
                prefix = random.choice(string.ascii_lowercase) + "".join(
                    random.choices(string.ascii_lowercase + string.digits, k=9)
                )
                email = f"{prefix}@outlook.com"
                print(f"  {tag} format error, retry: {email}")
                continue

            email_ok = True
            break

        if not email_ok:
            print(f"  {tag} all email attempts failed")
            await page.screenshot(path=f"{SCREENSHOT_DIR}/outlook_{idx}_email_fail.png")
            return None, None

        await page.screenshot(path=f"{SCREENSHOT_DIR}/outlook_{idx}_after_email.png")

        # Step 2: Enter password
        await asyncio.sleep(2)
        pwd_input = None
        for _ in range(10):
            pwd_input = page.locator(
                'input[type="password"], input[name="Password"], '
                'input[id="PasswordInput"], input[name="passwd"]'
            ).first
            if await pwd_input.count() > 0:
                break
            await asyncio.sleep(1)

        if pwd_input and await pwd_input.count() > 0:
            await pwd_input.fill(password)
            print(f"  {tag} password filled")
            await asyncio.sleep(0.5)

            clicked_next = False
            for sel in ['#iSignupAction', 'input[type="submit"]', 'button[type="submit"]',
                        'button:has-text("Next")', 'button:has-text("next")',
                        'button:has-text("下一步")', 'button:has-text("Suivant")']:
                btn = page.locator(sel).first
                if await btn.count() > 0:
                    try:
                        await btn.click(timeout=3000)
                        clicked_next = True
                        break
                    except Exception:
                        pass
            if not clicked_next:
                await page.keyboard.press("Enter")

            await asyncio.sleep(3)
            await page.screenshot(path=f"{SCREENSHOT_DIR}/outlook_{idx}_after_pwd.png")
        else:
            print(f"  {tag} password input not found")
            return None, None

        # Step 3: Country + Birthday
        year, month, day = generate_birthday()
        await asyncio.sleep(2)

        # Wait for birthday page to load (CN/EN/FR)
        for _ in range(10):
            page_text = await page.evaluate("() => document.body.innerText")
            if any(kw in page_text.lower() for kw in [
                "birth", "country", "region",
                "naissance", "pays", "région", "détails",
            ]) or any(kw in page_text for kw in ["出生", "国家", "地区", "年份", "详细信息"]):
                break
            await asyncio.sleep(1)

        await page.screenshot(path=f"{SCREENSHOT_DIR}/outlook_{idx}_bday_page.png")

        # Debug: dump all form elements
        form_debug = await page.evaluate("""() => {
            const els = document.querySelectorAll('input, select, button[role="combobox"], [role="combobox"], [role="listbox"]');
            return Array.from(els).filter(e => e.offsetParent !== null).map(e => ({
                tag: e.tagName, id: e.id, name: e.name, type: e.type || '',
                role: e.getAttribute('role') || '',
                ariaLabel: e.getAttribute('aria-label') || '',
                text: e.textContent ? e.textContent.trim().substring(0, 30) : '',
                placeholder: e.placeholder || '',
            }));
        }""")
        print(f"  {tag} form elements: {json.dumps(form_debug, ensure_ascii=False)[:600]}")

        all_selects = page.locator('select')
        select_count = await all_selects.count()
        print(f"  {tag} found {select_count} select elements")

        if select_count >= 2:
            # Traditional <select> dropdowns
            if select_count >= 3:
                try:
                    await all_selects.nth(0).select_option("US")
                    print(f"  {tag} country: US")
                except Exception:
                    try:
                        await all_selects.nth(0).select_option(index=1)
                    except Exception:
                        pass
                await all_selects.nth(1).select_option(str(month))
                await all_selects.nth(2).select_option(str(day))
            else:
                await all_selects.nth(0).select_option(str(month))
                await all_selects.nth(1).select_option(str(day))

            year_input = page.locator(
                'input[id*="Year"], input[id*="year"], input[name*="Year"], '
                'input[name*="year"], input[type="text"]'
            ).first
            if await year_input.count() > 0:
                await year_input.fill(str(year))
        else:
            # New UI with combobox/dropdown (Chinese or English)
            print(f"  {tag} no <select>, trying new UI (combobox)...")

            month_names_en = ["", "January", "February", "March", "April", "May", "June",
                              "July", "August", "September", "October", "November", "December"]
            # Chinese month names: 1月, 2月, ... 12月
            month_names_cn = ["", "1月", "2月", "3月", "4月", "5月", "6月",
                              "7月", "8月", "9月", "10月", "11月", "12月"]
            # French month names
            month_names_fr = ["", "janvier", "février", "mars", "avril", "mai", "juin",
                              "juillet", "août", "septembre", "octobre", "novembre", "décembre"]

            # Find all visible comboboxes
            combos = page.locator('button[role="combobox"], [role="combobox"]')
            combo_count = await combos.count()
            print(f"  {tag} found {combo_count} comboboxes")

            # Strategy: identify combos by their text/aria-label/position
            # Typically order is: Country, Month, Day (country may already be set)
            month_filled = False
            day_filled = False

            for ci in range(combo_count):
                combo = combos.nth(ci)
                try:
                    box = await combo.bounding_box()
                    if not box or box['width'] < 10:
                        continue
                    combo_text = (await combo.text_content() or "").strip()
                    combo_label = (await combo.get_attribute("aria-label") or "").lower()
                    combo_id = (await combo.get_attribute("id") or "").lower()
                    info = f"text='{combo_text}' label='{combo_label}' id='{combo_id}'"
                    print(f"  {tag} combo[{ci}]: {info}")

                    # Multi-language detection: EN/CN/FR/ES/DE/PT
                    is_month = any(kw in combo_label for kw in ["month", "月", "mois", "mes", "monat", "mês"]) or \
                               any(kw in combo_id for kw in ["month", "birthmonth"]) or \
                               combo_text in ["月", "Month", "月份", "Mois", "Mes"]
                    is_day = any(kw in combo_label for kw in ["day", "日", "jour", "día", "tag", "dia"]) or \
                             any(kw in combo_id for kw in ["day", "birthday"]) or \
                             combo_text in ["日", "Day", "Jour", "Día"]

                    # Disambiguate: if id contains both "day" and "month" substrings, use the more specific match
                    if is_month and is_day:
                        # Prefer the specific keyword: "birthdaydropdown" → day, "birthmonthdropdown" → month
                        if "month" in combo_id:
                            is_day = False
                        elif "day" in combo_id:
                            is_month = False

                    # If text contains "月" or "日" at end, it's already showing a value
                    if not is_month and not is_day:
                        if combo_text.endswith("月") and len(combo_text) <= 3:
                            is_month = True
                        elif combo_text.endswith("日") and len(combo_text) <= 4:
                            is_day = True

                    if is_month and not month_filled:
                        await combo.click(force=True)
                        await asyncio.sleep(1)
                        # Try month option (Chinese → English → French → number)
                        month_opt = page.locator(f'[role="option"]:has-text("{month_names_cn[month]}")').first
                        if await month_opt.count() == 0:
                            month_opt = page.locator(f'[role="option"]:has-text("{month_names_en[month]}")').first
                        if await month_opt.count() == 0:
                            month_opt = page.locator(f'[role="option"]:has-text("{month_names_fr[month]}")').first
                        if await month_opt.count() == 0:
                            month_opt = page.locator(f'[role="option"]:has-text("{month}")').first
                        if await month_opt.count() > 0:
                            await month_opt.click()
                            month_filled = True
                            print(f"  {tag} month: {month}")
                        else:
                            await page.keyboard.type(str(month))
                            await asyncio.sleep(0.3)
                            await page.keyboard.press("Enter")
                            month_filled = True
                        await asyncio.sleep(1)

                    elif is_day and not day_filled:
                        await combo.click(force=True)
                        await asyncio.sleep(1)
                        # Try day option: exact match first to avoid "1" matching "10","11"...
                        day_str = str(day)
                        # Try exact match via all options
                        day_opt = None
                        try:
                            all_opts = page.locator('[role="option"]')
                            opt_count = await all_opts.count()
                            for oi in range(opt_count):
                                opt_text = (await all_opts.nth(oi).text_content() or "").strip()
                                if opt_text == day_str or opt_text == f"{day}日":
                                    day_opt = all_opts.nth(oi)
                                    break
                        except Exception:
                            pass
                        if not day_opt:
                            day_opt = page.locator(f'[role="option"]:has-text("{day}日")').first
                        if not day_opt or await day_opt.count() == 0:
                            day_opt = page.locator(f'[role="option"]:has-text("{day_str}")').first
                        if await day_opt.count() > 0:
                            await day_opt.click()
                            day_filled = True
                            print(f"  {tag} day: {day}")
                        else:
                            await page.keyboard.type(str(day))
                            await asyncio.sleep(0.3)
                            await page.keyboard.press("Enter")
                            day_filled = True
                        await asyncio.sleep(1)
                except Exception as e:
                    print(f"  {tag} combo[{ci}] error: {e}")

            if not month_filled or not day_filled:
                print(f"  {tag} WARNING: month_filled={month_filled}, day_filled={day_filled}")

            # Year input (text field)
            year_input = page.locator(
                '#BirthYearInput, [aria-label*="year" i], [aria-label*="年" i], '
                '[id*="Year" i], [id*="year" i], [placeholder*="年" i], '
                'input[type="text"][inputmode="numeric"], input[type="number"]'
            ).first
            # Fallback: find the text input that's NOT already filled
            if await year_input.count() == 0:
                all_text = page.locator('input[type="text"]')
                for ti in range(await all_text.count()):
                    inp = all_text.nth(ti)
                    val = await inp.input_value()
                    if not val:  # empty text input = likely year
                        year_input = inp
                        break
            if await year_input.count() > 0:
                await year_input.fill(str(year))
                print(f"  {tag} year: {year}")

        await asyncio.sleep(0.5)
        for sel in ['input[type="submit"]', 'button[type="submit"]', '#iSignupAction',
                    'button[id="iSignupAction"]', 'button:has-text("下一步")',
                    'button:has-text("Next")', 'button:has-text("next")',
                    'button:has-text("Suivant")']:
            btn = page.locator(sel).first
            if await btn.count() > 0:
                await btn.click(timeout=3000)
                print(f"  {tag} clicked next (bday): {sel}")
                break
        await asyncio.sleep(3)
        await page.screenshot(path=f"{SCREENSHOT_DIR}/outlook_{idx}_after_bday.png")

        # Step 4: Username/Gamertag (Chinese: 游戏标签/用户名)
        await asyncio.sleep(2)
        username_input = page.locator(
            'input[id*="displayName"], input[id*="gamertag"], input[name*="displayName"], '
            'input[placeholder*="name"], input[type="text"]'
        ).first
        if await username_input.count() > 0:
            page_text = await page.evaluate("() => document.body.innerText")
            if any(kw in page_text.lower() for kw in ["name", "gamertag", "nom", "pseudo", "surnom"]) or \
               any(kw in page_text for kw in ["用户名", "游戏标签", "显示名称"]):
                username = prefix[:8] + str(random.randint(100, 999))
                await username_input.fill(username)
                print(f"  {tag} username: {username}")
                await asyncio.sleep(0.5)
                for sel in ['input[type="submit"]', 'button[type="submit"]', '#iSignupAction',
                            'button:has-text("下一步")', 'button:has-text("Next")',
                            'button:has-text("Suivant")']:
                    btn = page.locator(sel).first
                    if await btn.count() > 0:
                        await btn.click(timeout=3000)
                        break
                await asyncio.sleep(3)

        # Step 5: First/Last Name + checkbox (Chinese: 姓/名)
        first_name, last_name = generate_name()
        await asyncio.sleep(2)

        for _ in range(10):
            fname_input = page.locator(
                'input[name="FirstName"], input[id="FirstName"], input[name="firstNameInput"], '
                'input[id="firstNameInput"], input[aria-label*="first" i], input[placeholder*="first" i], '
                'input[aria-label*="名" i], input[placeholder*="名" i], '
                'input[aria-label*="prénom" i], input[placeholder*="prénom" i]'
            ).first
            lname_input = page.locator(
                'input[name="LastName"], input[id="LastName"], input[name="lastNameInput"], '
                'input[id="lastNameInput"], input[aria-label*="last" i], input[aria-label*="surname" i], '
                'input[placeholder*="last" i], input[aria-label*="姓" i], input[placeholder*="姓" i], '
                'input[aria-label*="nom de famille" i], input[placeholder*="nom de famille" i]'
            ).first
            if await fname_input.count() > 0 or await lname_input.count() > 0:
                break
            all_text_inputs = page.locator('input[type="text"]')
            if await all_text_inputs.count() >= 2:
                break
            await asyncio.sleep(1)

        if await fname_input.count() > 0:
            if await lname_input.count() > 0:
                await lname_input.fill(last_name)
            await fname_input.fill(first_name)
            print(f"  {tag} name: {first_name} {last_name}")
        else:
            all_text_inputs = page.locator('input[type="text"]')
            count = await all_text_inputs.count()
            if count >= 2:
                await all_text_inputs.nth(0).fill(last_name)
                await all_text_inputs.nth(1).fill(first_name)
                print(f"  {tag} name (generic): {first_name} {last_name}")

        checkbox = page.locator('input[type="checkbox"], [role="checkbox"]').first
        if await checkbox.count() > 0:
            try:
                checked = await checkbox.is_checked()
            except Exception:
                checked = False
            if not checked:
                await checkbox.click(force=True)
                print(f"  {tag} checkbox checked")

        await asyncio.sleep(0.5)
        for sel in ['input[type="submit"]', 'button[type="submit"]', '#iSignupAction',
                    'button[id="iSignupAction"]', 'button:has-text("Next")',
                    'button:has-text("下一步")', 'button:has-text("Suivant")']:
            btn = page.locator(sel).first
            if await btn.count() > 0:
                await btn.click(timeout=3000)
                print(f"  {tag} clicked next (name): {sel}")
                break
        await asyncio.sleep(3)
        await page.screenshot(path=f"{SCREENSHOT_DIR}/outlook_{idx}_after_name.png")

        # Step 6: CAPTCHA handling
        print(f"  {tag} checking for captcha...")
        await asyncio.sleep(3)

        arkose_solved = False
        press_count = 0
        # headless: 5 presses max (abort quickly on fail)
        # browser: 15 presses max (keep trying; PX sometimes passes after retries)
        max_press = 5 if captcha_early_abort else 15
        # Allow caller to cap presses tighter via env (e.g. bs_register_step1
        # sets this to 3 so failed PX checks fail-fast and we move on to
        # lqqq/backup instead of burning ~3 min per dud signup).
        _env_max_press = os.environ.get("OUTLOOK_REG_MAX_PRESS", "").strip()
        if _env_max_press.isdigit():
            max_press = min(max_press, int(_env_max_press))
        no_btn_rounds = 0
        had_captcha = False          # 是否真的出现过 captcha（避免一上来误判已通过）
        gone_rounds = 0              # captcha 消失后连续多少轮仍停在 signup（等跳转）

        async def _captcha_visible():
            """页面上是否还有【可交互】的 PerimeterX 按住验证（按住按钮 / hsprotect iframe）。
            captcha 通过后会变成 Loading 转圈、这些元素消失 -> 返回 False。"""
            try:
                for sel in ['button:has-text("Press and hold")', 'button:has-text("Appuyer et maintenir")',
                            'button:has-text("按住")', 'button:has-text("长按")',
                            'button:has-text("Halten")', '#px-captcha']:
                    el = page.locator(sel).first
                    if await el.count() > 0:
                        b = await el.bounding_box()
                        if b and b['width'] > 30:
                            return True
                ifr = page.locator('iframe[src*="hsprotect.net"], iframe[src*="arkose"], iframe[src*="funcaptcha"]')
                for hi in range(await ifr.count()):
                    b = await ifr.nth(hi).bounding_box()
                    if b and b['width'] > 50 and b['height'] > 30:
                        return True
            except Exception:
                pass
            return False

        # headless: 90 s captcha window; browser: 240 s (multiple press rounds)
        _captcha_rounds = 30 if captcha_early_abort else 80
        # When max_press is small, shrink the wait loop too — otherwise we'd
        # exhaust presses then idle for the remaining captcha window.
        # Rough budget: ~10s per press cycle. +20s slack for first solver call.
        _capped_rounds = max(8, max_press * 4 + 8)
        _captcha_rounds = min(_captcha_rounds, _capped_rounds)

        for wait_round in range(_captcha_rounds):
            try:
                page_text = (await page.evaluate("() => document.body.innerText")).lower()
                current_url = page.url.lower()
            except Exception:
                await asyncio.sleep(3)
                try:
                    page_text = (await page.evaluate("() => document.body.innerText")).lower()
                    current_url = page.url.lower()
                except Exception:
                    current_url = page.url.lower()
                    if "signup" not in current_url:
                        break
                    continue

            # —— captcha 通过判定（精确按 host）——
            # 坑：captcha 过后页面跳到 privacynotice.account.microsoft.com/notice?ru=...，
            # 其 ru= 参数里带 "signup" 字样，旧的裸 "signup" 子串判定 -> 误以为还在 signup
            # -> 一直 retrying presses 直到超时。改为按真实 host 判断是否已离开 signup 表单。
            on_signup_form = ("signup.live.com" in current_url) and ("privacynotice" not in current_url)
            if not on_signup_form and any(h in current_url for h in [
                    "privacynotice", "account.microsoft.com", "account.live.com",
                    "outlook.live.com", "outlook.office", "login.live.com/oauth20"]):
                print(f"  {tag} captcha passed, left signup -> {current_url[:70]}")
                break

            # Success checks
            if "outlook" in current_url and "signup" not in current_url and "login" not in current_url:
                print(f"  {tag} registration complete!")
                break
            if "welcome" in page_text or "inbox" in page_text or "account has been created" in page_text:
                print(f"  {tag} registration complete!")
                break
            if "signup" not in current_url and "live.com" in current_url:
                print(f"  {tag} left signup: {current_url[:60]}")
                break

            # captcha 消失判定：过验证后页面变 "Loading..." 转圈、按住按钮/iframe 消失，
            # 但 URL 可能还没跳转（异步）。此时不该再按压/超时 —— 标记已过，进入等跳转模式。
            if had_captcha:
                if await _captcha_visible():
                    gone_rounds = 0
                else:
                    gone_rounds += 1
                    if gone_rounds == 1:
                        print(f"  {tag} captcha 元素已消失（验证通过/Loading），等待页面跳转…")
                    # 轻推一下提交按钮，催收尾
                    if gone_rounds % 3 == 0:
                        for sel in ['#iSignupAction', 'input[type="submit"]', 'button[type="submit"]']:
                            try:
                                b = page.locator(sel).first
                                if await b.count() > 0 and await b.is_visible():
                                    await b.click(timeout=3000); break
                            except Exception:
                                pass
                    # 等够 ~20 轮(≈60s)仍没跳转，去 post-captcha 收尾兜底（不再傻等超时）
                    if gone_rounds >= 20:
                        print(f"  {tag} captcha 已过但久未跳转，进入收尾流程")
                        break
                    await asyncio.sleep(3)
                    continue

            # Account blocked detection (CN/EN/FR)
            if any(kw in page_text for kw in [
                "帐户创建已被阻止", "已被阻止", "阻止创建",
                "account creation has been blocked", "has been blocked", "account has been suspended",
                "création de compte a été bloquée", "a été bloquée", "bloquée",
                "unusual activity", "异常活动", "activité inhabituelle",
            ]):
                print(f"  {tag} BLOCKED: account creation blocked by Microsoft")
                await page.screenshot(path=f"{SCREENSHOT_DIR}/outlook_{idx}_blocked.png")
                return None, None

            # FIDO/passkey - skip
            if "fido" in current_url or "passkey" in current_url:
                for sel in ['a:has-text("Skip")', 'button:has-text("Skip")', 'a:has-text("No thanks")',
                            'button:has-text("No thanks")', 'button:has-text("Cancel")', '#skipBtn']:
                    btn = page.locator(sel).first
                    if await btn.count() > 0:
                        try:
                            await btn.click(timeout=3000)
                            break
                        except Exception:
                            pass
                else:
                    try:
                        await page.evaluate("""() => {
                            for (const l of document.querySelectorAll('a, button')) {
                                const t = l.textContent.toLowerCase();
                                if (t.includes('skip') || t.includes('no thanks') || t.includes('cancel')) { l.click(); return; }
                            }
                        }""")
                    except Exception:
                        pass
                await asyncio.sleep(3)
                continue

            # Privacy notice
            if "privacynotice" in current_url:
                await asyncio.sleep(2)
                for label in ['OK', 'Accept', 'Continue', 'Next', 'I agree', 'Got it']:
                    btn = page.locator(f'button:has-text("{label}"), input[value="{label}"], a:has-text("{label}")').first
                    if await btn.count() > 0:
                        try:
                            await btn.click(timeout=3000)
                            break
                        except Exception:
                            pass
                await asyncio.sleep(3)
                continue

            # PerimeterX press-and-hold
            if press_count < max_press:
                pressed = False
                target_box = None

                # 定位「按住」按钮。诊断已确认：按钮是可见 hsprotect iframe 内的 #px-captcha
                # 元素(box 如 y485~527,height 42)，page.locator 穿不进跨域 iframe，必须遍历
                # page.frames 在 frame 内取 #px-captcha 的真实坐标。优先用它(box_is_button=True)，
                # 拿不到才退回整个 iframe 框按比例。
                box_is_button = False
                # 1) frame 内真按钮 #px-captcha（取可见的那个 frame：width>0）
                for f in page.frames:
                    if f == page.main_frame or 'hsprotect.net' not in (f.url or ''):
                        continue
                    try:
                        px = f.locator('#px-captcha').first
                        if await px.count() > 0:
                            b = await px.bounding_box()
                            if b and b['width'] > 30 and b['height'] > 8:
                                target_box = b; box_is_button = True
                                break
                    except Exception:
                        pass
                # 2) 退回整个可见 hsprotect iframe 框
                if not target_box:
                    try:
                        hs = page.locator('iframe[src*="hsprotect.net"]')
                        for hi in range(await hs.count()):
                            b = await hs.nth(hi).bounding_box()
                            if b and b['width'] > 50 and b['height'] > 30:
                                target_box = b
                                break
                    except Exception:
                        pass

                if target_box and target_box['width'] > 30 and target_box['height'] >= 8:
                    press_count += 1
                    pressed = True
                    had_captcha = True   # 出现过 captcha，供「消失=已通过」判定使用
                    bx, by, bw, bh = target_box['x'], target_box['y'], target_box['width'], target_box['height']
                    if box_is_button:
                        # target_box 就是真按钮 #px-captcha：按其中心 + 小随机抖动
                        cx = bx + bw * random.uniform(0.40, 0.60)
                        cy = by + bh * random.uniform(0.40, 0.60)
                    else:
                        # 退回整个 iframe 框：按钮在中部窄带（实测 0.48-0.62 命中）
                        cx = bx + bw * random.uniform(0.42, 0.58)
                        cy = by + bh * random.uniform(0.48, 0.62)
                    print(f"  {tag} press #{press_count}: ({cx:.0f},{cy:.0f}){' [btn]' if box_is_button else ' [box]'}")

                    # Bezier mouse movement
                    sx, sy = random.uniform(200, 800), random.uniform(200, 400)
                    await page.mouse.move(sx, sy)
                    await asyncio.sleep(random.uniform(0.3, 0.8))
                    steps = random.randint(15, 30)
                    ctrl_x = (sx + cx) / 2 + random.uniform(-100, 100)
                    ctrl_y = (sy + cy) / 2 + random.uniform(-80, 80)
                    for step in range(1, steps + 1):
                        t = step / steps
                        mx = (1 - t) ** 2 * sx + 2 * (1 - t) * t * ctrl_x + t ** 2 * cx + random.uniform(-1.5, 1.5)
                        my = (1 - t) ** 2 * sy + 2 * (1 - t) * t * ctrl_y + t ** 2 * cy + random.uniform(-1.5, 1.5)
                        await page.mouse.move(mx, my)
                        await asyncio.sleep(random.uniform(0.005, 0.025))

                    await asyncio.sleep(random.uniform(0.1, 0.3))
                    await page.mouse.down()

                    # 拟人按住：关键是按住到进度条走满(captcha 消失)才松手 —— 不能固定短时长，
                    # 太短(3-5s)进度没满就松手 => 不过。策略：持续按住并每 ~0.5s 检测 captcha 是否
                    # 消失(进度满/通过)，一消失立刻松手；设一个较长上限(~14s)兜底防止卡死。
                    max_hold = random.uniform(11.0, 15.0)
                    hold_start = asyncio.get_event_loop().time()
                    drift_phase = random.uniform(0, 2 * math.pi)
                    drift_freq = random.uniform(1.5, 2.8)
                    last_chk = 0.0
                    passed_in_hold = False
                    while True:
                        elapsed = asyncio.get_event_loop().time() - hold_start
                        if elapsed >= max_hold:
                            break
                        ph = drift_phase + elapsed * drift_freq
                        await page.mouse.move(cx + 1.0 * math.sin(ph), cy + 0.6 * math.cos(ph * 1.3))
                        # 进度满后 captcha 消失就松手（但至少按住 1.5s，避免误判刚加载的空隙）
                        if elapsed > 1.5 and elapsed - last_chk > 0.5:
                            last_chk = elapsed
                            try:
                                if not await _captcha_visible():
                                    passed_in_hold = True
                                    break
                            except Exception:
                                pass
                        await asyncio.sleep(random.uniform(0.03, 0.08))

                    await asyncio.sleep(random.uniform(0.05, 0.18))
                    await page.mouse.up()
                    held = asyncio.get_event_loop().time() - hold_start
                    print(f"  {tag} held {held:.1f}s{' (passed)' if passed_in_hold else ''}")
                    await asyncio.sleep(random.uniform(2, 4))

                    try:
                        await page.screenshot(path=f"{SCREENSHOT_DIR}/outlook_{idx}_hold_{press_count}.png")
                    except Exception:
                        pass
                else:
                    no_btn_rounds += 1
                    # Scan frames for clickable buttons
                    try:
                        for f in page.frames:
                            if f == page.main_frame:
                                continue
                            frame_url = f.url.lower()
                            if frame_url == "about:blank" or "cfp.microsoft.com" in frame_url:
                                continue
                            try:
                                btns = f.locator('button, [role="button"], input[type="button"], input[type="submit"]')
                                for bi in range(await btns.count()):
                                    box = await btns.nth(bi).bounding_box()
                                    if box and box['width'] > 30 and box['height'] > 20:
                                        x = box['x'] + box['width'] / 2
                                        y = box['y'] + box['height'] / 2
                                        press_count += 1
                                        await page.mouse.move(x, y)
                                        await asyncio.sleep(0.3)
                                        await page.mouse.down()
                                        await asyncio.sleep(18)
                                        await page.mouse.up()
                                        pressed = True
                                        await asyncio.sleep(5)
                                        break
                                if pressed:
                                    break
                            except Exception:
                                continue
                    except Exception:
                        pass

                if pressed:
                    no_btn_rounds = 0

            # Main page captcha buttons
            if press_count < max_press and no_btn_rounds >= 3:
                try:
                    main_btns = page.locator('#hipTemplateContainer button, #HipPaneForm button, [id*="hip"] button')
                    for bi in range(await main_btns.count()):
                        box = await main_btns.nth(bi).bounding_box()
                        if box and box['width'] > 20:
                            press_count += 1
                            await main_btns.nth(bi).click(timeout=3000)
                            await asyncio.sleep(5)
                            no_btn_rounds = 0
                            break
                except Exception:
                    pass

            # Try submit
            if no_btn_rounds >= 8 and no_btn_rounds % 8 == 0:
                try:
                    for sel in ['#iSignupAction', 'input[type="submit"]', 'button[type="submit"]']:
                        submit = page.locator(sel).first
                        if await submit.count() > 0 and await submit.is_visible():
                            await submit.click(timeout=3000)
                            await asyncio.sleep(5)
                            break
                except Exception:
                    pass

            # 按满次数：两个打码器(capsolver-px/ezcaptcha-px)对 MS 这个 PerimeterX
            # 按住验证都没用(类型不支持/解不出)，已移除。按满后给一个短观察窗等跳转：
            # 若手动按住其实已过，循环顶部「captcha 消失=已通过」会接管收尾；若仍可见(没过)，
            # 观察窗内不再按压、等满 ~24s 就快速放弃，不空等到 captcha timeout。
            if press_count >= max_press and not arkose_solved:
                arkose_solved = True
                arkose_wait_start = wait_round
                print(f"  {tag} 按满 {max_press} 次，停止按压，等待页面跳转")
            if arkose_solved and had_captcha:
                # 仍能看到 captcha = 没过；给 8 轮(~24s)缓冲后快速放弃
                if await _captcha_visible():
                    if wait_round - arkose_wait_start >= 8:
                        print(f"  {tag} 按满仍未通过，快速放弃本号")
                        await page.screenshot(path=f"{SCREENSHOT_DIR}/outlook_{idx}_press_fail.png")
                        return None, None

            if wait_round % 5 == 0:
                await page.screenshot(path=f"{SCREENSHOT_DIR}/outlook_{idx}_wait_{wait_round}.png")
                print(f"  {tag} waiting... ({wait_round * 3}s)")

            await asyncio.sleep(3)
        else:
            print(f"  {tag} captcha timeout")
            await page.screenshot(path=f"{SCREENSHOT_DIR}/outlook_{idx}_timeout.png")
            return None, None

        # Post-captcha pages
        for retry in range(10):
            current_url = page.url.lower()
            # 精确判断：已离开 signup 表单且不在隐私声明页 = 收尾完成（同样避免 ru= 里
            # 的 "signup" 子串误伤）。
            on_signup_form = ("signup.live.com" in current_url) and ("privacynotice" not in current_url)
            if not on_signup_form and "privacynotice" not in current_url:
                break
            for label in ['OK', 'Accept', 'Continue', 'Next', 'I agree', 'Got it', 'Agree']:
                btn = page.locator(f'button:has-text("{label}"), input[value="{label}"], a:has-text("{label}")').first
                if await btn.count() > 0:
                    try:
                        await btn.click(timeout=3000)
                        break
                    except Exception:
                        pass
            await asyncio.sleep(3)

        if not verify_registered_outlook(email, password, tag):
            print(f"  {tag} verification failed, discarding account")
            return None, None

        print(f"  {tag} OK: {email} / {password}")
        return email, password

    except Exception as e:
        print(f"  {tag} FAILED: {e}")
        try:
            await page.screenshot(path=f"{SCREENSHOT_DIR}/outlook_{idx}_error.png")
        except Exception:
            pass
        return None, None


# ======================== Protocol Mode (pure HTTP) ========================

def _proxy_for_requests(proxy_str):
    """Convert proxy string to requests proxies dict."""
    if not proxy_str:
        return None
    p = BitBrowserClient._parse_proxy(proxy_str)
    if not p:
        return None
    auth = f"{p['username']}:{p['password']}@" if p.get("username") else ""
    url = f"{p.get('type', 'http')}://{auth}{p['host']}:{p['port']}"
    return {"http": url, "https": url}


def _proxy_for_playwright(proxy_str):
    """Convert proxy string to Playwright proxy dict."""
    if not proxy_str:
        return None
    p = BitBrowserClient._parse_proxy(proxy_str)
    if not p:
        return None
    result = {"server": f"{p.get('type', 'http')}://{p['host']}:{p['port']}"}
    if p.get("username"):
        result["username"] = p["username"]
        result["password"] = p["password"]
    return result


def register_outlook_protocol(proxy_str=None, idx=0):
    """
    Register Outlook via pure HTTP requests — no browser, ~50KB per attempt.
    Returns (email, password) on success, (None, None) on failure/captcha.
    """
    tag = f"[#{idx}][proto]"
    session = requests.Session()
    proxies = _proxy_for_requests(proxy_str)
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    })

    try:
        print(f"  {tag} GET signup page...")
        resp = session.get(
            "https://signup.live.com/signup?lic=1",
            proxies=proxies, timeout=30, allow_redirects=True,
        )
        if resp.status_code != 200:
            print(f"  {tag} HTTP {resp.status_code}")
            return None, None

        html = resp.text

        # Microsoft signup is a React SPA — form fields rendered via JS.
        # Protocol mode only works if the server-side rendered form is present.
        # Detect availability by checking for MemberName field.
        if "MemberName" not in html and "iSignupAction" not in html:
            print(f"  {tag} SPA form not in HTML (JS-rendered) — protocol N/A")
            return None, None

        # Detect immediate bot-block
        if any(kw in html.lower() for kw in ["perimeterx", "px-block", "_px.init", "bot protection"]):
            print(f"  {tag} PerimeterX blocked on load")
            return None, None

        # Extract PPFT (CSRF token)
        ppft = None
        for pat in [
            r'name="PPFT"[^>]*value="([^"]+)"',
            r'"sFT"\s*:\s*"([^"]+)"',
            r"sFT\s*:\s*'([^']+)'",
        ]:
            m = re.search(pat, html)
            if m:
                ppft = m.group(1)
                break
        if not ppft:
            print(f"  {tag} no PPFT token found")
            return None, None

        # Extract uaid and action URL
        uaid_m = re.search(r'[?&]uaid=([A-Za-z0-9\-]+)', html)
        uaid = uaid_m.group(1) if uaid_m else ""
        action_m = re.search(r'action="(https://signup\.live\.com[^"]+)"', html)
        action_url = action_m.group(1) if action_m else f"https://signup.live.com/signup?lic=1&uaid={uaid}"

        # Extract canary token (CSRF #2, optional)
        canary_name_m = re.search(r'"sCanaryTokenName"\s*:\s*"([^"]+)"', html)
        canary_val_m = re.search(r'"sCanaryToken"\s*:\s*"([^"]+)"', html)
        canary_name = canary_name_m.group(1) if canary_name_m else ""
        canary_val = canary_val_m.group(1) if canary_val_m else ""

        # Generate account details
        email, password, prefix = generate_email_password()
        first_name, last_name = generate_name()
        year, month, day = generate_birthday()
        print(f"  {tag} trying: {email}")

        form_data = {
            "MemberName": f"{prefix}@outlook.com",
            "Password": password,
            "FirstName": first_name,
            "LastName": last_name,
            "BirthDate": str(day),
            "BirthMonth": str(month),
            "BirthYear": str(year),
            "Country": "US",
            "LiveDomainBoxList": "outlook.com",
            "LcId": "1033",
            "PPFT": ppft,
            "lic": "1",
            "sErrorCode": "",
            "iSignupFlow": "2",
        }
        if canary_name and canary_val:
            form_data[canary_name] = canary_val

        resp2 = session.post(
            action_url,
            data=form_data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://signup.live.com/signup?lic=1",
                "Origin": "https://signup.live.com",
            },
            proxies=proxies, timeout=30, allow_redirects=True,
        )

        final_url = resp2.url.lower()
        body = resp2.text.lower()

        # Success: left signup domain
        if "signup" not in final_url and any(kw in final_url for kw in ["outlook", "live.com", "microsoft"]):
            if not verify_registered_outlook(email, password, tag):
                print(f"  {tag} verification failed, discarding proto account")
                return None, None
            print(f"  {tag} OK (proto): {email}")
            return email, password

        # Captcha / bot detection → fall back
        if any(kw in body for kw in ["captcha", "perimeterx", "challenge", "press and hold",
                                      "verify you're human", "unusual activity", "_pxhd"]):
            print(f"  {tag} captcha/bot detected — proto failed")
            return None, None

        # Email taken
        if ("already" in body and "email" in body) or "taken" in body:
            print(f"  {tag} email taken")
            return None, None

        # Blocked
        if "blocked" in body or "suspended" in body:
            print(f"  {tag} account blocked")
            return None, None

        print(f"  {tag} unknown result: {resp2.url[:80]}")
        return None, None

    except Exception as e:
        print(f"  {tag} error: {e}")
        return None, None


# ======================== Headless Mode (Playwright, no BitBrowser) ========================

# Headless: block everything heavy (CSS too, since rendering doesn't matter for detection)
_BLOCK_TYPES_HEADLESS = {"image", "stylesheet", "font", "media", "other"}

# Browser mode: keep CSS so PerimeterX doesn't detect missing stylesheets (captcha check)
_BLOCK_TYPES_BROWSER = {"image", "font", "media"}

# Domains that must NOT be blocked even for heavy resource types
_ALLOW_DOMAINS = {
    "fpt.live.com",          # PerimeterX DFP iframe — generates fptctx2 cookie
    "hsprotect.net",         # PerimeterX human challenge iframe
    "px-cloud.net",          # PerimeterX CDN
    "px-cdn.net",            # PerimeterX CDN alt
    "client.px-cloud.net",   # PerimeterX client
}


def _make_block_handler(block_types):
    """Return a route handler that blocks the given resource types.
    Always allows PerimeterX/captcha domains through.
    """
    async def _handler(route):
        url = route.request.url
        for domain in _ALLOW_DOMAINS:
            if domain in url:
                await route.continue_()
                return
        if route.request.resource_type in block_types:
            await route.abort()
        else:
            await route.continue_()
    return _handler


_block_heavy_resources = _make_block_handler(_BLOCK_TYPES_HEADLESS)   # headless: aggressive
_block_browser_resources = _make_block_handler(_BLOCK_TYPES_BROWSER)   # browser: keep CSS


async def _register_one_headless(idx, proxy_str):
    """
    Register via truly headless Chrome (no window shown to user).
    Uses headless=True with comprehensive fingerprint patches to compensate
    for the missing headed-browser signals that PerimeterX checks.
    Resource blocking saves ~70% bandwidth vs full BitBrowser.
    Returns (email, password) or (None, None).
    """
    tag = f"[#{idx}][headless]"
    try:
        async with async_playwright() as pw:
            args = [
                "--no-sandbox", "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-default-apps",
                "--disable-extensions",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1280,800",
            ]
            try:
                # Prefer real Chrome binary (better fingerprint than bundled Chromium)
                browser = await pw.chromium.launch(
                    channel="chrome",
                    headless=True,
                    proxy=_proxy_for_playwright(proxy_str),
                    args=args,
                )
                print(f"  {tag} using real Chrome (headless, no window)")
            except Exception:
                # Fallback to bundled Playwright Chromium
                browser = await pw.chromium.launch(
                    headless=True,
                    proxy=_proxy_for_playwright(proxy_str),
                    args=args,
                )
                print(f"  {tag} using Playwright Chromium (headless, no window)")

            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="en-US",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/136.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            # playwright-stealth: patches navigator.webdriver, plugins, languages, etc.
            if _HAS_STEALTH:
                await _stealth_obj.apply_stealth_async(page)

            # Additional patches for properties PX checks that stealth misses
            # in headless mode (document.hidden, visibilityState, chrome.runtime, etc.)
            await context.add_init_script("""
                // --- Headless detection fixes ---
                // PX checks document.hidden and visibilityState
                try {
                    Object.defineProperty(document, 'hidden', {get: () => false});
                    Object.defineProperty(document, 'visibilityState', {get: () => 'visible'});
                } catch(e){}

                // PX checks document.hasFocus()
                try {
                    document.hasFocus = function(){ return true; };
                } catch(e){}

                // screen dimensions (headless often returns 0x0)
                try {
                    if (!screen.width || screen.width < 100) {
                        Object.defineProperty(screen, 'width',       {get: () => 1920});
                        Object.defineProperty(screen, 'height',      {get: () => 1080});
                        Object.defineProperty(screen, 'availWidth',  {get: () => 1920});
                        Object.defineProperty(screen, 'availHeight', {get: () => 1040});
                        Object.defineProperty(screen, 'colorDepth',  {get: () => 24});
                        Object.defineProperty(screen, 'pixelDepth',  {get: () => 24});
                    }
                } catch(e){}

                // --- chrome.runtime stub (PX checks window.chrome.runtime) ---
                if (!window.chrome) window.chrome = {};
                if (!window.chrome.runtime) {
                    window.chrome.runtime = {
                        id: undefined,
                        connect: function(){},
                        sendMessage: function(){},
                        onMessage: {addListener: function(){}, removeListener: function(){}},
                        onConnect: {addListener: function(){}, removeListener: function(){}},
                        getManifest: function(){ return {}; },
                        getURL: function(p){ return 'chrome-extension://invalid/' + p; },
                        PlatformOs: {MAC:'mac',WIN:'win',ANDROID:'android',CROS:'cros',LINUX:'linux',OPENBSD:'openbsd'},
                        PlatformArch: {ARM:'arm',X86_32:'x86-32',X86_64:'x86-64'},
                    };
                }

                // --- deviceMemory ---
                try {
                    if (!navigator.deviceMemory) {
                        Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
                    }
                } catch(e){}

                // --- WebGL vendor/renderer ---
                (function() {
                    var _gp = WebGLRenderingContext.prototype.getParameter;
                    WebGLRenderingContext.prototype.getParameter = function(p) {
                        if (p === 37445) return 'NVIDIA Corporation';
                        if (p === 37446) return 'NVIDIA GeForce GTX 750 Ti/PCIe/SSE2';
                        return _gp.call(this, p);
                    };
                    try {
                        var _gp2 = WebGL2RenderingContext.prototype.getParameter;
                        WebGL2RenderingContext.prototype.getParameter = function(p) {
                            if (p === 37445) return 'NVIDIA Corporation';
                            if (p === 37446) return 'NVIDIA GeForce GTX 750 Ti/PCIe/SSE2';
                            return _gp2.call(this, p);
                        };
                    } catch(e){}
                })();

                // --- Remove CDP/Playwright leaks ---
                delete window.__playwright;
                delete window.__pwInitScripts;
                try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array; } catch(e){}
                try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise; } catch(e){}
            """)
            print(f"  {tag} headless browser ready (stealth+patches, no window)")

            # Block heavy resources to save bandwidth (headless doesn't need CSS for rendering)
            await page.route("**/*", _block_heavy_resources)

            # Abort early when captcha solvers fail so auto-mode falls back to BitBrowser fast
            email, password = await register_outlook(page, context, idx, captcha_early_abort=True)

            try:
                await browser.close()
            except Exception:
                pass

            return email, password

    except Exception as e:
        print(f"  {tag} error: {e}")
        return None, None


# ======================== Browser Mode (BitBrowser, full GUI) ========================

async def _register_one_browser(bb, idx, proxy_str):
    """
    Register via BitBrowser full browser (highest traffic, most reliable).
    Returns (email, password) or (None, None).
    """
    tag = f"[#{idx}][browser]"
    profile_id = None
    try:
        ts = datetime.now().strftime("%m%d_%H%M%S")
        name = f"outlook_{ts}_{idx}"

        for _retry in range(5):
            try:
                profile_id = bb.create_browser(name=name, proxy_str=proxy_str)
                break
            except Exception as e:
                err_msg = str(e)
                if '最大创建窗口数' in err_msg or '超过' in err_msg:
                    print(f"  {tag} browser quota full, cleaning up...")
                    bb.cleanup_browsers(keep=2)
                    await asyncio.sleep(3)
                    continue
                elif 'TLS' in err_msg or 'socket' in err_msg or 'ECONNRESET' in err_msg:
                    print(f"  {tag} BitBrowser TLS error (retry {_retry + 1}/5)")
                    await asyncio.sleep(5 + _retry * 3)
                    continue
                elif _retry < 4:
                    print(f"  {tag} create browser error (retry {_retry + 1}): {err_msg[:80]}")
                    await asyncio.sleep(3)
                    continue
                else:
                    raise

        if not profile_id:
            print(f"  {tag} create browser failed")
            return None, None

        info = bb.open_browser(profile_id)
        ws = info.get("ws", "")
        if not ws:
            print(f"  {tag} no WebSocket URL")
            return None, None

        print(f"  {tag} BitBrowser connected")
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(ws)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()
            # NOTE: resource blocking intentionally disabled in browser mode.
            # PerimeterX behavioral analysis can detect modified network patterns.
            # Bandwidth saving via resource blocking only applies in headless mode.
            email, password = await register_outlook(page, context, idx)

        return email, password

    except Exception as e:
        print(f"  {tag} error: {e}")
        return None, None
    finally:
        if profile_id:
            try:
                bb.close_browser(profile_id)
                await asyncio.sleep(2)
                bb.delete_browser(profile_id)
                print(f"  {tag} browser cleaned up")
            except Exception:
                pass


# ======================== Main ========================

async def register_one(bb, idx, proxy_str, results, results_lock, live_fh=None, mode="auto"):
    """
    Register one Outlook account with fallback across three modes.
      mode="auto"     — protocol → headless → browser (fallback chain)
      mode="protocol" — HTTP only, fastest, lowest traffic
      mode="headless" — headless Playwright, no BitBrowser, ~70% less traffic
      mode="browser"  — BitBrowser full GUI, highest traffic, most reliable
    """
    tag = f"[#{idx}]"
    email, password = None, None
    used_mode = None

    try:
        # ── 1. Protocol mode (pure HTTP, ~50KB) ──────────────────
        if mode in ("auto", "protocol"):
            print(f"  {tag} [1/3] protocol mode...")
            loop = asyncio.get_event_loop()
            email, password = await loop.run_in_executor(
                None, register_outlook_protocol, proxy_str, idx
            )
            if email:
                used_mode = "protocol"

        # ── 2. Headless mode (~70% less traffic than browser) ────
        # Use shorter timeout so we fall back to browser faster on captcha stall
        HEADLESS_TIMEOUT = min(REGISTER_TIMEOUT, 180)
        if not email and mode in ("auto", "headless"):
            print(f"  {tag} [2/3] headless mode (timeout={HEADLESS_TIMEOUT}s)...")
            try:
                email, password = await asyncio.wait_for(
                    _register_one_headless(idx, proxy_str),
                    timeout=HEADLESS_TIMEOUT,
                )
            except asyncio.TimeoutError:
                print(f"  {tag} headless timeout → falling back to browser")
            if email:
                used_mode = "headless"

        # ── 3. Browser mode (BitBrowser, full GUI) ───────────────
        if not email and mode in ("auto", "browser"):
            print(f"  {tag} [3/3] browser mode (BitBrowser)...")
            try:
                email, password = await asyncio.wait_for(
                    _register_one_browser(bb, idx, proxy_str),
                    timeout=REGISTER_TIMEOUT,
                )
            except asyncio.TimeoutError:
                print(f"  {tag} browser timeout")
            if email:
                used_mode = "browser"

    except Exception as e:
        print(f"  {tag} FATAL: {e}")

    # ── Save result ───────────────────────────────────────────────
    async with results_lock:
        if email:
            results.append({
                "index": idx, "email": email, "password": password,
                "status": "OK", "proxy": proxy_str, "mode": used_mode,
            })
            if live_fh:
                live_fh.write(f"{email}----{password}\n")
                live_fh.flush()
            print(f"  {tag} SUCCESS [{used_mode}]: {email}")
        else:
            results.append({
                "index": idx, "email": None, "password": None,
                "status": "FAIL", "proxy": proxy_str,
            })
            print(f"  {tag} FAILED all modes")


async def main():
    parser = argparse.ArgumentParser(description="Standalone Outlook Registration — multi-mode with fallback")
    parser.add_argument("--count", "-n", type=int, default=10, help="Number of accounts to register")
    parser.add_argument("--concurrency", "-c", type=int, default=2, help="Parallel registrations")
    parser.add_argument("--proxy-file", "-p", type=str, help="Proxy file (one per line: user:pass@host:port)")
    parser.add_argument("--no-proxy", action="store_true", default=False, help="No proxy")
    parser.add_argument("--timeout", "-t", type=int, default=300, help="Per-account timeout (seconds)")
    parser.add_argument("--mode", "-m", type=str, default="auto",
                        choices=["auto", "protocol", "headless", "browser"],
                        help="auto=protocol→headless→browser fallback; or fix to one mode")
    parser.add_argument("--no-verify", action="store_true",
                        help="Do not verify Outlook login before writing successful accounts")
    args = parser.parse_args()

    global REGISTER_TIMEOUT, VERIFY_AFTER_REGISTER
    REGISTER_TIMEOUT = args.timeout
    VERIFY_AFTER_REGISTER = not args.no_verify

    # Load proxies
    proxy_pool = []
    if args.proxy_file:
        with open(args.proxy_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    proxy_pool.append(line)
        if not proxy_pool:
            print("  WARNING: proxy file is empty, falling back to DEFAULT_PROXIES")

    if not proxy_pool and not args.no_proxy:
        proxy_pool = list(DEFAULT_PROXIES)

    count = args.count
    if args.no_proxy:
        # noproxy mode: fill with None
        proxies = [None] * count
    elif proxy_pool:
        # Cycle proxies: assign round-robin to each account
        proxies = [proxy_pool[i % len(proxy_pool)] for i in range(count)]
    else:
        proxies = [None] * count

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    bb = BitBrowserClient()

    proxy_mode = "noproxy" if args.no_proxy else f"{len(proxy_pool)} unique proxies (cycling for {count} accounts)"
    mode_desc = {
        "auto":     "protocol → headless → browser (fallback chain)",
        "protocol": "protocol only (pure HTTP, lowest traffic)",
        "headless": "headless only (no BitBrowser, -70% traffic)",
        "browser":  "browser only (BitBrowser full GUI)",
    }
    print("=" * 60)
    print("  Outlook Registration - Multi-mode")
    print(f"  count={count}  concurrency={args.concurrency}  timeout={args.timeout}s")
    print(f"  mode:  {args.mode} — {mode_desc[args.mode]}")
    print(f"  proxy: {proxy_mode}")
    print("=" * 60)

    results = []
    results_lock = asyncio.Lock()
    sem = asyncio.Semaphore(args.concurrency)

    # Real-time incremental output files
    ts_live = datetime.now().strftime('%Y%m%d_%H%M%S')
    live_file = os.path.join(OUTPUT_DIR, f"accounts_{ts_live}.txt")
    live_fh = open(live_file, "a", encoding="utf-8", buffering=1)
    print(f"  Live output: {live_file}")

    async def run_one(i):
        async with sem:
            if i > 0:
                await asyncio.sleep(random.uniform(2, 5))
            proxy = proxies[i]
            print(f"\n{'#' * 50}")
            print(f"  Account #{i + 1}/{count}")
            print(f"{'#' * 50}")
            await register_one(bb, i + 1, proxy, results, results_lock, live_fh, mode=args.mode)

    await asyncio.gather(*[run_one(i) for i in range(count)])
    live_fh.close()

    # Summary
    print(f"\n{'=' * 60}")
    print(f"  RESULTS: {len(results)} total")
    print(f"{'=' * 60}")

    ok_count = 0
    graph_count = 0
    mode_counts = {"protocol": 0, "headless": 0, "browser": 0}
    ts_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(OUTPUT_DIR, f"accounts_{ts_str}.txt")
    token_file = os.path.join(OUTPUT_DIR, f"graph_tokens_{ts_str}.json")

    all_tokens = []
    with open(output_file, "w", encoding="utf-8") as f:
        for r in sorted(results, key=lambda x: x['index']):
            if r['status'] == "OK":
                ok_count += 1
                line = f"{r['email']}----{r['password']}"
                graph = r.get("graph")
                if graph and graph.get("refresh_token"):
                    graph_count += 1
                    line += f"----{graph['refresh_token']}"
                    all_tokens.append({
                        "email": r['email'],
                        "password": r['password'],
                        "access_token": graph.get('access_token'),
                        "refresh_token": graph.get('refresh_token'),
                        "expires_in": graph.get('expires_in'),
                    })
                f.write(line + "\n")
                used = r.get("mode", "?")
                mode_counts[used] = mode_counts.get(used, 0) + 1
                gt = " +graph" if graph and graph.get("refresh_token") else ""
                print(f"  #{r['index']} [OK/{used}{gt}] {r['email']} / {r['password']}")
            else:
                print(f"  #{r['index']} [{r['status']}] -")

    if all_tokens:
        with open(token_file, "w", encoding="utf-8") as f:
            json.dump(all_tokens, f, indent=2, ensure_ascii=False)

    mode_str = "  |  ".join(f"{m}:{c}" for m, c in mode_counts.items() if c > 0)
    print(f"\n  Success: {ok_count}/{len(results)}  |  {mode_str}  |  Graph tokens: {graph_count}")
    if ok_count > 0:
        print(f"  Accounts: {output_file}")
    if graph_count > 0:
        print(f"  Tokens:   {token_file}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
