# -*- coding: utf-8 -*-
"""
common/session_export.py — 把注册成功后的登录态导出成下游工具认的"标准 token 格式"。

参考 FlowPilot(QLHazyCoder/FlowPilot)的实现:
  - ChatGPT: 抓 chatgpt.com/api/auth/session 拿 accessToken
             -> CPA codex 授权 JSON      (对齐 background/cpa-api.js: buildCpaSessionAuthJson)
             -> SUB2API 导入 content      (对齐 background/sub2api-api.js: buildCodexSessionImportContent)
  - Grok:    单个 sso cookie -> SUB2API Grok OAuth / webchat2api inject

落盘:
    tokens/chatgpt/<email>.session.json   原始 session(含 accessToken),上传时的通用源
    tokens/chatgpt/codex-<email>.json     CPA 标准授权 JSON
    tokens/grok/<email>.sso.json          {"email","sso","ts"}
"""

import base64
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    from config import TOKEN_OUTPUT_DIR
except Exception:
    TOKEN_OUTPUT_DIR = "tokens"

OPENAI_AUTH_CLAIM = "https://api.openai.com/auth"
OPENAI_PROFILE_CLAIM = "https://api.openai.com/profile"
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


# ============================================================ 小工具
def _s(value=""):
    """对齐 JS normalizeString。"""
    return str(value if value is not None else "").strip()


def _first_non_empty(*values):
    for v in values:
        n = _s(v)
        if n:
            return n
    return ""


def _email_or_empty(value=""):
    e = _s(value)
    return e if _EMAIL_RE.match(e) else ""


def _is_obj(value):
    return isinstance(value, dict)


def _b64url_decode(segment=""):
    """base64url 解码,自动补 '='。对齐 JS decodeBase64UrlSegment。"""
    seg = _s(segment)
    if not seg:
        return ""
    padded = seg + "=" * (-len(seg) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", "replace")
    except Exception:
        return ""


def _b64url_encode_json(value):
    """JSON -> base64url(无填充)。对齐 JS encodeBase64UrlJson。"""
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def parse_jwt_payload(token=""):
    """取 JWT 的 payload(第二段)。失败返回 None。对齐 JS parseJwtPayload。"""
    t = _s(token)
    if not t:
        return None
    parts = t.split(".")
    if len(parts) < 2:
        return None
    try:
        return json.loads(_b64url_decode(parts[1]))
    except Exception:
        return None


def _get_claim_section(payload, claim):
    if not _is_obj(payload):
        return {}
    section = payload.get(claim)
    return section if _is_obj(section) else {}


def _iso_from_unix_seconds(value):
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return ""
    try:
        return datetime.fromtimestamp(numeric, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return ""


def _iso_from_any(value):
    """把字符串/数字时间归一化成 ISO。对齐 JS normalizeTimestamp(够用即可)。"""
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        ms = value if value > 1e11 else value * 1000
        try:
            return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            return ""
    s = _s(value)
    if not s:
        return ""
    # ISO 串原样保留(下游能解析即可)
    return s


def _epoch_seconds(value):
    """对齐 JS epochSecondsFromValue。"""
    if value is None or value == "":
        return 0
    try:
        numeric = float(value)
        return int(numeric / 1000 if numeric > 1e11 else numeric)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(_s(value).replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        return 0


# ============================================================ CPA codex 授权 JSON
def _build_synthetic_codex_id_token(email, account_id, plan_type, user_id, expires_at):
    """没有真 id_token 时造一个合成 JWT。对齐 cpa-api.js buildSyntheticCodexIdToken。"""
    aid = _s(account_id)
    if not aid:
        return ""
    now = int(time.time())
    expires = _epoch_seconds(expires_at) or (now + 90 * 24 * 60 * 60)
    auth_info = {"chatgpt_account_id": aid}
    if plan_type:
        auth_info["chatgpt_plan_type"] = _s(plan_type)
    if user_id:
        auth_info["chatgpt_user_id"] = _s(user_id)
        auth_info["user_id"] = _s(user_id)
    payload = {"iat": now, "exp": expires, OPENAI_AUTH_CLAIM: auth_info}
    if email:
        payload["email"] = _s(email)
    header = _b64url_encode_json({"alg": "none", "typ": "JWT", "cpa_synthetic": True})
    return f"{header}.{_b64url_encode_json(payload)}.synthetic"


def _normalize_plan_for_filename(plan_type=""):
    parts = re.split(r"[^a-zA-Z0-9]+", _s(plan_type))
    return "-".join(p.strip().lower() for p in parts if p.strip())


def _sanitize_file_segment(value="", fallback="chatgpt-session"):
    n = _s(value)
    n = re.sub(r'[\\/:*?"<>|]+', "-", n)
    n = re.sub(r"\s+", "-", n)
    n = re.sub(r"-+", "-", n)
    n = n.strip("-")
    return n or fallback


def _build_cpa_auth_filename(email="", plan_type="", account_id=""):
    e = _sanitize_file_segment(email) if email else ""
    plan = _normalize_plan_for_filename(plan_type)
    aid = _sanitize_file_segment(account_id) if account_id else ""
    if e and plan:
        return f"codex-{e}-{plan}.json"
    if e:
        return f"codex-{e}.json"
    if aid and plan:
        return f"codex-{aid}-{plan}.json"
    if aid:
        return f"codex-{aid}.json"
    return f"codex-{int(time.time() * 1000)}.json"


def build_cpa_codex_json(session, email=""):
    """ChatGPT session -> CPA codex 授权 JSON。逐字段对齐 cpa-api.js buildCpaSessionAuthJson。
    返回 dict: {auth_json, account_id, email, expires_at, file_name, has_refresh_token}。
    无 accessToken 抛 ValueError。"""
    sess = session if _is_obj(session) else {}
    access_token = _s(sess.get("accessToken")) or _s(sess.get("access_token"))
    if not access_token:
        raise ValueError("未读取到可导入的 ChatGPT accessToken。")

    input_id_token = _first_non_empty(sess.get("idToken"), sess.get("id_token"))
    refresh_token = _first_non_empty(sess.get("refreshToken"), sess.get("refresh_token"))
    session_token = _first_non_empty(sess.get("sessionToken"), sess.get("session_token"))

    access_payload = parse_jwt_payload(access_token) or {}
    id_payload = parse_jwt_payload(input_id_token) or {}
    access_auth = _get_claim_section(access_payload, OPENAI_AUTH_CLAIM)
    id_auth = _get_claim_section(id_payload, OPENAI_AUTH_CLAIM)
    profile = _get_claim_section(access_payload, OPENAI_PROFILE_CLAIM)

    expires_at = _first_non_empty(
        _iso_from_unix_seconds(access_payload.get("exp")),
        _iso_from_any(sess.get("expires")),
        _iso_from_any(sess.get("expiresAt")),
        _iso_from_any(sess.get("expired")),
        _iso_from_any(sess.get("expires_at")),
    )

    user = sess.get("user") if _is_obj(sess.get("user")) else {}
    account = sess.get("account") if _is_obj(sess.get("account")) else {}
    email_val = _first_non_empty(
        _email_or_empty(user.get("email")),
        _email_or_empty(sess.get("email")),
        _email_or_empty(email),
        _email_or_empty(profile.get("email")),
        _email_or_empty(id_payload.get("email")),
        _email_or_empty(access_payload.get("email")),
    )
    account_id = _first_non_empty(
        account.get("id"), sess.get("account_id"),
        access_auth.get("chatgpt_account_id"), id_auth.get("chatgpt_account_id"),
    )
    user_id = _first_non_empty(
        user.get("id"), sess.get("user_id"),
        access_auth.get("chatgpt_user_id"), access_auth.get("user_id"),
        id_auth.get("chatgpt_user_id"), id_auth.get("user_id"),
    )
    plan_type = _first_non_empty(
        account.get("planType"), account.get("plan_type"),
        sess.get("planType"), sess.get("plan_type"),
        access_auth.get("chatgpt_plan_type"), id_auth.get("chatgpt_plan_type"),
    )

    exported_at = datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")
    synthetic_id_token = "" if input_id_token else _build_synthetic_codex_id_token(
        email_val, account_id, plan_type, user_id, expires_at
    )
    id_token = input_id_token or synthetic_id_token

    raw = {
        "type": "codex",
        "account_id": account_id,
        "chatgpt_account_id": account_id,
        "email": email_val,
        "name": _first_non_empty(email_val, email, "ChatGPT Account"),
        "plan_type": plan_type,
        "chatgpt_plan_type": plan_type,
        "id_token": id_token,
        "id_token_synthetic": True if synthetic_id_token else None,
        "access_token": access_token,
        "refresh_token": refresh_token or "",
        "session_token": session_token,
        "last_refresh": exported_at,
        "expired": expires_at,
        "disabled": True if sess.get("disabled") is True else None,
    }
    # 剔除 None/""(对齐 JS 的 .filter(value !== undefined && !== null && !== ''))
    auth_json = {k: v for k, v in raw.items() if v is not None and v != ""}

    return {
        "auth_json": auth_json,
        "account_id": account_id,
        "email": email_val,
        "expires_at": expires_at,
        "file_name": _build_cpa_auth_filename(email_val, plan_type, account_id),
        "has_refresh_token": bool(refresh_token),
    }


def build_cpa_codex_json_from_oauth(cred, email=""):
    """Codex OAuth 凭据(common/oauth_codex.build_oauth_credentials 的产物) -> CPA codex 授权 JSON。
    与网页 session 版的区别:这里带**真 refresh_token + 真 id_token**(非合成)。
    把 OAuth 凭据字段映射成 session 形状后复用 build_cpa_codex_json,字段口径仍对齐 cpa-api.js。
    cred 关键字段: access_token/refresh_token/id_token/expires_at/email/
                   chatgpt_account_id/chatgpt_user_id/plan_type。"""
    c = cred if _is_obj(cred) else {}
    pseudo_session = {
        "accessToken": _s(c.get("access_token")),
        "id_token": _s(c.get("id_token")),
        "refresh_token": _s(c.get("refresh_token")),
        "expires_at": c.get("expires_at"),
        "email": _s(c.get("email")),
        "account_id": _s(c.get("chatgpt_account_id")),
        "user_id": _s(c.get("chatgpt_user_id")),
        "plan_type": _s(c.get("plan_type")),
    }
    return build_cpa_codex_json(pseudo_session, email=email or c.get("email", ""))


# ============================================================ chatgpt2api 普通网页号导入格式
def build_chatgpt2api_account(session, email=""):
    """ChatGPT 网页 session -> chatgpt2api(basketikun/chatgpt2api)普通号导入对象。

    与 CPA codex 不同:这里导的是**普通网页号**,只认 access_token,不带 type:"codex"
    (带 codex 会被对端当成 codex 源,见 account_service._prepare_account_payload)。
    对端 POST /api/accounts 的 accounts 数组里,每个对象只有 access_token 是必需,
    其余可选;type 走号池套餐类型(free/Plus/Pro...),没有则对端默认 free。
    无 accessToken 抛 ValueError。"""
    sess = session if _is_obj(session) else {}
    access_token = _s(sess.get("accessToken")) or _s(sess.get("access_token"))
    if not access_token:
        raise ValueError("未读取到可导入的 ChatGPT accessToken。")

    access_payload = parse_jwt_payload(access_token) or {}
    access_auth = _get_claim_section(access_payload, OPENAI_AUTH_CLAIM)
    profile = _get_claim_section(access_payload, OPENAI_PROFILE_CLAIM)

    user = sess.get("user") if _is_obj(sess.get("user")) else {}
    account = sess.get("account") if _is_obj(sess.get("account")) else {}
    email_val = _first_non_empty(
        _email_or_empty(user.get("email")),
        _email_or_empty(sess.get("email")),
        _email_or_empty(email),
        _email_or_empty(profile.get("email")),
        _email_or_empty(access_payload.get("email")),
    )
    account_id = _first_non_empty(
        account.get("id"), sess.get("account_id"),
        access_auth.get("chatgpt_account_id"),
    )
    # 套餐类型映射成对端号池类型;free/Plus/Pro... 对端会再归一化,这里给原始值即可
    plan_type = _first_non_empty(
        account.get("planType"), account.get("plan_type"),
        sess.get("planType"), sess.get("plan_type"),
        access_auth.get("chatgpt_plan_type"),
    )

    item = {"access_token": access_token, "source_type": "web"}
    if email_val:
        item["email"] = email_val
    if account_id:
        item["account_id"] = account_id
    if plan_type:
        item["type"] = plan_type
    return item


# ============================================================ SUB2API 导入 content
def build_sub2api_content(session):
    """ChatGPT session -> SUB2API 导入 content 字符串。对齐 sub2api-api.js buildCodexSessionImportContent。
    有 session 对象就返回合并了 accessToken 的 JSON 串;否则退化成 accessToken 串。"""
    sess = session if _is_obj(session) else None
    access_token = _s((sess or {}).get("accessToken"))
    if sess:
        content_obj = dict(sess)
        if access_token:
            content_obj["accessToken"] = access_token
        return json.dumps(content_obj, ensure_ascii=False, separators=(",", ":"))
    if access_token:
        return access_token
    raise ValueError("未读取到可导入的 ChatGPT 会话或 accessToken。")


def sub2api_expires_at(session):
    """SUB2API expires_at(unix 秒),取自 session.expires。无则 None。"""
    sess = session if _is_obj(session) else {}
    secs = _epoch_seconds(sess.get("expires"))
    return secs if secs > 0 else None


# ============================================================ 抓取(Playwright)
async def fetch_chatgpt_session(page):
    """页面在 chatgpt.com 时抓 /api/auth/session。返回含 accessToken 的 dict,否则 None。"""
    try:
        result = await page.evaluate(
            "() => fetch('/api/auth/session?ts=' + Date.now(), {"
            "credentials: 'include', cache: 'no-store', "
            "headers: {'cache-control': 'no-cache', pragma: 'no-cache'}"
            "})"
            ".then(r => r.ok ? r.json() : null).catch(() => null)"
        )
    except Exception:
        result = None
    if _is_obj(result) and _s(result.get("accessToken")):
        return result
    return None


# ============================================================ 落盘
def _platform_dir(platform):
    pdir = os.path.join(TOKEN_OUTPUT_DIR, platform)
    os.makedirs(pdir, exist_ok=True)
    return pdir


def _safe_email_name(email):
    return _sanitize_file_segment(email, fallback="account")


def save_chatgpt_tokens(session, email=""):
    """落盘 ChatGPT 标准 token。返回 True/False。"""
    if not _is_obj(session) or not _s(session.get("accessToken")):
        return False
    pdir = _platform_dir("chatgpt")
    name = _safe_email_name(email or session.get("user", {}).get("email") or "account")

    session_path = os.path.join(pdir, f"{name}.session.json")
    with open(session_path, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2, ensure_ascii=False)

    try:
        cpa = build_cpa_codex_json(session, email=email)
        cpa_path = os.path.join(pdir, cpa["file_name"])
        with open(cpa_path, "w", encoding="utf-8") as f:
            json.dump(cpa["auth_json"], f, indent=2, ensure_ascii=False)
        print(f"  [chatgpt] token saved: {session_path} + {cpa_path}")
    except Exception as e:
        # session 已落盘,CPA 转换失败不致命(上传脚本可重试)
        print(f"  [chatgpt] session saved: {session_path} (CPA 转换跳过: {e})")

    # chatgpt2api 普通网页号导入对象(单账号文件,后续可聚合成 accounts 数组一键导入)
    try:
        c2a = build_chatgpt2api_account(session, email=email)
        c2a_path = os.path.join(pdir, f"c2a-{name}.json")
        with open(c2a_path, "w", encoding="utf-8") as f:
            json.dump(c2a, f, indent=2, ensure_ascii=False)
        print(f"  [chatgpt] chatgpt2api token saved: {c2a_path}")
    except Exception as e:
        print(f"  [chatgpt] chatgpt2api 转换跳过: {e}")
    return True


def save_grok_token(sso, email=""):
    """落盘 Grok sso token。返回 True/False。"""
    sso = _s(sso)
    if not sso:
        return False
    pdir = _platform_dir("grok")
    name = _safe_email_name(email or "account")
    path = os.path.join(pdir, f"{name}.sso.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"email": _s(email), "sso": sso, "ts": int(time.time())},
                  f, indent=2, ensure_ascii=False)
    print(f"  [grok] sso token saved: {path}")
    return True


def save_claude_token(session_key, email=""):
    """落盘 Claude sessionKey（Claude 登录态就这一个，等价 grok 的 sso）。返回 True/False。"""
    sk = _s(session_key)
    if not sk:
        return False
    pdir = _platform_dir("claude")
    name = _safe_email_name(email or "account")
    path = os.path.join(pdir, f"{name}.sessionKey.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"email": _s(email), "sessionKey": sk, "ts": int(time.time())},
                  f, indent=2, ensure_ascii=False)
    print(f"  [claude] sessionKey saved: {path}")
    return True
