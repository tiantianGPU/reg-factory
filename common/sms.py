# -*- coding: utf-8 -*-
"""
common/sms.py — 参数化接码客户端(sms-man.com 主用 + firefox.fun + hero-sms 兜底)。

逻辑搬自 register.py 的 Claude 接码函数，但把"项目号/服务号/国家偏好"做成参数，
这样不同平台(Claude / OpenAI ...)各用各的服务号，互不影响。register.py 原样保留。

provider 路由靠 pkey 前缀：
  smsman_<request_id>  -> sms-man.com  (Codex add-phone 主用)
  hero_<activation_id> -> hero-sms.com
  其余                 -> firefox.fun

firefox.fun: act=getPhone/getPhoneCode/cancelPhone, iid=项目号
hero-sms(sms-activate 兼容): action=getNumber/getStatus/setStatus, service=服务码(OpenAI 默认 dr)
sms-man(API v2.0): /get-number /get-sms /set-status, token 鉴权, JSON; number 已含国家码。
  application_id 支持数字 或 code/名(运行时查 /applications 自动解析)。

⚠️ WIP：自动接码主要被 common/oauth_codex.handle_add_phone(Codex add-phone)调用。
register.py 自带一套独立接码(get_phone_number)，两边暂未统一；全自动接码版完善时一并收口。

CLI 探测(查 OpenAI application_id / 余额)：
  python -m common.sms applications
  python -m common.sms countries
  python -m common.sms balance
"""

import re
import sys
import time

import requests

from config import (
    SMS_API_BASE, SMS_TOKEN,
    HERO_SMS_API_BASE, HERO_SMS_API_KEY, HERO_SMS_COUNTRY_PREFER,
    SMSMAN_API_BASE, SMSMAN_TOKEN,
)


def get_phone(project_id, hero_service, country_prefer=("",), country_blacklist=(), max_retries=5, max_price="0",
              smsman_app=None, smsman_country="0", smsman_maxprice="", smsman_blacklist=()):
    """返回 (phone, country_code, pkey)。sms-man.com 优先(若配 token+app)，再 firefox.fun，最后 hero-sms。
    hero-sms 与 sms-man 的 phone 已含国家码、country_code 返回 ''。
    max_price: firefox.fun 价格上限，'0' 只取最便宜(常是垃圾号段)，给够才摸得到好国家。
    smsman_app: sms-man 的 application_id(数字)或 code/名(自动解析)；None=不启用 sms-man。
    smsman_country: sms-man country_id，'0'=自动按价格升序逐国试；smsman_maxprice: 价格上限(USD)，''=不限。
    smsman_blacklist: sms-man country_id 黑名单(自动逐国时跳过)。"""
    # —— sms-man.com 优先(Codex add-phone 主用)。配了 token + app 才尝试，否则跳过 ——
    if SMSMAN_TOKEN and smsman_app not in (None, ""):
        res = _smsman_get_phone(smsman_app, smsman_country, smsman_maxprice, smsman_blacklist)
        if res:
            full_phone, pkey = res
            return full_phone, "", pkey
        print("  [sms-man] 无号/未解析到 application，转 firefox.fun...")

    if SMS_TOKEN and project_id:
        for country in country_prefer:
            attempts = max_retries if country == "" else 1
            for attempt in range(attempts):
                try:
                    resp = requests.get(SMS_API_BASE, params={
                        "act": "getPhone", "token": SMS_TOKEN, "iid": project_id,
                        "did": "", "country": country, "dock": "", "otpmode": "",
                        "maxPrice": str(max_price), "mobile": "", "pushUrl": "",
                    }, timeout=30)
                except Exception as e:
                    print(f"  [sms] err: {e}")
                    break
                text = resp.text.strip()
                print(f"  [sms] api(country={country or 'any'}, try={attempt+1}): {text}")
                parts = text.split("|")
                if parts[0] == "1" and len(parts) >= 8:
                    pkey, country_code, phone = parts[1], parts[4], parts[7]
                    if country_code in country_blacklist:
                        print(f"  [sms] +{country_code} blacklisted, releasing...")
                        release(pkey)
                        time.sleep(1)
                        continue
                    print(f"  [sms] phone: +{country_code}{phone} (pkey={pkey})")
                    return phone, country_code, pkey
                # 非成功响应：'0|-8'/'0|-4' 等是"该项目暂时无号"，库存常秒级闪进闪出，
                # 不能一次就 break —— 睡几秒再要，把瞬时空窗骑过去（仅 country=='' 多次重试时）。
                if attempt < attempts - 1:
                    time.sleep(8)
                    continue
                break

    print("  [sms] firefox.fun 无号/未配，转 hero-sms...")
    res = _hero_get_phone(hero_service)
    if res:
        full_phone, pkey = res
        return full_phone, "", pkey
    raise RuntimeError("get phone failed: 所有平台都没号")


def get_code(pkey, max_wait=180, interval=5):
    if str(pkey).startswith("smsman_"):
        return _smsman_get_code(pkey, max_wait, interval)
    if str(pkey).startswith("hero_"):
        return _hero_get_code(pkey, max_wait, interval)
    start = time.time()
    while time.time() - start < max_wait:
        try:
            resp = requests.get(SMS_API_BASE, params={"act": "getPhoneCode", "token": SMS_TOKEN, "pkey": pkey}, timeout=30)
            parts = resp.text.strip().split("|")
            if parts[0] == "1" and len(parts) >= 2:
                code = parts[1]
                print(f"  [sms] code: {code}")
                return code
        except Exception:
            pass
        print(f"  waiting sms... ({int(time.time()-start)}s/{max_wait}s)")
        time.sleep(interval)
    return None


def release(pkey):
    if str(pkey).startswith("smsman_"):
        _smsman_release(pkey)
        return
    if str(pkey).startswith("hero_"):
        _hero_release(pkey)
        return
    try:
        requests.get(SMS_API_BASE, params={"act": "cancelPhone", "token": SMS_TOKEN, "pkey": pkey}, timeout=10)
    except Exception:
        pass


# ---------------- hero-sms ----------------
def _hero_get_phone(service):
    if not (HERO_SMS_API_KEY and service):
        return None
    countries = HERO_SMS_COUNTRY_PREFER
    try:
        r = requests.get(HERO_SMS_API_BASE, params={"api_key": HERO_SMS_API_KEY, "action": "getPrices", "service": service}, timeout=15)
        prices = r.json()
        ranked = []
        for cid, svc in prices.items():
            info = svc.get(service, {})
            if info.get("count", 0) > 0 and info.get("cost", 999) < 1.0:
                ranked.append((info["cost"], -info["count"], int(cid)))
        ranked.sort()
        if ranked:
            countries = [c for _, _, c in ranked]
            print(f"  [hero-sms] {len(countries)} countries (cheapest ${ranked[0][0]} id={ranked[0][2]})")
    except Exception as e:
        print(f"  [hero-sms] getPrices failed: {e}")
    for country in countries:
        try:
            r = requests.get(HERO_SMS_API_BASE, params={
                "api_key": HERO_SMS_API_KEY, "action": "getNumber", "service": service, "country": country,
            }, timeout=30)
            text = r.text.strip()
            if text.startswith("ACCESS_NUMBER:"):
                _, act_id, full_phone = text.split(":")[:3]
                print(f"  [hero-sms] country={country}: +{full_phone} (id={act_id})")
                return full_phone, f"hero_{act_id}"
        except Exception as e:
            print(f"  [hero-sms] err country={country}: {e}")
    return None


def _hero_get_code(pkey, max_wait=180, interval=5):
    act_id = str(pkey).replace("hero_", "")
    try:
        requests.get(HERO_SMS_API_BASE, params={"api_key": HERO_SMS_API_KEY, "action": "setStatus", "id": act_id, "status": 1}, timeout=10)
    except Exception:
        pass
    start = time.time()
    while time.time() - start < max_wait:
        try:
            r = requests.get(HERO_SMS_API_BASE, params={"api_key": HERO_SMS_API_KEY, "action": "getStatus", "id": act_id}, timeout=30)
            text = r.text.strip()
            if text.startswith("STATUS_OK:"):
                code = text.split(":")[1]
                m = re.search(r"\d{4,8}", code)
                print(f"  [hero-sms] code: {code}")
                return m.group(0) if m else code
            if text == "STATUS_CANCEL":
                return None
        except Exception:
            pass
        print(f"  [hero-sms] waiting... ({int(time.time()-start)}s/{max_wait}s)")
        time.sleep(interval)
    return None


def _hero_release(pkey):
    act_id = str(pkey).replace("hero_", "")
    try:
        requests.get(HERO_SMS_API_BASE, params={"api_key": HERO_SMS_API_KEY, "action": "setStatus", "id": act_id, "status": 8}, timeout=10)
    except Exception:
        pass


# ---------------- sms-man.com (API v2.0) ----------------
_SMSMAN_APP_CACHE = {}  # 原始 app 值(str) -> 解析出的数字 application_id


def _smsman_url(path):
    return SMSMAN_API_BASE.rstrip("/") + "/" + path.lstrip("/")


def _smsman_get(path, params, timeout=30, retries=3):
    """GET sms-man 接口，对连接类错误(经代理偶发 SSLEOFError/连接重置)小退避重试。
    返回解析后的 JSON(dict/list)；彻底失败抛最后一个异常，由调用方兜底。
    业务错误(JSON 里的 error_code)不在这里重试——交给调用方按语义处理。"""
    last = None
    for i in range(retries):
        try:
            r = requests.get(_smsman_url(path), params=params, timeout=timeout)
            return r.json()
        except (requests.ConnectionError, requests.Timeout, ValueError) as e:
            last = e
            if i < retries - 1:
                time.sleep(1.5 * (i + 1))
                continue
    raise last if last else RuntimeError("sms-man 请求失败")


def _smsman_resolve_app(value):
    """把 application 标识解析成数字 application_id。
    纯数字直接用；否则查 /applications 按 code 精确 或 title/name 子串(忽略大小写)匹配。
    解析结果按原始 value 缓存，避免每次取号都打一次 /applications。
    注：sms-man /applications 实际返回 **以 id 为 key 的 dict**，字段名为 title（非 docs 的 name）。"""
    raw = str(value).strip()
    if not raw:
        return None
    if raw.isdigit():
        return raw
    if raw in _SMSMAN_APP_CACHE:
        return _SMSMAN_APP_CACHE[raw]
    try:
        apps = _smsman_get("applications", {"token": SMSMAN_TOKEN}, timeout=15)
    except Exception as e:
        print(f"  [sms-man] applications 查询失败(已重试): {e}")
        return None
    items = list(apps.values()) if isinstance(apps, dict) else apps
    if not isinstance(items, list):
        print(f"  [sms-man] applications 异常响应: {str(apps)[:120]}")
        return None
    low = raw.lower()
    by_code = by_name = None
    for a in items:
        if not isinstance(a, dict):
            continue
        code = str(a.get("code") or "").lower()
        name = str(a.get("title") or a.get("name") or "").lower()
        if code == low and by_code is None:
            by_code = str(a.get("id"))
        if low in name and by_name is None:
            by_name = str(a.get("id"))
    app_id = by_code or by_name
    if app_id:
        _SMSMAN_APP_CACHE[raw] = app_id
        print(f"  [sms-man] application '{raw}' -> id={app_id}")
    else:
        print(f"  [sms-man] 未在 applications 里匹配到 '{raw}'（用 `python -m common.sms applications` 查）")
    return app_id


def _smsman_rank_countries(app_id, max_price="", blacklist=()):
    """查 /get-prices，返回该 app **有货国家按价格升序** 的 country_id 列表。
    max_price 非空时过滤掉超价国家；blacklist 里的 country_id 跳过。
    取不到价格表(异常/空)返回 []，调用方回退随机(country_id=0)。"""
    try:
        prices = _smsman_get("get-prices", {"token": SMSMAN_TOKEN}, timeout=30)
    except Exception as e:
        print(f"  [sms-man] get-prices 失败(已重试): {e}")
        return []
    if not isinstance(prices, dict):
        return []
    cap = None
    try:
        cap = float(max_price) if str(max_price).strip() not in ("", "0") else None
    except Exception:
        cap = None
    bl = {str(b) for b in blacklist}
    ranked = []
    for cid, apps in prices.items():
        if str(cid) in bl or not isinstance(apps, dict):
            continue
        info = apps.get(str(app_id))
        if not isinstance(info, dict):
            continue
        try:
            cost, count = float(info.get("cost", 0)), int(info.get("count", 0))
        except Exception:
            continue
        if count <= 0:
            continue
        if cap is not None and cost > cap:
            continue
        ranked.append((cost, -count, str(cid)))
    ranked.sort()
    cids = [c for _, _, c in ranked]
    if cids:
        print(f"  [sms-man] 有货国家 {len(cids)} 个（最便宜 cost={ranked[0][0]} country_id={ranked[0][2]}）")
    return cids


def smsman_rent(app, country_id="0", prefer_multi=True, max_price="", blacklist=()):
    """接码助手专用租号：返回 (number, pkey, can_multi) 或 None。
    prefer_multi=True 时优先租 can_receive_multiple_sms=True 的号(适合 Gmail 多次验证)：
    先试已知支持多收的国家(马来6等)，没有再退而求其次拿普通号。
    country_id 指定具体国家时直接租该国(忽略 prefer)。"""
    app_id = _smsman_resolve_app(app)
    if not app_id:
        return None

    def _try(cid):
        params = {"token": SMSMAN_TOKEN, "country_id": str(cid), "application_id": str(app_id)}
        if str(max_price).strip() not in ("", "0"):
            params["maxPrice"] = str(max_price).strip(); params["currency"] = "USD"
        try:
            data = _smsman_get("get-number", params, timeout=30)
        except Exception as e:
            print(f"  [sms-man] get-number(country={cid}) 失败: {e}")
            return None
        if isinstance(data, dict) and data.get("request_id") and data.get("number"):
            multi = bool(data.get("can_receive_multiple_sms"))
            print(f"  [sms-man] phone: +{data['number']} (req={data['request_id']}, country={data.get('country_id')}, 多收={multi})")
            return str(data["number"]), f"smsman_{data['request_id']}", multi
        return None

    cid = str(country_id or "0").strip()
    if cid not in ("", "0"):
        return _try(cid)

    bl = {str(b) for b in blacklist}
    # 已知支持多收的国家优先(马来6/印度22/越南117实测/经验)，再常备国家兜底
    multi_first = [c for c in ("6", "22", "117", "16") if c not in bl]
    others = [c for c in ("7", "36", "4", "12", "14", "10") if c not in bl and c not in multi_first]
    order = (multi_first + others) if prefer_multi else (["7", "6", "16", "22", "117", "36", "4", "12"])
    fallback = None  # 拿到的非多收号(实在没多收号时用)
    for c in order:
        res = _try(c)
        if not res:
            continue
        if not prefer_multi or res[2]:
            return res  # 命中(多收号 或 不要求多收)
        # 是普通号：先记着，继续找多收号；找不到再用它
        if fallback is None:
            fallback = res
        else:
            _smsman_release(res[1])  # 多余的普通号退掉不浪费
    return fallback


def _smsman_request_number(app_id, country_id, max_price=""):
    """单次 GET /get-number。
    返回：成功 -> (full_phone, 'smsman_<request_id>')；该国无货 -> None；
    账号级错误(余额不足/token错/封号) -> "FATAL"（调用方应立即停止逐国，别白刷）。"""
    params = {"token": SMSMAN_TOKEN, "country_id": str(country_id), "application_id": str(app_id)}
    if str(max_price).strip() not in ("", "0"):
        params["maxPrice"] = str(max_price).strip()
        params["currency"] = "USD"
    try:
        data = _smsman_get("get-number", params, timeout=30)
    except Exception as e:
        print(f"  [sms-man] get-number(country={country_id}) 失败(已重试): {e}")
        return None
    if isinstance(data, dict) and data.get("request_id") and data.get("number"):
        req_id, number = data["request_id"], str(data["number"])
        print(f"  [sms-man] phone: +{number} (request_id={req_id}, country_id={data.get('country_id')})")
        return number, f"smsman_{req_id}"
    code = str((data or {}).get("error_code") or "").lower() if isinstance(data, dict) else ""
    err = (data or {}).get("error_msg") or (data or {}).get("error_code") if isinstance(data, dict) else data
    err_s = str(err)
    # 账号级错误：余额不足 / token 错 / 被封 —— 逐国都会同样失败，立即终止
    fatal_codes = {"wrong_token", "no_balance", "balance", "low_balance", "account_inactive", "banned"}
    fatal_kw = ["balance must exceed", "top up", "wrong token", "insufficient", "余额", "balance"]
    if code in fatal_codes or any(k in err_s.lower() for k in fatal_kw):
        print(f"  [sms-man] 账号级错误，停止逐国: {err_s[:120]}")
        return "FATAL"
    print(f"  [sms-man] 无号(country={country_id}): {err_s[:100]}")
    return None


def _smsman_get_phone(app, country_id="0", max_price="", blacklist=()):
    """租号。country_id 非 '0' 时直接要该国；'0'(自动)时按 /get-prices **价格升序**逐国尝试，
    没货再回退随机。返回 (full_phone, 'smsman_<request_id>')；失败返回 None。
    遇账号级错误(余额不足/token错)立即终止，不白刷 178 国。
    number 已含国家码（如 79002415539）。"""
    app_id = _smsman_resolve_app(app)
    if not app_id:
        return None
    cid = str(country_id or "0").strip()
    if cid not in ("", "0"):
        res = _smsman_request_number(app_id, cid, max_price)
        return res if isinstance(res, tuple) else None
    # 自动：按价格升序逐国尝试（贵的留给后面，先薅最便宜的有货国家）
    ranked = _smsman_rank_countries(app_id, max_price, blacklist)
    for c in ranked:
        res = _smsman_request_number(app_id, c, max_price)
        if res == "FATAL":
            return None  # 账号级错误，逐国无意义，直接退（回退到 firefox/hero）
        if res:
            return res
    # 价格表拿不到(get-prices 经常 500/抽风)时**不要直接放弃**——回退逐个试常备有货国家。
    # sms-man country_id: 7印尼 6马来 16英国 12美国 4菲律宾 22印度 36柬埔寨 117越南 14西班牙 ...
    if not ranked:
        bl = {str(b) for b in blacklist}
        fallback = [c for c in ("7", "6", "16", "22", "117", "36", "4", "12", "14", "10")
                    if c not in bl]
        print(f"  [sms-man] 价格表取不到(接口故障)，回退逐国试租 {len(fallback)} 个常备国家...")
        for c in fallback:
            res = _smsman_request_number(app_id, c, max_price)
            if res == "FATAL":
                return None
            if res:
                return res
        print("  [sms-man] 回退国家也都无货，跳过 sms-man，回退兜底渠道")
    return None


def _smsman_get_code(pkey, max_wait=180, interval=5):
    req_id = str(pkey).replace("smsman_", "")
    start = time.time()
    while time.time() - start < max_wait:
        try:
            r = requests.get(_smsman_url("get-sms"), params={"token": SMSMAN_TOKEN, "request_id": req_id}, timeout=30)
            data = r.json()
            if isinstance(data, dict) and data.get("sms_code"):
                code = str(data["sms_code"])
                print(f"  [sms-man] code: {code}")
                # 取到码即标记 used（释放/计费收口）
                try:
                    requests.get(_smsman_url("set-status"),
                                 params={"token": SMSMAN_TOKEN, "request_id": req_id, "status": "used"}, timeout=10)
                except Exception:
                    pass
                m = re.search(r"\d{4,8}", code)
                return m.group(0) if m else code
            # error_code=wait_sms 是"还没到码"，继续轮询；其它 error 直接放弃
            ec = (data or {}).get("error_code") if isinstance(data, dict) else None
            if ec and ec != "wait_sms":
                print(f"  [sms-man] get-sms 终止: {ec}")
                return None
        except Exception:
            pass
        print(f"  [sms-man] waiting... ({int(time.time()-start)}s/{max_wait}s)")
        time.sleep(interval)
    return None


def _smsman_release(pkey):
    """退号：set-status=reject（取消未用号，触发退款/释放）。"""
    req_id = str(pkey).replace("smsman_", "")
    try:
        requests.get(_smsman_url("set-status"),
                     params={"token": SMSMAN_TOKEN, "request_id": req_id, "status": "reject"}, timeout=10)
    except Exception:
        pass


def smsman_peek_code(pkey, max_wait=90, interval=5, mark_used=False, since=None):
    """手动多次接码用：轮询 get-sms 取一条码。**mark_used=False 时不 set-status**，
    号在租期内保持活动，可再次调用拿下一条新码(Gmail 二次验证等)。
    since: 只接受与该值不同的码(用于"再取一条新码"，跳过上次已拿到的)。
    返回 code 或 None(超时/未到新码)。与自动接码 _smsman_get_code 的区别仅在不收口。"""
    req_id = str(pkey).replace("smsman_", "")
    start = time.time()
    while time.time() - start < max_wait:
        try:
            r = requests.get(_smsman_url("get-sms"),
                             params={"token": SMSMAN_TOKEN, "request_id": req_id}, timeout=30)
            data = r.json()
            if isinstance(data, dict) and data.get("sms_code"):
                code = str(data["sms_code"])
                m = re.search(r"\d{4,8}", code)
                code = m.group(0) if m else code
                # 若要求"新码"且与上次相同，继续等(同号下一条短信)
                if since is not None and code == str(since):
                    time.sleep(interval)
                    continue
                print(f"  [sms-man] peek code: {code}")
                if mark_used:
                    try:
                        requests.get(_smsman_url("set-status"),
                                     params={"token": SMSMAN_TOKEN, "request_id": req_id, "status": "used"}, timeout=10)
                    except Exception:
                        pass
                return code
            ec = (data or {}).get("error_code") if isinstance(data, dict) else None
            # wait_sms = 还没到码；其它业务错误(如已过期/无此号)直接放弃
            if ec and ec != "wait_sms":
                print(f"  [sms-man] peek 终止: {ec}")
                return None
        except Exception:
            pass
        time.sleep(interval)
    return None


# ---------------- CLI 探测（查 OpenAI application_id / 国家 / 余额 / 价格）----------------
def _cli(argv):
    if not SMSMAN_TOKEN:
        print("未配置 SMSMAN_TOKEN（.env 或环境变量）")
        return 1
    cmd = (argv[0] if argv else "applications").lower()
    # prices [app] [country]：按价格升序列出有货国家
    if cmd in ("prices", "price"):
        app = argv[1] if len(argv) > 1 else "openai"
        app_id = _smsman_resolve_app(app)
        if not app_id:
            return 1
        try:
            rc = requests.get(_smsman_url("countries"), params={"token": SMSMAN_TOKEN}, timeout=20).json()
        except Exception:
            rc = {}
        cmap = {}
        for c in (rc.values() if isinstance(rc, dict) else (rc or [])):
            if isinstance(c, dict):
                cmap[str(c.get("id"))] = c.get("title") or c.get("name")
        try:
            rp = requests.get(_smsman_url("get-prices"), params={"token": SMSMAN_TOKEN}, timeout=30).json()
        except Exception as e:
            print(f"get-prices 失败: {e}")
            return 1
        rows = []
        for cid, apps in (rp.items() if isinstance(rp, dict) else []):
            info = apps.get(str(app_id)) if isinstance(apps, dict) else None
            if isinstance(info, dict) and int(info.get("count", 0)) > 0:
                rows.append((float(info["cost"]), int(info["count"]), str(cid)))
        rows.sort()
        print(f"app_id={app_id} 有货国家（按价格升序），共 {len(rows)}：")
        for cost, count, cid in rows[:40]:
            print(f"  cost={cost:<8} count={count:<9} country_id={cid:<5} {cmap.get(cid,'?')}")
        return 0
    path = {"applications": "applications", "apps": "applications",
            "countries": "countries", "balance": "get-balance"}.get(cmd)
    if not path:
        print("用法: python -m common.sms [applications|countries|balance|prices [app] ]")
        return 1
    try:
        r = requests.get(_smsman_url(path), params={"token": SMSMAN_TOKEN}, timeout=20)
        data = r.json()
    except Exception as e:
        print(f"请求失败: {e}")
        return 1
    if cmd in ("applications", "apps"):
        items = list(data.values()) if isinstance(data, dict) else (data or [])
        hits = []
        for a in items:
            if not isinstance(a, dict):
                continue
            name = str(a.get("title") or a.get("name") or "")
            if any(k in name.lower() for k in ("openai", "chatgpt", "gpt")):
                hits.append(a)
        print(f"共 {len(items)} 个服务。疑似 OpenAI/ChatGPT：")
        for a in hits:
            print(f"  id={a.get('id')}  code={a.get('code')}  {a.get('title') or a.get('name')}")
        if not hits:
            print("  （未匹配到，可 `python -m common.sms applications` 后自行 grep）")
    else:
        print(data)
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
