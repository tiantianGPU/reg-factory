# -*- coding: utf-8 -*-
"""
config.py — 全局配置。

所有密钥/凭据都从环境变量读取（默认空），不在仓库里留明文。
支持把变量写进同目录的 .env 文件（见 .env.example）；.env 只在对应环境
变量尚未设置时生效，不会覆盖真实的进程环境变量。
"""

import os


# ---------------------------------------------------------------- .env 加载
def _load_dotenv(path=None):
    """零依赖 .env 读取器：解析 KEY=VALUE，忽略空行与 # 注释。
    只在 os.environ 里尚未设置该 KEY 时填入（真实环境变量优先）。"""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        pass


_load_dotenv()


def _env(name, default=""):
    return os.environ.get(name, default)


def _env_int(name, default):
    try:
        return int(_env(name, str(default)) or default)
    except (TypeError, ValueError):
        return int(default)


# ---------------------------------------------------------------- 本地基建
# Fingerprint browser provider: bitbrowser / adspower
FINGERPRINT_BROWSER = _env("FINGERPRINT_BROWSER", "bitbrowser").strip().lower()

# BitBrowser 本地 API 地址
BITBROWSER_API = _env("BITBROWSER_API", "http://127.0.0.1:54345")

# AdsPower 本地 API 地址
ADSPOWER_API = _env("ADSPOWER_API", "http://127.0.0.1:50325")
ADSPOWER_API_KEY = _env("ADSPOWER_API_KEY", "")
ADSPOWER_GROUP_ID = _env("ADSPOWER_GROUP_ID", "0")

# Claude.ai 注册相关 URL
CLAUDE_LOGIN_URL = "https://claude.ai/login"
CLAUDE_CHALLENGE_WAIT_SECONDS = _env_int("CLAUDE_CHALLENGE_WAIT_SECONDS", 45)
CLAUDE_CHALLENGE_NODE_RETRIES = _env_int("CLAUDE_CHALLENGE_NODE_RETRIES", 3)
CLAUDE_CAPTCHA_MANUAL_TIMEOUT = _env_int("CLAUDE_CAPTCHA_MANUAL_TIMEOUT", 0)
CLAUDE_HCAPTCHA_SOLVE_RETRIES = _env_int("CLAUDE_HCAPTCHA_SOLVE_RETRIES", 2)
CLAUDE_VISION_API_BASE = _env("CLAUDE_VISION_API_BASE", "")
CLAUDE_VISION_API_KEY = _env("CLAUDE_VISION_API_KEY", "")
CLAUDE_VISION_MODEL = _env("CLAUDE_VISION_MODEL", "gemini-3.6-flash")
CLAUDE_NODE_PROBE_LIMIT = _env_int("CLAUDE_NODE_PROBE_LIMIT", 6)
CLAUDE_NODE_PROBE_TIMEOUT_SECONDS = _env_int("CLAUDE_NODE_PROBE_TIMEOUT_SECONDS", 8)

# Cookie 输出目录
COOKIE_OUTPUT_DIR = "cookies"

# ---------------------------------------------------------------- 域名邮箱（备用）
MAIL_DOMAIN = _env("MAIL_DOMAIN", "")
MAIL_API_BASE = _env("MAIL_API_BASE", "")
MAIL_ADMIN_USER = _env("MAIL_ADMIN_USER", "admin")
MAIL_ADMIN_PASS = _env("MAIL_ADMIN_PASS", "")
# JWT token（从浏览器抓取，可能会过期需要更新）
MAIL_AUTH_TOKEN = _env("MAIL_AUTH_TOKEN", "")
# 新建邮箱统一密码
MAIL_NEW_PASS = _env("MAIL_NEW_PASS", "")

# ---------------------------------------------------------------- 临时邮箱（纯 HTTP API 取码，Grok 注册用）
# 参考 grokcli-2api：用临时邮箱 HTTP API 直接拉验证码，免去 Outlook 浏览器登录/轮询的重开销。
# GROK_USE_TEMP_EMAIL=true 时 register_grok.py 走临时邮箱；创建失败自动回退 emails.txt Outlook。
GROK_USE_TEMP_EMAIL = _env("GROK_USE_TEMP_EMAIL", "false").strip().lower() in ("1", "true", "yes", "on")
# provider: moemail | yyds | gptmail | cfmail（默认 gptmail，带公共测试 key 开箱即用）
TEMP_EMAIL_PROVIDER = _env("TEMP_EMAIL_PROVIDER", "gptmail").strip().lower() or "gptmail"

# MoeMail（beilunyang/moemail，需自部署）
MOEMAIL_BASE_URL = _env("MOEMAIL_BASE_URL", "https://moemail.example.com")
MOEMAIL_API_KEY = _env("MOEMAIL_API_KEY", "")
MOEMAIL_DOMAIN = _env("MOEMAIL_DOMAIN", "")  # 留空则运行时从已有邮箱推断
MOEMAIL_EXPIRY_MS = int(_env("MOEMAIL_EXPIRY_MS", "3600000") or "3600000")  # 1h|1d(86400000)|3d(259200000)|0永久

# YYDS Mail（vip.215.im / maliapi.215.im）
YYDS_BASE_URL = _env("YYDS_BASE_URL", "https://maliapi.215.im")
YYDS_API_KEY = _env("YYDS_API_KEY", "")  # AC-... 格式，profile 页获取

# GPTMail（mail.chatgpt.org.uk），支持公共测试 key "gpt-test"
GPTMAIL_BASE_URL = _env("GPTMAIL_BASE_URL", "https://mail.chatgpt.org.uk")
GPTMAIL_API_KEY = _env("GPTMAIL_API_KEY", "gpt-test")

# Cloudflare Temp Email（dreamhunter2333/cloudflare_temp_email，建议自部署 Workers）
CFMAIL_BASE_URL = _env("CFMAIL_BASE_URL", "https://temp-email-api.awsl.uk")
CFMAIL_ADMIN_PASSWORD = _env("CFMAIL_ADMIN_PASSWORD", "")  # x-admin-auth header
CFMAIL_SITE_PASSWORD = _env("CFMAIL_SITE_PASSWORD", "")   # x-custom-auth header（可选）

# ---- 自定义临时邮箱（配置驱动，接任意 REST 风格 API，不写代码）----
# TEMP_EMAIL_PROVIDER=custom 时启用。JSON 路径支持点号+数组下标（如 data.address / data.items[0].id）。
# URL 与 body 模板可用占位符：{email} {id} {token} {name} {domain} {msg_id}
CUSTOM_MAIL_BASE_URL = _env("CUSTOM_MAIL_BASE_URL", "")
CUSTOM_MAIL_AUTH_HEADER = _env("CUSTOM_MAIL_AUTH_HEADER", "")   # 鉴权头名，空=不加鉴权头
CUSTOM_MAIL_API_KEY = _env("CUSTOM_MAIL_API_KEY", "")           # 鉴权头的值本体
CUSTOM_MAIL_AUTH_PREFIX = _env("CUSTOM_MAIL_AUTH_PREFIX", "")   # 值前缀（如 "Bearer "）
# 建号
CUSTOM_MAIL_CREATE_METHOD = _env("CUSTOM_MAIL_CREATE_METHOD", "POST")
CUSTOM_MAIL_CREATE_PATH = _env("CUSTOM_MAIL_CREATE_PATH", "")
CUSTOM_MAIL_CREATE_BODY = _env("CUSTOM_MAIL_CREATE_BODY", "")   # POST body 模板（JSON 串，占位符替换）
CUSTOM_MAIL_EMAIL_PATH = _env("CUSTOM_MAIL_EMAIL_PATH", "email")  # 响应里 email 的 JSON 路径
CUSTOM_MAIL_ID_PATH = _env("CUSTOM_MAIL_ID_PATH", "")           # 邮箱 id 路径（空=拿 email 当 id）
CUSTOM_MAIL_TOKEN_PATH = _env("CUSTOM_MAIL_TOKEN_PATH", "")     # 邮箱 token 路径（可选）
# 取信
CUSTOM_MAIL_FETCH_METHOD = _env("CUSTOM_MAIL_FETCH_METHOD", "GET")
CUSTOM_MAIL_FETCH_PATH = _env("CUSTOM_MAIL_FETCH_PATH", "")     # 占位符替换，如 /api/emails/{id}
CUSTOM_MAIL_FETCH_AUTH = _env("CUSTOM_MAIL_FETCH_AUTH", "key").strip().lower()  # key | token
CUSTOM_MAIL_LIST_PATH = _env("CUSTOM_MAIL_LIST_PATH", "")       # 消息数组的 JSON 路径（空=响应本身是数组）
CUSTOM_MAIL_DETAIL_PATH = _env("CUSTOM_MAIL_DETAIL_PATH", "")   # 单封详情路径（可选）
CUSTOM_MAIL_MSG_ID_PATH = _env("CUSTOM_MAIL_MSG_ID_PATH", "id")  # 列表项里 msgid 路径（配合 detail）
CUSTOM_MAIL_MSG_PATH = _env("CUSTOM_MAIL_MSG_PATH", "")         # detail 响应里单封 msg 的 JSON 路径

# ---------------------------------------------------------------- 短信接码平台 (firefox.fun)
SMS_API_BASE = _env("SMS_API_BASE", "http://www.firefox.fun/yhapi.ashx")
SMS_TOKEN = _env("SMS_TOKEN", "")  # 接码平台 token
SMS_PROJECT_ID = _env("SMS_PROJECT_ID", "2313")  # claude 项目
# 优先国家列表，按顺序尝试，""=任意(排除黑名单)
SMS_COUNTRY_PREFER = ["60", "56", "57", "44", ""]  # 60=马来西亚 56=智利 57=哥伦比亚 44=英国 ""=任意
SMS_COUNTRY_BLACKLIST = ["63"]  # 菲律宾

# ---------------------------------------------------------------- 备用短信平台 (hero-sms.com)
HERO_SMS_API_BASE = _env("HERO_SMS_API_BASE", "https://hero-sms.com/stubs/handler_api.php")
HERO_SMS_API_KEY = _env("HERO_SMS_API_KEY", "")  # 备用接码 api_key
HERO_SMS_SERVICE = _env("HERO_SMS_SERVICE", "acz")  # Claude 专用服务
# 优先国家: 7=马来西亚 52=泰国 16=英国 56=西班牙 39=阿根廷 86=意大利 34=爱沙尼亚 49=立陶宛 36=中国
HERO_SMS_COUNTRY_PREFER = [7, 52, 16, 56, 39, 86, 34, 49, 36]

# ---------------------------------------------------------------- 打码平台
# CapSolver 验证码打码平台
CAPSOLVER_API_KEY = _env("CAPSOLVER_API_KEY", "")

# EZ-Captcha 验证码打码平台
EZCAPTCHA_API_KEY = _env("EZCAPTCHA_API_KEY", "")
EZCAPTCHA_API_BASE = _env("EZCAPTCHA_API_BASE", "https://api.ez-captcha.com")

# YesCaptcha 打码平台（Grok Turnstile + GitHub Arkose）。API 与 CapSolver 兼容。
YESCAPTCHA_API_KEY = _env("YESCAPTCHA_API_KEY", "")
YESCAPTCHA_API_BASE = _env("YESCAPTCHA_API_BASE", "https://api.yescaptcha.com")

# ---------------------------------------------------------------- agent-captcha 视觉投票求解器
# GitHub Arkose 拼图用多模态大模型「投票」求解（common/agent_captcha.py）。
# 各家网关 OpenAI 兼容(/v1/chat/completions)；claude/opus 走 Anthropic 原生(/v1/messages)。
# 主视觉网关（gpt-5.x，图像增强 gpt-image-2 也在此）
VISION_API_BASE = _env("VISION_API_BASE", "")
VISION_API_KEY = _env("VISION_API_KEY", "")
# 图像增强兜底网关（gpt-image-2 images/edits）
IMAGE_EDIT_BASE2 = _env("IMAGE_EDIT_BASE2", "")
IMAGE_EDIT_KEY2 = _env("IMAGE_EDIT_KEY2", "")
# 投票池：中转网关(gemini/gpt) + claude 专用网关。逗号分隔的 key 留空则该模型不参与。
VOTE_ZZ_BASE = _env("VOTE_ZZ_BASE", "")          # 中转网关(gemini-3.5-flash / gemini-3.1-pro / gpt-5.5)
VOTE_ZZ_KEY = _env("VOTE_ZZ_KEY", "")            # 上面网关里 gemini 用的 key
VOTE_GPT_KEY = _env("VOTE_GPT_KEY", "")          # 同网关里 gpt-5.5 用的 key（可与 ZZ_KEY 不同）
VOTE_OPUS_BASE = _env("VOTE_OPUS_BASE", "")      # claude opus 专用网关（Anthropic /v1/messages）
VOTE_OPUS_KEY = _env("VOTE_OPUS_KEY", "")
# gemma 免费兜底文本网关（可选）
GEMMA_API_BASE = _env("GEMMA_API_BASE", "")
GEMMA_API_KEY = _env("GEMMA_API_KEY", "")

# ---------------------------------------------------------------- 标准 token 导出/上传
# 注册成功后落地的标准格式 token 目录（CPA codex / SUB2API content / grok sso）
TOKEN_OUTPUT_DIR = _env("TOKEN_OUTPUT_DIR", "tokens")

# CPA 管理接口（ChatGPT codex 授权文件导入）
CPA_URL = _env("CPA_URL", "")
CPA_MGMT_KEY = _env("CPA_MGMT_KEY", "")

# SUB2API 管理接口（ChatGPT codex-session / Grok SSO 转 OAuth 导入）
SUB2API_URL = _env("SUB2API_URL", "")
SUB2API_EMAIL = _env("SUB2API_EMAIL", "")
SUB2API_PASSWORD = _env("SUB2API_PASSWORD", "")
SUB2API_GROUP = _env("SUB2API_GROUP", "codex")  # 目标分组名，需先在 SUB2API 后台建好
SUB2API_GROK_GROUP = _env("SUB2API_GROK_GROUP", "grok")  # platform=grok 的目标分组
SUB2API_GROK_PROXY_ID = int(_env("SUB2API_GROK_PROXY_ID", "0") or "0")  # 0=不指定

# webchat2api（Grok sso 注入）
WEBCHAT2API_URL = _env("WEBCHAT2API_URL", "")
WEBCHAT2API_KEY = _env("WEBCHAT2API_KEY", "")

# chatgpt2api（basketikun/chatgpt2api 普通网页号导入，POST <url>/api/accounts）
# register_chatgpt.py --import-c2a 注册成功后逐个上传时用
CHATGPT2API_URL = _env("CHATGPT2API_URL", "")  # 对端 host（见 .env）
CHATGPT2API_KEY = _env("CHATGPT2API_KEY", "")  # 对端 admin key（Authorization: Bearer）

# ---------------------------------------------------------------- 订阅授权入口
# Claude / SuperGrok 订阅入口（激活码 CDK 流程「敬请期待」，后续支持授权到 SUB2API / CPA）
CLAUDE_SUB_URL = _env("CLAUDE_SUB_URL", "https://6661231.xyz/#/claude")
GROK_SUB_URL = _env("GROK_SUB_URL", "https://6661231.xyz/#/grok")
# 激活码 CDK 池（预留，逗号/换行/空格分隔）
CLAUDE_SUB_CDK = [c.strip() for c in _env("CLAUDE_SUB_CDK", "").replace("\n", ",").replace(" ", ",").split(",") if c.strip()]
GROK_SUB_CDK = [c.strip() for c in _env("GROK_SUB_CDK", "").replace("\n", ",").replace(" ", ",").split(",") if c.strip()]

# ---------------------------------------------------------------- ChatGPT OAuth add-phone 接码
# OpenAI/ChatGPT 在接码平台的服务号（按平台分，跟 Claude 的不同）
SMS_PROJECT_ID_OPENAI = _env("SMS_PROJECT_ID_OPENAI", "")  # firefox.fun 的 ChatGPT 项目 iid（待填）
HERO_SMS_SERVICE_OPENAI = _env("HERO_SMS_SERVICE_OPENAI", "dr")  # hero-sms/sms-activate OpenAI 服务码默认 dr
# firefox.fun 价格上限：'0' 只取最便宜(垃圾号易被 OpenAI 拒)，给够才摸得到智利等好号
SMS_MAXPRICE_OPENAI = _env("SMS_MAXPRICE_OPENAI", "20")
# OpenAI add-phone 拉黑的号段(dialing code)：261 马达加斯加、63 菲律宾 等 OpenAI 常拒的
SMS_COUNTRY_BLACKLIST_OPENAI = [c.strip() for c in _env("SMS_COUNTRY_BLACKLIST_OPENAI", "261,63").split(",") if c.strip()]

# ---------------------------------------------------------------- 接码平台 (sms-man.com)
# sms-man.com API v2.0：base/control，token 鉴权，JSON 响应。过 Codex add-phone 主用。
# 返回的 number 已含国家码。app_id 支持数字 application_id 或 code/名(运行时查 /applications 解析)。
SMSMAN_API_BASE = _env("SMSMAN_API_BASE", "https://api.sms-man.com/control")
SMSMAN_TOKEN = _env("SMSMAN_TOKEN", "")  # sms-man.com API key（profile 页获取）
SMSMAN_APP_ID_OPENAI = _env("SMSMAN_APP_ID_OPENAI", "openai")  # 数字 application_id 或 code/名(自动解析)
SMSMAN_COUNTRY_ID_OPENAI = _env("SMSMAN_COUNTRY_ID_OPENAI", "0")  # 0=随机国家
SMSMAN_MAXPRICE_OPENAI = _env("SMSMAN_MAXPRICE_OPENAI", "")  # 价格上限（sms-man 币种），空=不限
