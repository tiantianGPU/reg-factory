# -*- coding: utf-8 -*-
"""
common/proxy_switch.py — Clash/mihomo 节点切换 + 代理出口

通过 Clash RESTful 控制面切换出口节点，让注册脚本/反代走指定干净节点
（用于过 grok.com 这种对 IP 敏感的 Cloudflare）。

本机 Clash Verge（需在 设置 -> External Controller 开启 API 并设置 secret）:
  控制面 http://127.0.0.1:9097  (secret 由环境变量 CLASH_SECRET 提供)
  代理   http://127.0.0.1:7897   (mixed-port)

相关环境变量: CLASH_API / CLASH_SECRET / CLASH_PROXY / CLASH_GROUP（见 .env.example）。
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request

# 触发 .env 加载（config 模块导入时会读取同目录 .env，使 CLASH_SECRET 等生效）。
try:
    import config  # noqa: F401
except Exception:
    pass

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

CLASH_API = os.environ.get("CLASH_API", "http://127.0.0.1:9097")
CLASH_SECRET = os.environ.get("CLASH_SECRET", "")
CLASH_PROXY = os.environ.get("CLASH_PROXY", "http://127.0.0.1:7897")
# Clash 处于 global 模式时，真正决定出口的是 GLOBAL 组（切 '🚀 节点选择' 不生效！）
DEFAULT_GROUP = os.environ.get("CLASH_GROUP", "GLOBAL")


def _headers():
    h = {"Content-Type": "application/json"}
    if CLASH_SECRET:
        h["Authorization"] = f"Bearer {CLASH_SECRET}"
    return h


def _get(path):
    req = urllib.request.Request(f"{CLASH_API}{path}", headers=_headers())
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read())


def list_nodes(group=DEFAULT_GROUP):
    """返回组内全部节点名列表"""
    d = _get(f"/proxies/{urllib.parse.quote(group)}")
    return d.get("all", [])


def current_node(group=DEFAULT_GROUP):
    d = _get(f"/proxies/{urllib.parse.quote(group)}")
    return d.get("now")


def set_node(name, group=DEFAULT_GROUP):
    """切换组的出口节点到 name"""
    data = json.dumps({"name": name}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{CLASH_API}/proxies/{urllib.parse.quote(group)}",
        data=data, method="PUT", headers=_headers(),
    )
    urllib.request.urlopen(req, timeout=8).read()
    return True


def node_delay(name, url="https://www.google.com/generate_204", timeout_ms=5000):
    """测某节点延迟(ms)，超时/失败返回 None"""
    try:
        d = _get(f"/proxies/{urllib.parse.quote(name)}/delay?timeout={timeout_ms}&url={urllib.parse.quote(url)}")
        return d.get("delay")
    except Exception:
        return None


def concrete_nodes(group=DEFAULT_GROUP):
    """Return leaf proxies, including country-only names without digits."""
    metadata = ("套餐", "剩余", "重置", "到期", "官网", "http://", "https://")
    names = list_nodes(group)
    try:
        catalog = (_get("/proxies").get("proxies") or {})
        group_types = {"Selector", "URLTest", "Fallback", "LoadBalance"}
        return [
            name for name in names
            if name not in {"DIRECT", "REJECT"}
            and not any(marker in name for marker in metadata)
            and (catalog.get(name) or {}).get("type") not in group_types
        ]
    except Exception:
        # Older Clash controllers may not expose the full catalog. Preserve the
        # old numbered-node fallback while also accepting flag-prefixed leaves.
        return [
            name for name in names
            if (any(char.isdigit() for char in name)
                or (name and 0x1F1E6 <= ord(name[0]) <= 0x1F1FF))
            and not any(marker in name for marker in metadata)
        ]


def find_working_node(test_url="https://grok.com/", group=DEFAULT_GROUP,
                      challenge_markers=("just a moment", "performing security"),
                      required_markers=(), warmup_url=None, candidates=None,
                      settle=3, timeout=18, verbose=True):
    """切 GLOBAL 组逐个试节点，返回第一个能过 test_url 的 CF 的节点名（curl_cffi chrome 指纹）。
    找不到返回 None。注意：必须切 GLOBAL 组（global 模式下 '🚀 节点选择' 不影响出口）。"""
    try:
        from curl_cffi import requests as creq
    except ImportError:
        creq = None
    nodes = candidates or concrete_nodes(group)
    # 打散，避免每次都从同一批热节点开始
    import random as _r
    _r.shuffle(nodes)
    for name in nodes:
        try:
            set_node(name, group)
        except Exception:
            continue
        time.sleep(settle)
        if creq is None:
            # 没有 curl_cffi 就只切节点不验证
            return name
        try:
            proxies = {"http": CLASH_PROXY, "https": CLASH_PROXY}
            if warmup_url:
                session = creq.Session(impersonate="chrome131", http_version="v2")
                session.proxies = proxies
                try:
                    session.get(
                        warmup_url, allow_redirects=True, timeout=timeout
                    )
                    r = session.get(
                        test_url, allow_redirects=True, timeout=timeout
                    )
                finally:
                    session.close()
            else:
                r = creq.get(
                    test_url,
                    impersonate="chrome131",
                    proxies=proxies,
                    timeout=timeout,
                )
            body = r.text[:200000].lower()
            chal = any(m.lower() in body for m in challenge_markers)
            required_ok = all(m.lower() in body for m in required_markers)
            ok = r.status_code == 200 and not chal and required_ok
            if verbose:
                reason = "PASS" if ok else ("INCOMPLETE" if not required_ok else "BLOCK")
                print(f"  [node] {name}: HTTP {r.status_code} {reason}")
            if ok:
                return name
        except Exception as e:
            if verbose:
                print(f"  [node] {name}: ERR {str(e)[:30]}")
    return None


if __name__ == "__main__":
    # CLI: python -m common.proxy_switch [list|current|set <name>]
    import sys as _s
    args = _s.argv[1:]
    if not args or args[0] == "list":
        print("当前:", current_node())
        for n in list_nodes():
            print(" ", n)
    elif args[0] == "current":
        print(current_node())
    elif args[0] == "set" and len(args) > 1:
        set_node(args[1])
        time.sleep(1)
        print("已切换 ->", current_node())
