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
import os
import random
import re
import string
import sys
import time
import urllib.parse
from datetime import datetime

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stdin, "reconfigure"):
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

# 拟人鼠标(WindMouse 轨迹 + OU 震颤)用于 PerimeterX 按住验证。保证脚本被
# importlib 从任意路径加载时也能找到 common 包。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import human_mouse as _hm

# BitBrowser local API
BITBROWSER_API = os.environ.get("BITBROWSER_API", "http://127.0.0.1:54345")


def ensure_clash_proxy_env():
    """Use .env CLASH_PROXY for direct standalone runs, while local APIs stay direct."""
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
    parts = [p.strip() for p in no_proxy.split(",") if p.strip()]
    for item in ("127.0.0.1", "localhost", "::1"):
        if item not in parts:
            parts.append(item)
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = ",".join(parts)
    return proxy


def _fingerprint_provider():
    return (
        os.environ.get("FINGERPRINT_BROWSER")
        or os.environ.get("BROWSER_PROVIDER")
        or "bitbrowser"
    ).strip().lower()

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

    def __new__(cls, api_base=None):
        if cls is BitBrowserClient and _fingerprint_provider() in {"adspower", "ads_power", "ads"}:
            from bitbrowser import BitBrowser
            return BitBrowser(api_base=api_base)
        return super().__new__(cls)

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

# Thunderbird's public client is enabled for consumer Microsoft accounts and
# is also used by extract_graph_tokens.py. Keep each RT paired with this ID.
GRAPH_CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
GRAPH_REDIRECT_URI = "http://localhost"
GRAPH_SCOPE = "offline_access https://graph.microsoft.com/Mail.Read"
GRAPH_DEVICE_CODE_URL = (
    "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
)
GRAPH_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
MICROSOFT_UI_LOCALE = os.environ.get("OUTLOOK_UI_LOCALE", "en-US").strip() or "en-US"

# Microsoft occasionally ignores mkt/ui_locales and renders according to the exit IP.
# Keep text matching as a fallback, but prefer stable element IDs and field metadata.
MS_POSITIVE_ACTION_LABELS = (
    "accept", "allow", "continue", "yes", "agree", "next", "ok",
    "agree and continue", "i agree", "got it",
    "\u662f",
    "accepter", "autoriser", "continuer", "oui", "j'accepte", "suivant",
    "aceptar", "permitir", "continuar", "sí", "siguiente", "acepto",
    "akzeptieren", "zulassen", "weiter", "ja", "zustimmen",
    "aceitar", "permitir", "continuar", "sim", "avançar", "concordo",
    "accetta", "consenti", "continua", "sì", "avanti", "accetto",
    "accepteren", "toestaan", "doorgaan", "ja", "volgende", "akkoord",
    "zaakceptuj", "zezwól", "kontynuuj", "tak", "dalej", "zgadzam się",
    "принять", "разрешить", "продолжить", "да", "далее", "согласен",
    "kabul et", "izin ver", "devam et", "evet", "ileri", "kabul ediyorum",
    "قبول", "السماح", "متابعة", "نعم", "التالي", "موافق", "أوافق",
    "接受", "允许", "同意", "继续", "下一步", "确定",
    "允許", "繼續", "下一步", "確定",
    "承諾", "許可", "続行", "次へ", "はい", "同意する",
    "동의", "허용", "계속", "다음", "예", "확인",
)
MS_NEGATIVE_ACTION_LABELS = (
    "deny", "decline", "cancel", "no", "back",
    "\u5426",
    "refuser", "annuler", "non",
    "rechazar", "denegar", "cancelar", "no",
    "ablehnen", "abbrechen", "nein",
    "recusar", "negar", "cancelar", "não",
    "rifiuta", "nega", "annulla", "no",
    "weigeren", "annuleren", "nee",
    "odrzuć", "anuluj", "nie",
    "отклонить", "отмена", "нет",
    "reddet", "iptal", "hayır",
    "رفض", "إلغاء", "لا", "رجوع",
    "拒绝", "拒絕", "取消", "否", "いいえ", "拒否", "취소", "거부", "아니요",
)
MS_SKIP_ACTION_LABELS = (
    "skip for now", "skip", "not now", "no thanks", "maybe later", "later",
    "ignorer pour le moment", "ignorer", "pas maintenant", "non merci", "plus tard",
    "omitir por ahora", "omitir", "ahora no", "no, gracias", "más tarde",
    "vorerst überspringen", "überspringen", "nicht jetzt", "nein, danke", "später",
    "ignorar por enquanto", "ignorar", "agora não", "não, obrigado", "mais tarde",
    "ignora per ora", "ignora", "non ora", "no, grazie", "più tardi",
    "voorlopig overslaan", "overslaan", "niet nu", "nee, bedankt", "later",
    "pomiń na razie", "pomiń", "nie teraz", "nie, dziękuję", "później",
    "пропустить", "не сейчас", "нет, спасибо", "позже",
    "şimdilik atla", "atla", "şimdi değil", "hayır, teşekkürler", "daha sonra",
    "تخطي الآن", "تخطي", "ليس الآن", "لا شكرًا", "ربما لاحقًا", "لاحقًا",
    "暂时跳过", "跳过", "现在不", "以后再说", "不用了",
    "暫時略過", "略過", "現在不要", "稍後再說",
    "今はしない", "スキップ", "後で", "건너뛰기", "나중에", "지금은 안 함",
)


def _microsoft_url_with_locale(url):
    """Add locale hints without overwriting OAuth/device-code parameters."""
    parsed = urllib.parse.urlsplit(url)
    params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    existing = {key.lower() for key, _ in params}
    if "mkt" not in existing:
        params.append(("mkt", MICROSOFT_UI_LOCALE))
    if "ui_locales" not in existing:
        params.append(("ui_locales", MICROSOFT_UI_LOCALE))
    return urllib.parse.urlunsplit((
        parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(params), parsed.fragment
    ))


async def _click_microsoft_action(
    page, labels=MS_POSITIVE_ACTION_LABELS, negative_labels=MS_NEGATIVE_ACTION_LABELS,
    preferred_ids=(),
):
    """Click a visible Microsoft action using IDs first and localized text second."""
    try:
        return await page.evaluate(r"""({labels, negativeLabels, preferredIds}) => {
            const normalize = value => String(value || '')
                .replace(/\s+/g, ' ').trim().toLocaleLowerCase();
            const visible = el => {
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return !el.disabled && el.getAttribute('aria-disabled') !== 'true'
                    && style.display !== 'none' && style.visibility !== 'hidden'
                    && rect.width > 0 && rect.height > 0;
            };
            const textOf = el => normalize(
                el.value || el.innerText || el.textContent
                || el.getAttribute('aria-label') || el.getAttribute('title')
            );
            const blocked = text => negativeLabels.some(label =>
                text === label || text.startsWith(label + ' ')
            );
            for (const id of preferredIds) {
                const el = document.getElementById(id);
                if (el && visible(el) && !blocked(textOf(el))) {
                    el.click();
                    return textOf(el) || '#' + id;
                }
            }
            const controls = document.querySelectorAll(
                'button, a[role="button"], input[type="submit"], '
                + 'input[type="button"], [role="button"]'
            );
            for (const el of controls) {
                if (!visible(el)) continue;
                const text = textOf(el);
                if (!text || blocked(text)) continue;
                if (labels.some(label => text === label || text.startsWith(label + ' '))) {
                    el.click();
                    return text;
                }
            }
            const positiveIdentifiers = new Set([
                'accept', 'allow', 'approve', 'consent', 'continue',
                'next', 'yes', 'confirm', 'primary'
            ]);
            const negativeIdentifiers = new Set([
                'deny', 'decline', 'reject', 'cancel', 'no', 'back', 'secondary'
            ]);
            for (const el of controls) {
                if (!visible(el)) continue;
                const semantic = normalize([
                    el.id, el.getAttribute('name'), el.getAttribute('data-testid'),
                    el.getAttribute('data-test-id'), el.getAttribute('aria-label')
                ].filter(Boolean).join(' '));
                const tokens = semantic.split(/[^a-z0-9]+/).filter(Boolean);
                if (tokens.some(token => negativeIdentifiers.has(token))) continue;
                if (tokens.some(token => positiveIdentifiers.has(token))) {
                    el.click();
                    return semantic || textOf(el);
                }
            }
            return '';
        }""", {
            "labels": [label.lower() for label in labels],
            "negativeLabels": [label.lower() for label in negative_labels],
            "preferredIds": list(preferred_ids),
        })
    except Exception:
        return ""


def _birthdate_field_kind(metadata):
    """Classify a birth-date field from stable metadata across UI locales."""
    value = " ".join(str(metadata.get(key) or "") for key in (
        "id", "name", "ariaLabel", "placeholder", "text"
    )).lower()
    stable_tokens = (
        ("month", ("birthmonth",)),
        ("year", ("birthyear",)),
        ("country", ("country", "region")),
        ("day", ("birthday", "birthdate")),
    )
    for kind, kind_tokens in stable_tokens:
        if any(token in value for token in kind_tokens):
            return kind
    tokens = {
        "month": ("birthmonth", "month", "mois", "mes", "monat", "mês", "mese",
                  "maand", "miesiąc", "месяц", "月", "월"),
        "day": ("birthday", "birthdate", "day", "jour", "día", "dia", "tag",
                "giorno", "dag", "dzień", "день", "gün", "日", "일"),
        "year": ("birthyear", "year", "année", "año", "ano", "jahr", "anno",
                 "jaar", "rok", "год", "yıl", "年", "년"),
        "country": ("country", "region", "pays", "país", "land", "paese",
                    "kraj", "страна", "ülke", "国家", "國家", "国", "국가"),
    }
    if value.strip() == "ay":
        return "month"
    # Stable IDs such as BirthMonthDropdown contain "birthday" as a substring.
    for kind in ("month", "year", "country", "day"):
        if any(token in value for token in tokens[kind]):
            return kind
    return ""


async def _select_birthdate_combo_option(page, combo, value):
    """Select a numeric month/day option without relying on translated names."""
    await combo.click(force=True)
    await asyncio.sleep(0.5)
    try:
        clicked = await page.evaluate(r"""value => {
            const visible = el => {
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0
                    && style.display !== 'none' && style.visibility !== 'hidden'
                    && el.getAttribute('aria-disabled') !== 'true';
            };
            const options = [...document.querySelectorAll(
                '[role="option"], [role="menuitemradio"], option'
            )].filter(visible);
            const numericValue = el => {
                const candidates = [
                    el.getAttribute('data-value'), el.getAttribute('value'),
                    el.getAttribute('aria-label'), el.textContent
                ];
                for (const candidate of candidates) {
                    const match = String(candidate || '').trim().match(/^(\d{1,2})(?:\D.*)?$/);
                    if (match) return Number(match[1]);
                }
                return null;
            };
            let target = options.find(option => numericValue(option) === Number(value));
            if (!target && options.length >= 12) {
                // Text-only month lists still have a stable chronological order.
                target = options[Number(value) - 1];
            }
            if (!target) return false;
            target.click();
            return true;
        }""", int(value))
    except Exception:
        clicked = False
    if not clicked:
        await page.keyboard.type(str(value))
        await page.keyboard.press("Enter")
    await asyncio.sleep(0.5)
    return True


async def _select_native_numeric_option(select, value):
    """Select month/day from a native select with localized option labels."""
    for candidate in (str(value), f"{value:02d}"):
        try:
            await select.select_option(value=candidate)
            return True
        except Exception:
            pass
    try:
        options = select.locator("option")
        count = await options.count()
        for index in range(count):
            option = options.nth(index)
            option_value = (await option.get_attribute("value") or "").strip()
            option_text = (await option.text_content() or "").strip()
            for candidate in (option_value, option_text):
                match = re.match(r"^(\d{1,2})(?:\D.*)?$", candidate)
                if match and int(match.group(1)) == value:
                    await select.select_option(index=index)
                    return True
        # Localized month names retain chronological order. A 13th item is
        # normally the placeholder; a 12-item list starts directly at January.
        if count >= 13 and value <= 12:
            await select.select_option(index=value)
            return True
        if count >= 12 and value <= 12:
            await select.select_option(index=value - 1)
            return True
    except Exception:
        pass
    return False


async def _signup_email_rejected(page, email_input):
    """Detect a rejected alias from field validity/ARIA before localized text."""
    try:
        if await email_input.count() == 0:
            return ""
        state = await email_input.evaluate(r"""el => {
            const described = (el.getAttribute('aria-describedby') || '')
                .split(/\s+/).filter(Boolean)
                .map(id => document.getElementById(id)?.innerText || '').join(' ');
            const nearby = el.closest('form, [role="main"], main')?.innerText || '';
            return {
                invalid: el.getAttribute('aria-invalid') === 'true'
                    || (el.validity && !el.validity.valid),
                message: [el.validationMessage, described, nearby].join(' ').toLowerCase(),
                visible: !!el.offsetParent,
            };
        }""", timeout=1000)
    except Exception:
        state = {}
    if state.get("invalid"):
        return "format"
    # Remaining on the alias field after submit normally means the alias was rejected.
    if state.get("visible"):
        message = state.get("message") or ""
        format_markers = (
            "valid", "format", "letter", "caractère", "formato", "gültig",
            "válido", "valido", "corretto", "geldig", "prawidł", "допустим",
            "geçerli", "格式", "有效", "文字", "올바른",
        )
        if any(marker in message for marker in format_markers):
            return "format"
        return "taken"
    return ""


def _microsoft_direct_session():
    """Exchange OAuth codes directly; signup proxy state must not affect Graph."""
    session = requests.Session()
    session.trust_env = False
    session.proxies = {"http": None, "https": None}
    return session


def _request_graph_device_code():
    session = _microsoft_direct_session()
    try:
        response = session.post(
            GRAPH_DEVICE_CODE_URL,
            data={"client_id": GRAPH_CLIENT_ID, "scope": GRAPH_SCOPE},
            timeout=30,
        )
        data = response.json()
    except Exception as e:
        print(f"  [graph] device-code request error: {str(e)[:120]}")
        return None
    finally:
        session.close()
    if response.status_code == 200 and data.get("device_code") and data.get("user_code"):
        return data
    error = data.get("error_description") or data.get("error") or "unknown error"
    print(f"  [graph] device-code request failed: {str(error)[:140]}")
    return None


def _exchange_graph_device_code(device_code):
    """Perform one non-blocking device-code token poll."""
    session = _microsoft_direct_session()
    try:
        response = session.post(
            GRAPH_TOKEN_URL,
            data={
                "client_id": GRAPH_CLIENT_ID,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
            },
            timeout=30,
        )
        data = response.json()
    except Exception as e:
        return "retry", {"error_description": str(e)}
    finally:
        session.close()
    if response.status_code == 200 and data.get("access_token"):
        data["client_id"] = GRAPH_CLIENT_ID
        return "ready", data
    error = data.get("error") or ""
    if error in {"authorization_pending", "slow_down"}:
        return "pending", data
    return "failed", data


async def _skip_optional_recovery_email(page, idx=0):
    """Skip optional recovery/alternate-email enrollment in any common locale."""
    tag = f"[#{idx}]"
    try:
        body = " ".join(
            (await page.locator("body").inner_text(timeout=3000)).split()
        ).lower()
    except Exception:
        return False, False
    try:
        current_url = (page.url or "").lower()
    except Exception:
        current_url = ""
    page_markers = (
        "recovery email", "alternate email", "add an email address",
        "security info", "help us protect your account",
        "adresse e-mail de récupération", "informations de sécurité",
        "correo de recuperación", "información de seguridad",
        "wiederherstellungs-e-mail", "sicherheitsinformationen",
        "email de recuperação", "informações de segurança",
        "email di recupero", "informazioni di sicurezza",
        "herstel-e-mailadres", "beveiligingsgegevens",
        "adres e-mail odzyskiwania", "informacje zabezpieczające",
        "резервный адрес электронной почты", "сведения для защиты",
        "kurtarma e-postası", "güvenlik bilgileri",
        "辅助邮箱", "恢复邮箱", "备用邮箱", "添加电子邮件",
        "帮助我们保护你的帐户", "協助我們保護您的帳戶",
        "復原電子郵件", "備用電子郵件", "セキュリティ情報",
        "回復用メール", "보안 정보", "복구 이메일",
    )
    detected = "/proofs/add" in current_url or any(marker in body for marker in page_markers)
    if not detected:
        return False, False
    clicked = await _click_microsoft_action(
        page, labels=MS_SKIP_ACTION_LABELS, negative_labels=(),
        preferred_ids=("iShowSkip", "idBtn_Skip", "skipBtn", "Skip"),
    )
    if clicked:
        print(f"  {tag} [graph] skipped optional recovery email")
        await asyncio.sleep(2)
        return True, True
    return True, False


async def _skip_optional_passkey(page, idx=0):
    """Cancel optional Microsoft passkey enrollment without opening native UI."""
    tag = f"[#{idx}]"
    try:
        body = " ".join(
            (await page.locator("body").inner_text(timeout=3000)).split()
        ).lower()
    except Exception:
        return False, False
    try:
        current_url = (page.url or "").lower()
    except Exception:
        current_url = ""
    markers = (
        "setting up your passkey", "set up a passkey", "create a passkey",
        "passkey", "security key", "clé d’accès", "clave de acceso",
        "passschlüssel", "chave de acesso", "chiave di accesso",
        "wachtwoordsleutel", "klucz dostępu", "ключ доступа", "geçiş anahtarı",
        "通行密钥", "密碼金鑰", "パスキー", "패스키",
    )
    detected = "passkey" in current_url or any(marker in body for marker in markers)
    if not detected:
        return False, False
    clicked = await _click_microsoft_action(
        page, labels=("cancel", "annuler", "cancelar", "abbrechen", "annulla",
                           "annuleren", "anuluj", "отмена", "iptal", "取消",
                           "キャンセル", "취소") + MS_SKIP_ACTION_LABELS,
        negative_labels=(),
        preferred_ids=("idBtn_Back", "iCancel", "cancelBtn", "skipBtn"),
    )
    if clicked:
        print(f"  {tag} [graph] skipped optional passkey setup")
        await asyncio.sleep(2)
        return True, True
    return True, False


async def _accept_microsoft_app_consent(page, idx=0):
    """Accept the Device Code app-consent interstitial without selecting Deny."""
    tag = f"[#{idx}]"
    try:
        current_url = (page.url or "").lower()
    except Exception:
        current_url = ""
    try:
        body = " ".join(
            (await page.locator("body").inner_text(timeout=3000)).split()
        ).lower()
    except Exception:
        body = ""
    detected = (
        "/consent/update" in current_url
        or "let this app access your info" in body
        or "needs your permission" in body
        or "允许此应用访问你的信息" in body
        or "讓此應用程式存取您的資訊" in body
        or "このアプリが情報にアクセスすることを許可" in body
        or "이 앱이 사용자 정보에 액세스하도록 허용" in body
        or "autoriser cette application à accéder" in body
        or "permitir que esta aplicación acceda" in body
        or "dieser app den zugriff" in body
        or "permitir que este aplicativo acesse" in body
        or "consenti a questa app di accedere" in body
        or "deze app toegang geven" in body
        or "zezwolić tej aplikacji na dostęp" in body
        or "разрешить этому приложению доступ" in body
        or "bu uygulamanın bilgilerinize erişmesine izin ver" in body
        or "هل تريد السماح لهذا التطبيق" in body
        or "يحتاج thunderbird للحصول على إذنك" in body
    )
    if not detected:
        return False, False
    clicked = await _click_microsoft_action(
        page, preferred_ids=("idBtn_Accept", "acceptButton", "iAgree", "idSIButton9"),
    )
    if clicked:
        print(f"  {tag} [graph] accepted Microsoft app consent")
        await asyncio.sleep(2)
        return True, True
    print(f"  {tag} [graph] app consent detected; Accept control not ready")
    return True, False


async def _handle_microsoft_kmsi(page, idx=0):
    """Continue through Microsoft's localized Keep Me Signed In prompt."""
    tag = f"[#{idx}]"
    try:
        current_url = (page.url or "").lower()
    except Exception:
        current_url = ""
    try:
        body = " ".join(
            (await page.locator("body").inner_text(timeout=3000)).split()
        ).lower()
    except Exception:
        body = ""
    markers = (
        "stay signed in", "keep me signed in",
        "\u8981\u4fdd\u6301\u767b\u5165\u55ce",  # Traditional: keep signed in?
        "\u4fdd\u6301\u767b\u5165",              # Traditional: keep signed in
        "\u662f\u5426\u4fdd\u6301\u767b\u5f55",  # Simplified: keep signed in?
        "\u4fdd\u6301\u767b\u5f55",              # Simplified: keep signed in
        "rester connecté", "mantener la sesión iniciada",
        "angemeldet bleiben", "manter-me conectado",
        "rimanere connessi", "aangemeld blijven",
        "nie wylogowuj mnie", "оставаться в системе",
        "oturumunuz açık kalsın",
        "هل تريد أن يظل دخولك مسجلاً", "هل تريد البقاء قيد تسجيل الدخول",
        "保持登录状态", "保持登入狀態", "サインインしたままにする",
        "로그인 상태를 유지",
    )
    detected = "/kmsi" in current_url or any(marker in body for marker in markers)
    if not detected:
        return False, False
    clicked = await _click_microsoft_action(
        page, preferred_ids=("idSIButton9", "acceptButton", "iNext"),
    )
    if clicked:
        print(f"  {tag} [graph] continued past stay-signed-in prompt")
        await asyncio.sleep(2)
        return True, True
    print(f"  {tag} [graph] stay-signed-in prompt detected; action not ready")
    return True, False


async def _extract_graph_token_device(page, email, password, idx=0):
    """Authorize Graph by device code in the live Microsoft browser session."""
    tag = f"[#{idx}]"
    device = await asyncio.to_thread(_request_graph_device_code)
    if not device:
        return False
    verification_uri = _microsoft_url_with_locale(
        device.get("verification_uri") or "https://www.microsoft.com/link"
    )
    try:
        await page.context.add_init_script("""(() => {
            try {
                Object.defineProperty(CredentialsContainer.prototype, 'create', {
                    configurable: true,
                    value: () => Promise.reject(
                        new DOMException('Optional passkey enrollment disabled', 'NotAllowedError')
                    )
                });
            } catch (e) {}
        })();""")
    except Exception:
        pass
    try:
        await page.goto(verification_uri, timeout=45000, wait_until="domcontentloaded")
    except Exception as e:
        print(f"  {tag} [graph] device page navigation warning: {str(e)[:100]}")

    code_submitted = False
    idle_rounds = 0
    for _ in range(30):
        state, token_data = await asyncio.to_thread(
            _exchange_graph_device_code, device["device_code"]
        )
        if state == "ready":
            print(f"  {tag} [graph] device authorization OK! refresh_token="
                  f"{'yes' if token_data.get('refresh_token') else 'no'}")
            return token_data
        if state == "failed":
            error = token_data.get("error_description") or token_data.get("error") or ""
            print(f"  {tag} [graph] device authorization failed: {str(error)[:150]}")
            return None

        kmsi_page, kmsi_accepted = await _handle_microsoft_kmsi(page, idx)
        consent_page = consent_accepted = False
        if not kmsi_page:
            consent_page, consent_accepted = await _accept_microsoft_app_consent(
                page, idx
            )
        passkey_page = passkey_skipped = False
        if not kmsi_page and not consent_page:
            passkey_page, passkey_skipped = await _skip_optional_passkey(page, idx)
        recovery_page = recovery_skipped = False
        if not kmsi_page and not consent_page and not passkey_page:
            recovery_page, recovery_skipped = await _skip_optional_recovery_email(
                page, idx
            )
        acted = kmsi_accepted or consent_accepted or passkey_skipped or recovery_skipped
        optional_setup_page = kmsi_page or consent_page or passkey_page or recovery_page
        if not acted and not optional_setup_page:
            email_input = page.locator('input[type="email"], input[name="loginfmt"]').first
            try:
                if await email_input.count() > 0 and await email_input.is_visible():
                    await email_input.fill(email)
                    await page.keyboard.press("Enter")
                    acted = True
            except Exception:
                pass

        if not acted and not optional_setup_page:
            password_input = page.locator('input[type="password"]').first
            try:
                if await password_input.count() > 0 and await password_input.is_visible():
                    await password_input.fill(password)
                    await page.keyboard.press("Enter")
                    acted = True
            except Exception:
                pass

        if not acted and not optional_setup_page and not code_submitted:
            for selector in (
                'input[name="otc"]', '#otc', 'input[autocomplete="one-time-code"]',
                'input[placeholder*="code" i]', 'input[type="text"]',
            ):
                code_input = page.locator(selector).first
                try:
                    if await code_input.count() > 0 and await code_input.is_visible():
                        await code_input.fill(device["user_code"])
                        await page.keyboard.press("Enter")
                        code_submitted = True
                        acted = True
                        print(f"  {tag} [graph] device code submitted")
                        break
                except Exception:
                    pass

        if not acted and not optional_setup_page:
            try:
                account = page.get_by_text(email, exact=False).first
                if await account.count() > 0 and await account.is_visible():
                    await account.click(timeout=3000)
                    acted = True
            except Exception:
                pass

        if not acted and not optional_setup_page:
            clicked = await _click_microsoft_action(
                page, preferred_ids=("idSIButton9", "idBtn_Accept", "iNext", "acceptButton"),
            )
            acted = bool(clicked)

        idle_rounds = 0 if acted else idle_rounds + 1
        if idle_rounds >= 3:
            try:
                body = " ".join(
                    (await page.locator("body").inner_text(timeout=3000)).split()
                )
            except Exception:
                body = ""
            if body:
                print(f"  {tag} [graph] device page waiting: {body[:180]}")
            idle_rounds = 0
        await asyncio.sleep(2)
    try:
        body = " ".join(
            (await page.locator("body").inner_text(timeout=3000)).split()
        )
    except Exception:
        body = ""
    print(f"  {tag} [graph] device authorization timed out: {body[:180]}")
    try:
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        await page.screenshot(
            path=f"{SCREENSHOT_DIR}/outlook_{idx}_graph_device_timeout.png"
        )
    except Exception:
        pass
    return None


def extract_graph_token_http(email, password, idx=0, attempts=3):
    """Extract Graph refresh_token through the shared pure-HTTP OAuth flow."""
    try:
        from extract_graph_tokens import get_graph_token
    except Exception as exc:
        print(f"  [#{idx}] [graph] import error: {exc}")
        return None

    for attempt in range(attempts):
        try:
            res = get_graph_token(email, password, idx)
        except Exception as exc:
            print(f"  [#{idx}] [graph] attempt {attempt + 1}/{attempts} error: {exc}")
            res = None
        if res and res.get("refresh_token"):
            return {
                "refresh_token": res["refresh_token"],
                "client_id": res.get("client_id") or "",
            }
        if attempt < attempts - 1:
            print(f"  [#{idx}] [graph] no refresh_token, retrying...")
            time.sleep(3 * (attempt + 1))
    return None


async def _extract_graph_token_authorization_code(page, context, email, password, idx=0):
    """Extract Microsoft Graph API refresh_token after registration.
    Uses OAuth2 authorization code flow with a native client (no secret needed).
    Uses 'consumers' tenant for personal Microsoft accounts (Outlook.com).
    Returns dict with access_token, refresh_token, or None on failure.
    """
    tag = f"[#{idx}]"
    try:
        auth_url = (
            f"https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
            f"?client_id={GRAPH_CLIENT_ID}"
            f"&response_type=code"
            f"&redirect_uri={urllib.parse.quote(GRAPH_REDIRECT_URI, safe='')}"
            f"&scope={urllib.parse.quote(GRAPH_SCOPE)}"
            f"&prompt=consent"
        )
        auth_url = _microsoft_url_with_locale(auth_url)
        print(f"  {tag} [graph] navigating to OAuth consent...")
        try:
            await page.goto(auth_url, timeout=30000, wait_until="domcontentloaded")
        except Exception:
            # A desktop OAuth callback has no local listener. Chromium reports
            # connection refused while preserving the code in page.url.
            if "code=" not in page.url and "error=" not in page.url:
                raise
        await asyncio.sleep(3)

        # May need to click accept/consent buttons
        for attempt in range(15):
            current_url = page.url
            # Check if redirected with auth code
            if GRAPH_REDIRECT_URI in current_url and "code=" in current_url:
                break

            # Login if needed (shouldn't be since we just registered)
            email_input = page.locator('input[type="email"], input[name="loginfmt"]').first
            try:
                if await email_input.count() > 0 and await email_input.is_visible():
                    await email_input.fill(email)
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(3)
                    continue
            except Exception:
                pass

            pwd_input = page.locator('input[type="password"]').first
            try:
                if await pwd_input.count() > 0 and await pwd_input.is_visible():
                    await pwd_input.fill(password)
                    await page.keyboard.press("Enter")
                    await asyncio.sleep(3)
                    continue
            except Exception:
                pass

            clicked = await _click_microsoft_action(
                page, preferred_ids=("idBtn_Accept", "idSIButton9", "acceptButton", "iAgree"),
            )
            if clicked:
                print(f"  {tag} [graph] clicked: {clicked}")
                await asyncio.sleep(3)

            await asyncio.sleep(2)

        current_url = page.url
        if "code=" not in current_url:
            parsed_error = urllib.parse.parse_qs(
                urllib.parse.urlparse(current_url).query
            )
            oauth_error = parsed_error.get("error_description", [""])[0]
            if oauth_error:
                print(f"  {tag} [graph] OAuth error: {oauth_error[:160]}")
            else:
                try:
                    body = " ".join(
                        (await page.locator("body").inner_text(timeout=3000)).split()
                    )
                except Exception:
                    body = ""
                print(f"  {tag} [graph] no auth code: {body[:160] or current_url[:120]}")
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
        token_session = _microsoft_direct_session()
        try:
            token_resp = token_session.post(
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
        finally:
            token_session.close()
        token_data = token_resp.json()

        if "access_token" in token_data:
            print(f"  {tag} [graph] OK! refresh_token={('yes' if token_data.get('refresh_token') else 'no')}")
            return {
                "access_token": token_data["access_token"],
                "refresh_token": token_data.get("refresh_token"),
                "expires_in": token_data.get("expires_in"),
                "client_id": GRAPH_CLIENT_ID,
            }
        else:
            print(f"  {tag} [graph] token error: {token_data.get('error_description', token_data.get('error', '?'))[:100]}")
            return None

    except Exception as e:
        print(f"  {tag} [graph] error: {e}")
        return None


async def extract_graph_token(page, context, email, password, idx=0):
    """Prefer device-code OAuth; retain authorization-code flow as fallback."""
    result = await _extract_graph_token_device(page, email, password, idx)
    if result and result.get("refresh_token"):
        return result
    if result is not False:
        return None
    print(f"  [#{idx}] [graph] device flow unavailable; trying authorization-code fallback")
    return await _extract_graph_token_authorization_code(
        page, context, email, password, idx
    )


# ======================== Outlook Registration ========================


def _env_truthy(name, default="0"):
    return (os.environ.get(name, default) or "").strip().lower() in {"1", "true", "yes", "on"}


async def _maybe_confirm_before_register(page, tag, captcha_early_abort=False):
    """Auto-click a confirmation/consent gate shown before the signup form."""
    if captcha_early_abort or not _env_truthy("OUTLOOK_CONFIRM_BEFORE_REGISTER"):
        return
    try:
        title = await page.title()
    except Exception:
        title = ""
    print(f"  {tag} signup page opened: {page.url}")
    if title:
        print(f"  {tag} page title: {title[:100]}")

    # 只有真出现「数据确认/许可」页时才点允许接受；正常直接进注册表单的不点，
    # 否则会误点页面上别的 OK/确定链接(如 cookie 条、页脚)打乱流程。
    # 先按内容判定：body 文本含数据许可关键词、或已在 privacynotice 页，才继续找按钮。
    try:
        page_text = await page.evaluate("() => document.body.innerText")
    except Exception:
        page_text = ""
    ptl = (page_text or "").lower()
    curl = (page.url or "").lower()
    consent_hit = (
        any(kw in page_text for kw in ["同意并继续", "个人数据", "数据导出", "数据确认",
                                       "資料", "個人資料", "同意並繼續"])
        or any(kw in ptl for kw in ["agree and continue", "consent", "data export",
                                    "accepter et continuer", "consentement",
                                    "your data", "personal data",
                                    "aceptar y continuar", "consentimiento", "datos personales",
                                    "akzeptieren und fortfahren", "einwilligung", "personenbezogene daten",
                                    "aceitar e continuar", "consentimento", "dados pessoais",
                                    "accetta e continua", "consenso", "dati personali",
                                    "accepteren en doorgaan", "toestemming", "persoonsgegevens",
                                    "zaakceptuj i kontynuuj", "zgoda", "dane osobowe",
                                    "принять и продолжить", "согласие", "личные данные",
                                    "kabul et ve devam et", "onay", "kişisel veriler"])
        or "privacynotice" in curl
    )
    if not consent_hit:
        print(f"  {tag} auto-confirm: no data-consent gate, skip (进正常表单)")
        return

    for _ in range(3):
        clicked = await _click_microsoft_action(
            page, preferred_ids=("acceptButton", "iAgree", "idBtn_Accept", "iNext"),
        )
        if clicked:
            print(f"  {tag} auto-confirm clicked: {clicked}")
            await asyncio.sleep(2)
            return
        await asyncio.sleep(1)
    print(f"  {tag} auto-confirm: no confirmation button found")
    return


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
        signup_url = _microsoft_url_with_locale("https://signup.live.com/signup?lic=1")
        await page.goto(signup_url, timeout=60000, wait_until="domcontentloaded")
        await asyncio.sleep(3)
        await page.screenshot(path=f"{SCREENSHOT_DIR}/outlook_{idx}_start.png")
        await _maybe_confirm_before_register(page, tag, captcha_early_abort)

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
                    "accepter et continuer", "consentement", "aceptar y continuar",
                    "akzeptieren und fortfahren", "aceitar e continuar",
                    "accetta e continua", "accepteren en doorgaan",
                    "zaakceptuj i kontynuuj", "принять и продолжить",
                    "kabul et ve devam et",
                ]) or "privacynotice" in current_url
            ):
                print(f"  {tag} privacy/consent page detected, clicking accept...")
                clicked = await _click_microsoft_action(
                    page, preferred_ids=("acceptButton", "iAgree", "idBtn_Accept", "iNext"),
                )
                if clicked:
                    print(f"  {tag} clicked consent: {clicked}")
                else:
                    print(f"  {tag} consent action not ready")
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
            rejection = await _signup_email_rejected(page, email_input)

            if rejection == "taken" or ("already" in page_lower and "email" in page_lower) or "taken" in page_lower:
                prefix = random.choice(string.ascii_lowercase) + "".join(
                    random.choices(string.ascii_lowercase + string.digits, k=11)
                )
                email = f"{prefix}@outlook.com"
                print(f"  {tag} email taken, retry: {email}")
                continue

            if rejection == "format" or "needs to start" in page_lower or "in the format" in page_lower or "enter a valid" in page_lower or "use letters" in page_lower:
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

            clicked_next = await _click_microsoft_action(
                page, preferred_ids=("iSignupAction", "iNext", "idSIButton9"),
            )
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

        # Wait for the structural birth-date controls; labels vary by exit locale.
        for _ in range(10):
            birth_controls = page.locator(
                '[id*="Birth" i], [name*="Birth" i], '
                + 'select, [role="combobox"], input[type="number"]'
            )
            if await birth_controls.count() >= 2:
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
            # Traditional <select> dropdowns, classified by stable metadata.
            for select_index in range(select_count):
                select = all_selects.nth(select_index)
                metadata = {
                    "id": await select.get_attribute("id") or "",
                    "name": await select.get_attribute("name") or "",
                    "ariaLabel": await select.get_attribute("aria-label") or "",
                    "text": (await select.text_content() or "")[:80],
                }
                kind = _birthdate_field_kind(metadata)
                if not kind:
                    inferred = ("country", "month", "day") if select_count >= 3 else ("month", "day")
                    kind = inferred[select_index] if select_index < len(inferred) else ""
                if kind == "country":
                    try:
                        await select.select_option("US")
                        print(f"  {tag} country: US")
                    except Exception:
                        pass
                elif kind == "month":
                    await _select_native_numeric_option(select, month)
                elif kind == "day":
                    await _select_native_numeric_option(select, day)

            year_input = page.locator(
                'input[id*="BirthYear" i], input[name*="BirthYear" i], '
                'input[id*="Year" i], input[name*="Year" i], input[type="number"]'
            ).first
            if await year_input.count() > 0:
                await year_input.fill(str(year))
        else:
            # New UI with combobox/dropdown.
            print(f"  {tag} no <select>, trying new UI (combobox)...")

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
                    combo_name = (await combo.get_attribute("name") or "").lower()
                    combo_placeholder = (await combo.get_attribute("placeholder") or "").lower()
                    info = f"text='{combo_text}' label='{combo_label}' id='{combo_id}'"
                    print(f"  {tag} combo[{ci}]: {info}")

                    kind = _birthdate_field_kind({
                        "id": combo_id, "name": combo_name, "ariaLabel": combo_label,
                        "placeholder": combo_placeholder, "text": combo_text,
                    })
                    if not kind:
                        inferred = ("country", "month", "day") if combo_count >= 3 else ("month", "day")
                        kind = inferred[ci] if ci < len(inferred) else ""

                    if kind == "month" and not month_filled:
                        await _select_birthdate_combo_option(page, combo, month)
                        month_filled = True
                        print(f"  {tag} month: {month}")
                    elif kind == "day" and not day_filled:
                        await _select_birthdate_combo_option(page, combo, day)
                        day_filled = True
                        print(f"  {tag} day: {day}")
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
        clicked = await _click_microsoft_action(
            page, preferred_ids=("iSignupAction", "iNext", "idSIButton9"),
        )
        if clicked:
            print(f"  {tag} clicked next (bday): {clicked}")
        await asyncio.sleep(3)
        await page.screenshot(path=f"{SCREENSHOT_DIR}/outlook_{idx}_after_bday.png")

        # Step 4: Optional Username/Gamertag. Stable field attributes are locale-free.
        await asyncio.sleep(2)
        username_input = page.locator(
            'input[id*="displayName" i], input[id*="gamertag" i], '
            'input[name*="displayName" i], input[name*="gamertag" i]'
        ).first
        if await username_input.count() > 0:
            username = prefix[:8] + str(random.randint(100, 999))
            await username_input.fill(username)
            print(f"  {tag} username: {username}")
            await asyncio.sleep(0.5)
            await _click_microsoft_action(
                page, preferred_ids=("iSignupAction", "iNext", "idSIButton9"),
            )
            await asyncio.sleep(3)

        # Step 5: First/Last Name + checkbox (Chinese: 姓/名)
        first_name, last_name = generate_name()
        await asyncio.sleep(2)

        for _ in range(10):
            fname_input = page.locator(
                'input[name="FirstName"], input[id="FirstName"], input[name="firstNameInput"], '
                'input[id="firstNameInput"], input[aria-label*="first" i], input[placeholder*="first" i], '
                'input[aria-label*="名" i], input[placeholder*="名" i], '
                'input[aria-label*="prénom" i], input[placeholder*="prénom" i], '
                'input[aria-label*="nombre" i], input[placeholder*="nombre" i], '
                'input[aria-label*="vorname" i], input[placeholder*="vorname" i], '
                'input[aria-label*="nome" i], input[placeholder*="nome" i]'
            ).first
            lname_input = page.locator(
                'input[name="LastName"], input[id="LastName"], input[name="lastNameInput"], '
                'input[id="lastNameInput"], input[aria-label*="last" i], input[aria-label*="surname" i], '
                'input[placeholder*="last" i], input[aria-label*="姓" i], input[placeholder*="姓" i], '
                'input[aria-label*="nom de famille" i], input[placeholder*="nom de famille" i], '
                'input[aria-label*="apellido" i], input[placeholder*="apellido" i], '
                'input[aria-label*="nachname" i], input[placeholder*="nachname" i], '
                'input[aria-label*="cognome" i], input[placeholder*="cognome" i]'
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
        clicked = await _click_microsoft_action(
            page, preferred_ids=("iSignupAction", "iNext", "idSIButton9"),
        )
        if clicked:
            print(f"  {tag} clicked next (name): {clicked}")
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

            # Account blocked detection across common Microsoft markets.
            if any(kw in page_text for kw in [
                "帐户创建已被阻止", "已被阻止", "阻止创建",
                "account creation has been blocked", "has been blocked", "account has been suspended",
                "création de compte a été bloquée", "a été bloquée", "bloquée",
                "creación de la cuenta se ha bloqueado", "cuenta bloqueada",
                "kontoerstellung wurde blockiert", "konto wurde gesperrt",
                "criação da conta foi bloqueada", "conta bloqueada",
                "creazione dell'account è stata bloccata", "account bloccato",
                "account maken is geblokkeerd", "account geblokkeerd",
                "tworzenie konta zostało zablokowane", "konto zablokowane",
                "создание учетной записи заблокировано", "учетная запись заблокирована",
                "hesap oluşturma engellendi", "hesap engellendi",
                "unusual activity", "异常活动", "activité inhabituelle",
            ]):
                print(f"  {tag} BLOCKED: account creation blocked by Microsoft")
                await page.screenshot(path=f"{SCREENSHOT_DIR}/outlook_{idx}_blocked.png")
                return None, None

            # FIDO/passkey - skip
            if "fido" in current_url or "passkey" in current_url:
                await _skip_optional_passkey(page, idx)
                await asyncio.sleep(3)
                continue

            # Privacy notice
            if "privacynotice" in current_url:
                await asyncio.sleep(2)
                await _click_microsoft_action(
                    page, preferred_ids=("acceptButton", "iAgree", "iNext", "idSIButton9"),
                )
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

                    # 拟人按住(WindMouse 逼近 + OU 生理震颤)，取代旧的贝塞尔逼近 +
                    # 正弦漂移。旧正弦是完全周期性运动，PerimeterX 行为模型秒判；这里的
                    # 轨迹变速 + 自相关抖动更像真人手。is_done 复用 _captcha_visible 取反：
                    # 进度条走满(按住按钮/iframe 消失)即松手，未满则按住到 max_hold 兜底。
                    async def _hold_done():
                        return not await _captcha_visible()

                    try:
                        held, passed_in_hold = await _hm.human_press_and_hold(
                            page, cx, cy, is_done=_hold_done,
                            max_hold=random.uniform(11.0, 15.0), min_hold=1.5,
                        )
                    except Exception as _he:
                        # 页面/context 已关闭(节点掉线或 captcha 过后导航销毁上下文)：
                        # 此时 page 已死，再 down/up 只会二次抛错。直接标记未过、交给外层
                        # 循环顶部的「captcha 消失=已通过 / URL 判定」去收尾，别在死页上乱按。
                        _msg = f"{type(_he).__name__}: {_he}"
                        print(f"  {tag} human_press_and_hold err: {_msg}")
                        if "closed" in _msg.lower() or "TargetClosed" in _msg:
                            print(f"  {tag} page/context 已关闭，跳过重按，交外层判定")
                            held, passed_in_hold = 0.0, False
                        else:
                            # 其它异常(非页面关闭)：退回最简按住兜底，仍防崩
                            try:
                                await page.mouse.down()
                                await asyncio.sleep(random.uniform(11.0, 14.0))
                                await page.mouse.up()
                            except Exception:
                                pass
                            held, passed_in_hold = 12.0, False
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
            await _click_microsoft_action(
                page, preferred_ids=("acceptButton", "iAgree", "iNext", "idSIButton9"),
            )
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
        "Accept-Language": f"{MICROSOFT_UI_LOCALE},en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    })

    try:
        print(f"  {tag} GET signup page...")
        signup_url = _microsoft_url_with_locale("https://signup.live.com/signup?lic=1")
        resp = session.get(
            signup_url,
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
        action_url = action_m.group(1) if action_m else f"{signup_url}&uaid={uaid}"

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
                "Referer": signup_url,
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


async def extract_graph_token_browser(bb, email, password, idx=0, proxy_str=None):
    """Authorize Graph in a disposable BitBrowser profile via Device Code."""
    tag = f"[#{idx}][graph-browser]"
    profile_id = None
    try:
        name = f"outlook_graph_{datetime.now().strftime('%m%d_%H%M%S')}_{idx}"
        for attempt in range(3):
            try:
                profile_id = bb.create_browser(name=name, proxy_str=proxy_str)
                break
            except Exception as e:
                if attempt >= 2:
                    raise
                print(f"  {tag} create retry {attempt + 1}/3: {str(e)[:80]}")
                await asyncio.sleep(3 + attempt)
        if not profile_id:
            return None

        info = bb.open_browser(profile_id)
        ws = info.get("ws", "")
        if not ws:
            print(f"  {tag} no WebSocket URL")
            return None
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(ws)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()
            return await extract_graph_token(page, context, email, password, idx)
    except Exception as e:
        print(f"  {tag} error: {str(e)[:140]}")
        return None
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

    graph = None
    if email:
        print(f"  {tag} [graph] extracting refresh_token via Device Code...")
        graph = await extract_graph_token_browser(
            bb, email, password, idx, proxy_str
        )
        if not graph or not graph.get("refresh_token"):
            print(f"  {tag} [graph] browser authorization failed; trying HTTP fallback")
            loop = asyncio.get_event_loop()
            graph = await loop.run_in_executor(
                None, extract_graph_token_http, email, password, idx
            )

    # ── Save result ───────────────────────────────────────────────
    async with results_lock:
        if email:
            if graph and graph.get("refresh_token"):
                results.append({
                    "index": idx, "email": email, "password": password,
                    "status": "OK", "proxy": proxy_str, "mode": used_mode,
                    "graph": graph,
                })
                if live_fh:
                    live_fh.write(
                        f"{email}----{password}----{graph['refresh_token']}----{graph.get('client_id', '')}\n"
                    )
                    live_fh.flush()
                print(f"  {tag} SUCCESS [{used_mode} +graph]: {email}")
            else:
                results.append({
                    "index": idx, "email": email, "password": password,
                    "status": "GRAPH_FAIL", "proxy": proxy_str, "mode": used_mode,
                })
                print(f"  {tag} REGISTERED but graph RT missing; not saved: {email}")
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
    parser.add_argument("--confirm-before-register", action="store_true",
                        help="Auto-click confirmation on the signup page before filling")
    args = parser.parse_args()

    global REGISTER_TIMEOUT, VERIFY_AFTER_REGISTER
    REGISTER_TIMEOUT = args.timeout
    VERIFY_AFTER_REGISTER = not args.no_verify
    if args.confirm_before_register:
        os.environ["OUTLOOK_CONFIRM_BEFORE_REGISTER"] = "1"
    proxy_env = ensure_clash_proxy_env()
    if proxy_env:
        print(f"  proxy env ready: {proxy_env}")

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
                    line += f"----{graph['refresh_token']}----{graph.get('client_id', '')}"
                    all_tokens.append({
                        "email": r['email'],
                        "password": r['password'],
                        "access_token": graph.get('access_token'),
                        "refresh_token": graph.get('refresh_token'),
                        "client_id": graph.get('client_id'),
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
