<div align="center">

# 🏭 reg-factory

### Outlook · Gmail · ChatGPT · Grok · Claude · Gemini · Google One 全自动注册/授权机

**自动批量注册 Outlook / Gmail 邮箱 → 平台注册 / 订阅授权 → 导出 cookie 或导入 SUB2API / CPA**

<p>
  <img src="https://img.shields.io/badge/Outlook-0078D4?style=for-the-badge&logo=data:image/svg+xml;base64,PHN2ZyByb2xlPSJpbWciIHZpZXdCb3g9IjAgMCAyNCAyNCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cGF0aCBmaWxsPSJ3aGl0ZSIgZD0iTTcuODggMTIuMDRxMCAuNDUtLjExLjg3LS4xLjQxLS4zMy43NC0uMjIuMzMtLjU4LjUyLS4zNy4yLS44Ny4ydC0uODUtLjJxLS4zNS0uMjEtLjU3LS41NS0uMjItLjMzLS4zMy0uNzUtLjEtLjQyLS4xLS44NnQuMS0uODdxLjEtLjQzLjM0LS43Ni4yMi0uMzQuNTktLjU0LjM2LS4yLjg3LS4ydC44Ni4ycS4zNS4yMS41Ny41NS4yMi4zNC4zMS43Ny4xLjQzLjEuODh6TTI0IDEydjkuMzhxMCAuNDYtLjMzLjgtLjMzLjMyLS44LjMySDcuMTNxLS40NiAwLS44LS4zMy0uMzItLjMzLS4zMi0uOFYxOEgxcS0uNDEgMC0uNy0uMy0uMy0uMjktLjMtLjdWN3EwLS40MS4zLS43US41OCA2IDEgNmg2LjVWMi41NXEwLS40NC4zLS43NS4zLS4zLjc1LS4zaDEyLjlxLjQ0IDAgLjc1LjMuMy4zLjMuNzVWMTAuODVsMS4yNC43MmguMDFxLjEuMDcuMTguMTguMDcuMTIuMDcuMjV6bS02LTguMjV2M2gzdi0zem0wIDQuNXYzaDN2LTN6bTAgNC41djEuODNsMy4wNS0xLjgzem0tNS4yNS05djNoMy43NXYtM3ptMCA0LjV2M2gzLjc1di0zem0wIDQuNXYyLjAzbDIuNDEgMS41IDEuMzQtLjh2LTIuNzN6TTkgMy43NVY2aDJsLjEzLjAxLjEyLjA0di0yLjN6TTUuOTggMTUuOThxLjkgMCAxLjYtLjMuNy0uMzIgMS4xOS0uODYuNDgtLjU1LjczLTEuMjguMjUtLjc0LjI1LTEuNjEgMC0uODMtLjI1LTEuNTUtLjI0LS43MS0uNzEtMS4yNHQtMS4xNS0uODNxLS42OC0uMy0xLjU1LS4zLS45MiAwLTEuNjQuMy0uNzEuMy0xLjIuODUtLjUuNTQtLjc1IDEuMy0uMjUuNzQtLjI1IDEuNjMgMCAuODQuMjUgMS41NS4yNC43MS43IDEuMjMuNDcuNTIgMS4xNi44Mi42OS4zIDEuNjIuM3pNNy41IDIxaDEyLjM5TDEyIDE2LjE4VjE3cTAgLjQxLS4zLjctLjI5LjMtLjcuM0g3LjV6bTE1LS4xM3YtNy40OWwtNi4zIDMuNzl6Ii8+PC9zdmc+Cg==&logoColor=white" alt="Outlook" height="34" />
  &nbsp;
  <img src="https://img.shields.io/badge/Gmail-EA4335?style=for-the-badge&logo=gmail&logoColor=white" alt="Gmail" height="34" />
  &nbsp;
  <img src="https://img.shields.io/badge/ChatGPT-10A37F?style=for-the-badge&logo=data:image/svg+xml;base64,PHN2ZyByb2xlPSJpbWciIHZpZXdCb3g9IjAgMCAyNCAyNCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cGF0aCBmaWxsPSJ3aGl0ZSIgZD0iTTIyLjI4MTkgOS44MjExYTUuOTg0NyA1Ljk4NDcgMCAwIDAtLjUxNTctNC45MTA4IDYuMDQ2MiA2LjA0NjIgMCAwIDAtNi41MDk4LTIuOUE2LjA2NTEgNi4wNjUxIDAgMCAwIDQuOTgwNyA0LjE4MThhNS45ODQ3IDUuOTg0NyAwIDAgMC0zLjk5NzcgMi45IDYuMDQ2MiA2LjA0NjIgMCAwIDAgLjc0MjcgNy4wOTY2IDUuOTggNS45OCAwIDAgMCAuNTExIDQuOTEwNyA2LjA1MSA2LjA1MSAwIDAgMCA2LjUxNDYgMi45MDAxQTUuOTg0NyA1Ljk4NDcgMCAwIDAgMTMuMjU5OSAyNGE2LjA1NTcgNi4wNTU3IDAgMCAwIDUuNzcxOC00LjIwNTggNS45ODk0IDUuOTg5NCAwIDAgMCAzLjk5NzctMi45MDAxIDYuMDU1NyA2LjA1NTcgMCAwIDAtLjc0NzUtNy4wNzI5em0tOS4wMjIgMTIuNjA4MWE0LjQ3NTUgNC40NzU1IDAgMCAxLTIuODc2NC0xLjA0MDhsLjE0MTktLjA4MDQgNC43NzgzLTIuNzU4MmEuNzk0OC43OTQ4IDAgMCAwIC4zOTI3LS42ODEzdi02LjczNjlsMi4wMiAxLjE2ODZhLjA3MS4wNzEgMCAwIDEgLjAzOC4wNTJ2NS41ODI2YTQuNTA0IDQuNTA0IDAgMCAxLTQuNDk0NSA0LjQ5NDR6bS05LjY2MDctNC4xMjU0YTQuNDcwOCA0LjQ3MDggMCAwIDEtLjUzNDYtMy4wMTM3bC4xNDIuMDg1MiA0Ljc4MyAyLjc1ODJhLjc3MTIuNzcxMiAwIDAgMCAuNzgwNiAwbDUuODQyOC0zLjM2ODV2Mi4zMzI0YS4wODA0LjA4MDQgMCAwIDEtLjAzMzIuMDYxNUw5Ljc0IDE5Ljk1MDJhNC40OTkyIDQuNDk5MiAwIDAgMS02LjE0MDgtMS42NDY0ek0yLjM0MDggNy44OTU2YTQuNDg1IDQuNDg1IDAgMCAxIDIuMzY1NS0xLjk3MjhWMTEuNmEuNzY2NC43NjY0IDAgMCAwIC4zODc5LjY3NjVsNS44MTQ0IDMuMzU0My0yLjAyMDEgMS4xNjg1YS4wNzU3LjA3NTcgMCAwIDEtLjA3MSAwbC00LjgzMDMtMi43ODY1QTQuNTA0IDQuNTA0IDAgMCAxIDIuMzQwOCA3Ljg3MnptMTYuNTk2MyAzLjg1NThMMTMuMTAzOCA4LjM2NCAxNS4xMTkyIDcuMmEuMDc1Ny4wNzU3IDAgMCAxIC4wNzEgMGw0LjgzMDMgMi43OTEzYTQuNDk0NCA0LjQ5NDQgMCAwIDEtLjY3NjUgOC4xMDQydi01LjY3NzJhLjc5Ljc5IDAgMCAwLS40MDctLjY2N3ptMi4wMTA3LTMuMDIzMWwtLjE0Mi0uMDg1Mi00Ljc3MzUtMi43ODE4YS43NzU5Ljc3NTkgMCAwIDAtLjc4NTQgMEw5LjQwOSA5LjIyOTdWNi44OTc0YS4wNjYyLjA2NjIgMCAwIDEgLjAyODQtLjA2MTVsNC44MzAzLTIuNzg2NmE0LjQ5OTIgNC40OTkyIDAgMCAxIDYuNjgwMiA0LjY2ek04LjMwNjUgMTIuODYzbC0yLjAyLTEuMTYzOGEuMDgwNC4wODA0IDAgMCAxLS4wMzgtLjA1NjdWNi4wNzQyYTQuNDk5MiA0LjQ5OTIgMCAwIDEgNy4zNzU3LTMuNDUzN2wtLjE0Mi4wODA1TDguNzA0IDUuNDU5YS43OTQ4Ljc5NDggMCAwIDAtLjM5MjcuNjgxM3ptMS4wOTc2LTIuMzY1NGwyLjYwMi0xLjQ5OTggMi42MDY5IDEuNDk5OHYyLjk5OTRsLTIuNTk3NCAxLjQ5OTctMi42MDY3LTEuNDk5N1oiLz48L3N2Zz4K&logoColor=white" alt="ChatGPT" height="34" />
  &nbsp;
  <img src="https://img.shields.io/badge/Grok-000000?style=for-the-badge&logo=x&logoColor=white" alt="Grok" height="34" />
  &nbsp;
  <img src="https://img.shields.io/badge/Claude-D97757?style=for-the-badge&logo=anthropic&logoColor=white" alt="Claude" height="34" />
  &nbsp;
  <img src="https://img.shields.io/badge/Gemini-886FBF?style=for-the-badge&logo=googlegemini&logoColor=white" alt="Gemini" height="34" />
  &nbsp;
  <img src="https://img.shields.io/badge/Google%20One-4285F4?style=for-the-badge&logo=googleone&logoColor=white" alt="Google One" height="34" />
</p>

<p>
  <img src="https://img.shields.io/badge/QQ%E7%BE%A4-1048143135-12B7F5?style=for-the-badge&logo=qq&logoColor=white" alt="QQ 交流群 1048143135" />
  &nbsp;
  <a href="https://t.me/TIANTIANAIPRO">
    <img src="https://img.shields.io/badge/Telegram-@TIANTIANAIPRO-26A5E4?style=for-the-badge&logo=telegram&logoColor=white" alt="Telegram @TIANTIANAIPRO" />
  </a>
</p>

<p>
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/Playwright-自动化-2EAD33?style=flat-square" alt="Playwright" />
  <img src="https://img.shields.io/badge/BitBrowser%20%2F%20AdsPower-指纹隔离-5A4FCF?style=flat-square" alt="Fingerprint Browser" />
  <img src="https://img.shields.io/badge/Clash%20Verge-节点切换-1F8FFF?style=flat-square" alt="Clash Verge" />
  <img src="https://img.shields.io/badge/license-educational-lightgrey?style=flat-square" alt="license" />
</p>

</div>

---

**reg-factory** 是一套全自动注册流水线：先自注册 **Outlook** 邮箱，再用同一邮箱在
**ChatGPT / Grok / Claude** 上批量注册账号，并导出可直登的 cookie。底层用
**比特浏览器(BitBrowser) / AdsPower** 做指纹隔离、**Clash Verge** 做节点切换绕区域封锁与 Cloudflare 风控、
接码/打码平台过手机号与验证码。

> 🔜 即将上新：**Gmail 注册机 → Google One 授权 → SUB2API / CPA 导入**完整链路。

> ⚠️ 仅供学习与授权测试使用。所有密钥通过环境变量提供，仓库内不含任何明文凭据。

---

## 1. 前置条件

### ① 指纹浏览器（二选一）

**选项 A：BitBrowser（默认）**
- 安装并**启动**比特浏览器客户端，确保本地 API 在线（默认 `http://127.0.0.1:54345`）。
- `.env` 保持 `FINGERPRINT_BROWSER=bitbrowser`，或不填该项。

**选项 B：AdsPower**
- 安装并**启动** AdsPower 客户端，开启 Local API（默认 `http://127.0.0.1:50325`）。
- 在 `.env` 设置 `FINGERPRINT_BROWSER=adspower`，并按本机 AdsPower 配置填写 `ADSPOWER_API_KEY`（启用鉴权时必填）。

客户端要保持运行——脚本通过本地 API 创建/打开/关闭浏览器窗口。

### ② Clash Verge（开启 API 权限）
- 安装 Clash Verge 并导入你的机场订阅，选一个节点并开启「系统代理 / Tun 模式」。
  - 注册 Grok 需要能过 Cloudflare 的干净节点；脚本会在订阅节点里自动逐个试探可用节点。
- **设置 → External Controller**：开启外部控制器 API，并**设置一个 secret**。
  - 记下控制面端口（Clash Verge 默认 `9097`，mihomo 内核默认 `9090`）。
  - 记下混合代理端口（mixed-port，默认 `7897`）。
- 把 secret 填进 `.env` 的 `CLASH_SECRET`（见下）。

### ③ Python
- Python 3.10+。

### ④ 注册 Gmail / 谷歌邮箱时的 Android 环境（按需）
- BlueStacks：用于运行 Gmail Android App，建议使用固定版本；Release 包可选择附带安装器。
- Android Studio 或 Android SDK Platform Tools：至少需要 `adb` 可用。只安装 `platform-tools` 也可以，不强制完整安装 Android Studio。
- Node.js 20+、Appium 2.x、Appium UiAutomator2 driver：用于驱动 Android UI 自动化。
- 模拟器内需要安装 Gmail App，并开启 BlueStacks ADB，默认连接地址为 `127.0.0.1:5675`。
- 相关脚本在 `gmail_android/scripts/` 下；小体积 zip 只包含代码和安装脚本，不包含 BlueStacks / Android SDK / Node / Appium 等大二进制。

---

## 0. 图形界面 / 一键安装（推荐新手）

### Codex K12 控制台

`codex_k12/` 是 reg-factory 内置的 Codex/K12 运营控制台，用于统一管理已授权的 K12 workspace 任务、邮箱资产、Codex 凭据和下游账号。它采用 Vue + TypeScript 前端与本地 Node API，运行数据和主 Python 流程隔离，但可以直接复用仓库已有的 `emails.txt` 邮箱池。

**主要能力**

- 邮箱资产：批量导入、状态管理、手动/自动 OTP，以及从主仓库邮箱池增量同步。
- K12 任务：按 workspace 编排任务，支持并发、队列、取消、重试、分页日志和结果追踪。
- Codex / Sub2API：OAuth 或 noRT 入库、账号 JSON 写出、AT 测活与修复。
- 运营管理：自动补号、失败任务清理、数据包导入导出和浏览器租户隔离。
- 安全默认：只监听 `127.0.0.1`，不预置 workspace ID、代理或通用密码，配置接口不返回密钥原文。

**与主项目的集成方式**

```text
start.bat
  -> 主 WebUI http://127.0.0.1:8799/
  -> 自动启动 Codex K12 http://127.0.0.1:8806/
  -> 左侧“Codex K12”通道内嵌控制台
```

主 WebUI 会检测 K12 健康状态、按需启动本地服务，并在退出时回收由它创建的 K12 子进程。K12 运行数据仍独立保存在 `codex_k12/data/`，不会写入主面板的 Python 运行目录。

**首次使用**

1. 运行 `install.bat` / `install.sh`，安装 Python 依赖并构建 K12 前端。
2. 运行 `start.bat` / `start.sh`，从主面板左侧进入“Codex K12”。
3. 在 K12 设置中填写你有权使用的 Workspace ID 和网络出口；本机直连可填写 `direct`。
4. 按需配置邮箱 API、Sub2API、输出格式和补号策略，再同步主仓库邮箱池。

`start_k12.bat` 可用于独立启动。主仓库邮箱同步只读取 `emails.txt`，不会改写原文件；账号、任务、token 和 JSON 输出均位于已忽略的 K12 数据目录。完整配置、开发命令和目录说明见 [`codex_k12/README.md`](codex_k12/README.md)。

不想敲命令行？用自带的 **Web 控制面板** + **一键安装脚本**：

**Windows**

```
1. 双击 install.bat   ——  自动建虚拟环境、装依赖、装 Playwright Chromium、生成 .env
2. 打开 BitBrowser/AdsPower 和 Clash Verge 客户端
3. 双击 start.bat     ——  自动启动面板并打开浏览器（http://127.0.0.1:8799）
4. 后续双击 update.bat ——  检查任务、更新代码和依赖、重启并验证面板版本
```

**macOS / Linux**

```bash
./install.sh     # 同上：venv + 依赖 + 浏览器内核 + .env
./start.sh       # 启动面板并打开浏览器
./update.sh      # 安全更新，运行中有注册任务时会拒绝更新
```

### 一行命令下载安装 / 启动

默认安装到用户目录下的 `reg-factory`。脚本检测到 Git 时使用 clone/pull，没有 Git 时自动下载
GitHub main 分支压缩包。可先打开下方脚本直链审阅内容再执行。

**Windows PowerShell**

```powershell
# 一键下载安装
$env:REG_FACTORY_ACTION="install"; irm "https://raw.githubusercontent.com/tiantianGPU/reg-factory/main/bootstrap.ps1" | iex

# 一键启动
$env:REG_FACTORY_ACTION="start"; irm "https://raw.githubusercontent.com/tiantianGPU/reg-factory/main/bootstrap.ps1" | iex

# 一键更新并重启
$env:REG_FACTORY_ACTION="update"; irm "https://raw.githubusercontent.com/tiantianGPU/reg-factory/main/bootstrap.ps1" | iex
```

**macOS / Linux**

```bash
# 一键下载安装
curl -fsSL "https://raw.githubusercontent.com/tiantianGPU/reg-factory/main/bootstrap.sh" | bash -s -- install

# 一键启动
curl -fsSL "https://raw.githubusercontent.com/tiantianGPU/reg-factory/main/bootstrap.sh" | bash -s -- start

# 一键更新并重启
curl -fsSL "https://raw.githubusercontent.com/tiantianGPU/reg-factory/main/bootstrap.sh" | bash -s -- update
```

脚本直链：[Windows `bootstrap.ps1`](https://raw.githubusercontent.com/tiantianGPU/reg-factory/main/bootstrap.ps1) ·
[macOS/Linux `bootstrap.sh`](https://raw.githubusercontent.com/tiantianGPU/reg-factory/main/bootstrap.sh)

**面板能做什么**

- 顶部状态灯：指纹浏览器 / Clash 是否在线 + 当前节点（实时刷新）。
- 左侧按分类列出全部脚本（主流程 / 单平台注册 / 养号·邮箱 / 导出·上传）。
- 点脚本 → 自动生成参数表单（勾选框 / 下拉 / 多选 / 输入框）→ 点「运行」→ 实时日志，可随时「停止」。
- **⚙️ 配置(.env)** 页：分组填写所有密钥（密码框遮挡），每类带**连通测试按钮**——
  Clash（验证控制器 + secret）、指纹浏览器、sms-man / firefox.fun 接码平台，一键看通不通。
  指纹浏览器 provider 可在页面里用下拉框切换 `bitbrowser` / `adspower`。
- **Claude 注册与验证**配置组：集中设置节点探测、Cloudflare 等待、hCaptcha 重试、视觉网关/模型和浏览器内核；单任务页可直接填写与 refresh token 配套的 `client-id`。
- 桌面宽屏使用参数/实时日志双栏，任务结束后显示真实的成功、失败或停止状态；移动端保持单栏和抽屉式任务导航。
- 保存 `.env` 后，后续新任务和面板管理的子服务立即使用新配置，无需重启主服务。
- 更新代码请使用 `update.bat` / `update.sh`（或上面的一键更新命令）；脚本会拒绝中断运行中的注册任务，并在更新依赖后重启、核对 WebUI 提交版本，避免页面仍使用旧 schema。
- 移动端顶栏可直接打开 Telegram 联系二维码。
- 仅监听 `127.0.0.1`，含密钥不暴露公网。

> 面板只是给现有命令行脚本套了个壳：拼好命令 → 起子进程 → 收实时输出，行为与下面的 CLI 完全一致。
> 想用命令行 / 进阶用法看下面各节。

---

## 2. 安装（命令行）

```bash
pip install -r requirements.txt
playwright install chromium
```

> 或直接用上面的一键安装脚本（`install.bat` / `install.sh`），等价于这两条 + 建 venv + 生成 .env。

---

## 3. 配置（密钥走环境变量）

复制模板并填写：

```bash
cp .env.example .env
```

`.env` 已被 `.gitignore` 忽略。真实的进程环境变量优先于 `.env`。

| 环境变量 | 说明 | 必填 |
|---|---|---|
| `CLASH_SECRET` | Clash Verge External Controller 的 secret | 走节点时必填 |
| `CLASH_API` | Clash 控制面地址（默认 `http://127.0.0.1:9097`） | 否 |
| `CLASH_PROXY` | Clash 混合端口代理（默认 `http://127.0.0.1:7897`） | 否 |
| `CLASH_GROUP` | 切换出口的代理组名（默认 `GLOBAL`） | 否 |
| `FINGERPRINT_BROWSER` | 指纹浏览器 provider：`bitbrowser` / `adspower`（默认 `bitbrowser`） | 否 |
| `BITBROWSER_API` | 比特浏览器本地 API（默认 `http://127.0.0.1:54345`） | 否 |
| `GROK_BROWSER_CORE_VERSION` | Grok 浏览器 Chromium 内核（默认 `146`；旧 `130` 会触发 xAI 发码 403） | 否 |
| `ADSPOWER_API` | AdsPower 本地 API（默认 `http://127.0.0.1:50325`） | 使用 AdsPower 时 |
| `ADSPOWER_API_KEY` | AdsPower Local API 鉴权 key（未启用鉴权可留空） | 否 |
| `ADSPOWER_GROUP_ID` | AdsPower 新建 profile 的分组 ID（默认 `0`） | 否 |
| `CLAUDE_CHALLENGE_WAIT_SECONDS` | Claude 每个节点等待 Cloudflare 自动验证的秒数（默认 `45`） | 否 |
| `CLAUDE_CHALLENGE_NODE_RETRIES` | Claude 自动模式提交邮箱前的节点轮换次数（默认 `3`） | 否 |
| `CLAUDE_CAPTCHA_MANUAL_TIMEOUT` | 图形验证人工接管等待秒数（默认 `0` 关闭；可设 `180`） | 否 |
| `CLAUDE_HCAPTCHA_SOLVE_RETRIES` | Claude magic-link hCaptcha 的 YesCaptcha 重试次数（默认 `2`） | 否 |
| `CLAUDE_VISION_API_BASE` / `CLAUDE_VISION_API_KEY` | Claude hCaptcha 专用视觉网关；留空时复用通用 `VISION_*` | 自动视觉求解时 |
| `CLAUDE_VISION_MODEL` | Claude hCaptcha 主视觉模型（默认 `gemini-3.6-flash`） | 否 |
| `CLAUDE_BROWSER_CORE_VERSION` | Claude 注册和 sessionKey 校验使用的 Chromium 指纹（默认 `146`） | 否 |
| `CLAUDE_NODE_PROBE_LIMIT` / `CLAUDE_NODE_PROBE_TIMEOUT_SECONDS` | 启动时快速探测的新节点数 / 单节点超时（默认 `6` / `8` 秒） | 否 |
| `SMS_TOKEN` | 接码平台 firefox.fun 的 token | 需手机号时必填 |
| `HERO_SMS_API_KEY` | 备用接码 hero-sms.com 的 api_key | 否 |
| `YESCAPTCHA_API_KEY` | YesCaptcha key（Claude hCaptcha 备用、Grok Turnstile、GitHub Arkose） | 按需 |
| `CAPSOLVER_API_KEY` | CapSolver 打码 key（Grok Turnstile 回退） | Grok 与 YesCaptcha 二选一 |
| `EZCAPTCHA_API_KEY` | EZ-Captcha 打码 key | 按需 |
| `OUTLOOK_PROXIES` | Outlook 自注册住宅代理池，`user:pass@host:port`，换行/逗号分隔 | 否 |
| `OUTLOOK_UI_LOCALE` | Outlook 注册及 Graph OAuth 的首选界面语言（默认 `en-US`，页面忽略时自动按实际语言适配） | 否 |
| `MAIL_*` | 备用域名邮箱（一般用不到） | 否 |

**Codex / 标准 token 上传（按需启用，留空自动跳过）**

| 环境变量 | 说明 | 必填 |
|---|---|---|
| `CLAUDE_SUB_URL` / `GROK_SUB_URL` | Claude / SuperGrok 订阅入口（CDK 激活流程 🔜 敬请期待） | 否 |
| `CLAUDE_SUB_CDK` / `GROK_SUB_CDK` | Claude / SuperGrok 激活码 CDK 池（预留） | 否 |
| `CPA_URL` / `CPA_MGMT_KEY` | CPA 管理接口（codex 授权文件导入） | 用 CPA 时 |
| `SUB2API_URL` / `SUB2API_EMAIL` / `SUB2API_PASSWORD` | SUB2API 管理接口登录 | 用 SUB2API 时 |
| `SUB2API_GROUP` | Codex 目标分组名（默认 `codex`，平台需为 `openai`） | 用 Codex 时 |
| `SUB2API_GROK_GROUP` | Grok 目标分组名（默认 `grok`，平台必须为 `grok`） | 用 Grok 导入时 |
| `SUB2API_GROK_PROXY_ID` | Grok SSO 转 OAuth 使用的 SUB2API 代理 ID（默认 `0` 不指定） | 按需 |
| `WEBCHAT2API_URL` / `WEBCHAT2API_KEY` | webchat2api（Grok sso 注入） | 用 Grok 时 |
| `CHATGPT2API_URL` / `CHATGPT2API_KEY` | chatgpt2api 普通网页号导入（`POST /api/accounts`，Bearer admin key） | 用 `--import-c2a` 时 |
| `SMSMAN_TOKEN` / `SMSMAN_APP_ID_OPENAI` | sms-man.com 接码（Codex add-phone 主用，OpenAI 服务 id=2754） | 用 `--codex` 自动接码时 |
| `SMS_PROJECT_ID_OPENAI` / `HERO_SMS_SERVICE_OPENAI` | add-phone 回退接码服务号（firefox.fun / hero-sms） | 回退接码时 |

---

## 4. 运行

### 端到端（注册邮箱 → 三平台注册）
```bash
python run_full_flow.py                       # 注册 1 个 outlook 号后在 claude 上注册
python run_full_flow.py --platforms claude chatgpt grok
python run_full_flow.py --platforms grok --grok-sub2api  # Grok 注册后直接导入 SUB2API Grok 渠道
python run_full_flow.py --platforms chatgpt --import-c2a   # chatgpt 注册成功后即时导入 chatgpt2api
python run_full_flow.py --platforms chatgpt --email-confirm-before-register  # Outlook 注册页自动点确认后再填写
python run_full_flow.py --skip-email --email a@outlook.com --password xxx
python run_full_flow.py --dry-run             # 只打印将执行的命令
```
> 自动注入 `HTTP(S)_PROXY` 与 `CLASH_API/SECRET/GROUP` 给子进程。
> `--import-c2a` 逐层透传到 `register_chatgpt.py`，只对 chatgpt 平台生效，需先配 `CHATGPT2API_URL/KEY`。
> `--email-confirm-before-register` 会在 Outlook 注册页打开后自动点击确认/同意类按钮，再开始填写。

### 仅三平台注册（已有邮箱池 emails.txt）
```bash
python register_three_platforms.py --from-pool
python register_three_platforms.py --email a@outlook.com --password xxx --token <refresh>
python register_three_platforms.py --loop     # 常驻消费池
```
并行流水线模式下建议先起共享取码服务（避免三窗口并发登录同一邮箱）：
```bash
python mailbox_broker.py --port 8765
```

### 仅养号（持续自注册 Outlook，写入 _outlook_pool/ 与 emails.txt）
```bash
python outlook_reg_loop.py                     # 循环
python outlook_reg_loop.py --count 20          # 注册 20 个后退出
python outlook_reg_loop.py --confirm-before-register --max-press 10 --timeout 300
python outlook_reg_loop.py --no-rotate         # 固定当前节点，不轮换
python register_outlook_standalone.py --count 5 --mode browser --confirm-before-register
```
> Outlook 自注册成功后会立即提取 Microsoft Graph `refresh_token`；只有拿到 RT 的账号才写入 `_outlook_pool/` 与 `emails.txt`。
> `emails.txt` / `outlook_accounts/accounts_*.txt` 格式为 `email----password----refresh_token----client_id`。
>
> **人机验证（PerimeterX 按住）**：按住动作用拟人鼠标（`common/human_mouse.py`：WindMouse 逼近轨迹 + Ornstein-Uhlenbeck 生理震颤），取代旧的正弦/均匀抖动，实测可稳定过验证。抖动幅度可用 `HUMAN_MOUSE_TREMOR_PX` 调，`HUMAN_MOUSE_DEBUG=1` 打印运动统计。
> **节点轮换**：每次 attempt 前先探测 Clash 节点 `/delay`，跳过超时/过慢节点，在可用节点里挑延迟最低的再切换（`CLASH_MAX_LATENCY_MS` / `CLASH_PROBE_BATCH` 可调）。加 `--no-rotate`（或 `OUTLOOK_NO_ROTATE=1`）则固定当前节点、不切换。

### `agent-captcha` 视觉投票求解器

`common/agent_captcha.py` 提供独立的 Arkose 视觉验证码求解内核：

- 按题目文本自动识别 `sequence`、`rotate`、`character`、`wires` 等挑战变体。
- 将候选图拼接、编号并用 PIL 本地增强，在控制传输体积的同时保留判题细节。
- 并发请求多个多模态模型，通过多数投票选择答案，再由 driver 执行点击、导航或提交。
- 生成带最终选择和各模型投票的复盘标注图，便于定位误判和调整 prompt。
- 网关、模型和密钥统一读取 `.env` 中的 `VISION_*`、`VOTE_*`、`IMAGE_EDIT_*` 配置。

### 通用验证码求解库 `vision_solver/`（过 hCaptcha 等）

`vision_solver/` 将 `agent-captcha` 的投票内核泛化成独立库：用 `CaptchaSpec`（frame / 选择器 / prompt / 交互模式）描述**任意一类**验证码，不写死平台，多模型视觉投票求解，通用 driver 据答案驱动浏览器。

**新增解新版 hCaptcha** —— 实测发现现代 hCaptcha 把整个挑战**渲染进单个 `<canvas>`**（没有任何可点的 DOM tile），传统「枚举 tile 点击」无效。本库提供两种画布 driver：

- **`canvas_grid`（点击型）**：稳定截画布 → 按行列叠加编号网格（四边内缩对齐真实图块）→ 多模型投票 → **按像素坐标点 canvas** → 提交。题干自动判单选（`the item/thing`）/ 卡片（`card/different`）/ 多选（`select all`）。
- **`canvas_drag`（拖拽型）**：多模型投票 `FROM/TO` 归一化坐标（取中位数抗离群）→ **鼠标分步带抖动拖放**（模拟人手）→ 提交。
- **Claude 重复题型优化**：按多语言题干识别动物、珠链和线段题；珠链可用本地图像几何检测复核最短两组，线段题接受两个端点坐标并直接点击，长度比较题可启用多模型投票降低单模型误判。

```python
from vision_solver import solve, CaptchaSpec

# 验证码已在页面触发后：
spec = CaptchaSpec.from_json("vision_solver/presets/hcaptcha.json")   # 点击型
ok = await solve(page, spec)        # True=通过
```

```bash
# 对 hCaptcha 官方 demo 跑端到端 / 三 sitekey 鲁棒性跑分（复盘图落 screenshots_vision/）
python vision_solver/tests/test_hcaptcha_live.py
python vision_solver/tests/test_hcaptcha_robust.py 3
```

> 实测：点击型对 hCaptcha demo 三个测试 sitekey 各 3 轮 **8/9 通过**（修复空选卡死后全过）；拖拽机制经本地合成 canvas 谜题验证 **3/3**（该 demo 不下发拖拽题）。投票网关/key 复用现有 `VISION_*` / `VOTE_*`（`.env`）。详见 `vision_solver/README.md`。


### 导出已注册账号 cookie（供直登扩展使用）
```bash
python export_accounts.py                      # 全部平台
python export_accounts.py claude chatgpt       # 指定平台
```

### 批量解锁被锁的 Outlook 账号
指纹浏览器 + Playwright,复用注册同款 PX 按压验证逻辑;按结果分类输出到
`unlock_results/`(`unlocked_*` 成功 / `needs_phone_*` 需短信 / `failed_*` 失败)。
打码 key 走环境变量 `EZCAPTCHA_API_KEY`。
```bash
python unlock_outlook.py                                       # 自动找最新的 locked 文件
python unlock_outlook.py --input outlook_accounts/accounts.txt # 指定账号文件
python unlock_outlook.py --input emails_locked.txt --concurrency 2
python unlock_outlook.py --input accounts.txt --proxy-file proxies.txt
```
> 输入每行 `email----password`（可带额外字段）。解锁后再跑下面的 token 提取。

### 提取 Outlook 的 Graph OAuth refresh_token
纯 `requests` 模拟 OAuth2 授权码流程（免浏览器），用账号密码换取
Microsoft Graph 的 `refresh_token`，输出 `email----password----refresh_token----client_id`，
结果存到 `outlook_accounts/graph_tokens_<时间戳>.txt`。
```bash
python extract_graph_tokens.py                                   # 自动扫 unlock_results/，跳过已提取
python extract_graph_tokens.py outlook_accounts/accounts.txt     # 指定账号文件
python extract_graph_tokens.py --email a@outlook.com --password xxx
python extract_graph_tokens.py accounts.txt --concurrency 10     # 并发数(默认 5)
```
> 走系统代理（Clash），避免 `account.live.com` 限流；账号文件每行 `email----password----...`。
> OAuth 页面会优先请求 `OUTLOOK_UI_LOCALE`（默认 `en-US`），同时兼容出口 IP 返回的中、英、法、西、德、葡、意、荷、波、俄、土、日、韩页面。

### Clash 节点自检
```bash
python -m common.proxy_switch list             # 列出 GLOBAL 组节点
python _clash_verge.py ping                    # 控制面连通性
```

---


---

## Gmail Android / Appium 本地注册包

Gmail Android 模块位于 `gmail_android/`。它用于在本机 BlueStacks + Appium 环境里驱动 Gmail Android 注册流程，并把 Android 环境安装脚本一起打包，方便后续作为 GitHub Release 附件分发。

安全边界：
- 默认停在手机/SMS/CAPTCHA 或 Google 额外安全验证页，由人工完成。
- `--resume-after-phone` 用于人工验证完成后续跑。
- `--accept-terms` 只在操作者明确同意 Google Privacy and Terms 后使用。
- `sms_provider.py` 只提供环境变量驱动的 provider 骨架，后续合并内部合规接码流程时复用；当前不默认接入 Gmail 安全验证自动化。

### GitHub Release 安装包

Release 上传建议：

```text
gmail-android-local-with-bluestacks.zip
```

包内结构：

```text
gmail_android/
  gmail_register_local.py
  appium_api.py
  config.py
  sms_provider.py
  .env.example
  requirements.txt
  scripts/
    install_all_windows.ps1
    install_bluestacks.ps1
    install_windows.ps1
    start_appium.ps1
    check_env.ps1
    run_gmail_register.ps1
  offline/
    bluestacks/
      BlueStacksInstaller_*.exe   # 可选；推荐固定版本安装器
```

当前脚本支持 BlueStacks 直接安装版：把固定版本 BlueStacks 安装器放入 `gmail_android/offline/bluestacks/`，用户解压后运行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
cd gmail_android
.\scripts\install_all_windows.ps1
.\scripts\start_appium.ps1
.\scripts\check_env.ps1
```

注意：如果包内 BlueStacks 安装器是 web/micro installer，目标电脑仍需要联网下载 BlueStacks 组件。完全离线发布时，请替换为官方 full/offline installer 后再打包。

构建 GitHub Release zip：

```powershell
cd gmail_android
.\scripts\build_release.ps1

# 带固定版本 BlueStacks 安装器
.\scripts\build_release.ps1 -BlueStacksInstaller C:\path\to\BlueStacksInstaller.exe
```

上传到 GitHub Release：

1. 先运行上面的 `build_release.ps1`，生成 `gmail_android/dist/gmail-android-local.zip`。
2. 打开 GitHub 仓库页面，进入 `Releases` -> `Draft a new release`。
3. 新建 tag，例如 `gmail-android-v2026.06.06`。
4. 把 `gmail_android/dist/gmail-android-local.zip` 拖到 release 附件区域，然后发布。

也可以用 GitHub CLI 上传：

```powershell
cd E:\reg-factory
gh auth login
gh release create gmail-android-v2026.06.06 `
  .\gmail_android\dist\gmail-android-local.zip `
  --title "Gmail Android Local Package" `
  --notes "Gmail Android/Appium local installer package."

# 如果 release 已经存在，改用 upload
gh release upload gmail-android-v2026.06.06 `
  .\gmail_android\dist\gmail-android-local.zip `
  --clobber
```

### Gmail Android 使用

复制环境模板：

```powershell
cd gmail_android
copy .env.example .env
```

关键变量也可写入仓库根目录 `.env`：

```env
APPIUM_SERVER=http://127.0.0.1:4723
ANDROID_DEVICE=127.0.0.1:5675
GMAIL_USERNAME_PREFIX=
ACCEPT_TERMS=0
SMS_PROJECT_ID_GMAIL=
HERO_SMS_SERVICE_GMAIL=
```

运行：

```powershell
# 默认跑到手机/安全验证处停住
python .\gmail_register_local.py

# 人工完成手机验证后续跑
python .\gmail_register_local.py --resume-after-phone

# 明确同意条款后，让脚本继续点击 I agree / ACCEPT 并进入 Gmail
python .\gmail_register_local.py --resume-after-phone --accept-terms

# 也可以让脚本等待人工手机验证完成后自动继续
python .\gmail_register_local.py --wait-phone-verification --accept-terms
```
## 5. SUB2API 授权 & 标准 token 上传

注册拿到的是**网页 session**（无 `refresh_token`，下游中转易 401）。这一组流程把账号升级成
带 `refresh_token` 的正式凭据，并灌到下游中转（SUB2API / CPA）。

**前置条件**
- 已用 `register_chatgpt.py` 注册过该账号，`cookies/chatgpt/full_*.json` 存在（①靠它重登）。
- `.env` 配好 `SUB2API_URL/EMAIL/PASSWORD`（①必需），可选 `CPA_URL/CPA_MGMT_KEY`（推 CPA）。
- `.env` 配好接码：`SMSMAN_TOKEN`（主用，自动过 add-phone），可选 firefox/hero 兜底。

**典型一条龙**
```bash
# 注册即授权：邮箱注册 → CF 过墙 → ChatGPT 注册 → add-phone 接码 → OAuth → SUB2API
python run_full_flow.py --platforms chatgpt --codex

# 或对已注册账号单独跑 OAuth 授权（默认最新 cookie，自动接码过 add-phone）
python oauth_codex.py --keep
```

### ① Codex OAuth 授权 → SUB2API + CPA（带 refresh_token）
用已存 cookie 重登账号，走 Codex CLI OAuth 换取**带 `refresh_token` 的正式凭据**，同时建到
SUB2API（type=oauth）并推到 CPA。授权时若遇 OpenAI 的 **add-phone** 手机验证：
- 默认走接码平台（**sms-man 主用**）自动接 SMS 过号；OpenAI 默认 WhatsApp 投递，脚本会自动切到 SMS。
- 手机号大概率被风控拒，脚本自动换号重试（最多 `CODEX_ADDPHONE_ATTEMPTS` 次，默认 8）。
- `--manual-phone`：**手动模式**，脚本停在输号页，由你在浏览器里自己填号 + 输验证码。
```bash
python oauth_codex.py                            # 默认最新 cookie，自动接码（sms-man）
python oauth_codex.py --manual-phone --keep      # 手动填号 + 输码
python oauth_codex.py --cookie cookies/chatgpt/full_xxx.json --skip-cpa
```

### ② 批量上传本地标准 token
注册脚本默认把 token 落到 `tokens/`；未启用即时导入时可在这里批量补传，成功的 email 会记账跳过。
```bash
python upload_tokens.py            # all（chatgpt + grok）
python upload_tokens.py chatgpt    # 只传 ChatGPT（CPA + SUB2API）
python upload_tokens.py grok       # 只传 Grok（SUB2API + webchat2api，按已配置目标执行）
```
> ⚠️ ChatGPT 这条是 **Path A（兜底）**：从网页 session 上传，**无 `refresh_token`**（CPA 用合成
> id_token），下游过期不能续期。**Codex 进 SUB2API/CPA 的 OAuth 路径是上面 ① 的 `oauth_codex.py`（带真
> `refresh_token`）**；本路径仅供没走 OAuth 的批量兜底。

### ③ Grok 注册 → SUB2API Grok OAuth

SUB2API 当前提供 Grok SSO 转 OAuth 接口。注册脚本拿到 Web SSO 后可立即兑换成带
`refresh_token` 的 Grok OAuth 账号，并绑定到 Grok 渠道分组。先在 SUB2API 后台创建
`platform=grok` 的分组，再配置 `SUB2API_GROK_GROUP`；同名的 OpenAI 分组不会被误用。
该流程要求部署的 SUB2API 版本已提供 `/api/v1/admin/grok/sso-to-oauth` 接口。
若 SUB2API 所在服务器不能直连 xAI，可把后台代理列表中的 ID 填到 `SUB2API_GROK_PROXY_ID`。
若未配置后台代理且远端转换被 xAI 返回 403，脚本会自动用本机 `CLASH_PROXY` 完成 OAuth，
再通过 SUB2API 管理接口创建 Grok 账号。

```bash
# 单平台注册并立即导入
python register_grok_http.py --count 1 --sub2api

# 指纹浏览器版对照流程（默认仍推荐上面的纯 HTTP 版）
python register_grok.py --count 1 --node auto

# 使用邮箱池中最新带 refresh token 的 Outlook，Graph API 取验证码
python register_grok.py --count 1 --node auto --latest-rt

# Claude 使用最新有效 RT 邮箱，Graph API 收 magic link
python register.py --count 1 --node auto --latest-rt

# 指定邮箱时，refresh token 与实际签发它的 client_id 必须配套
python register.py --email a@outlook.com --password xxx --token <refresh> --client-id <client_id> --node auto

# 指定 Grok 分组
python register_grok_http.py --count 1 --sub2api --sub2api-group grok

# Outlook → 平台编排中的 Grok 全流程
python run_full_flow.py --platforms grok --grok-sub2api

# 下游临时失败时无需重注册；SSO 已保存，可事后补传
python upload_tokens.py grok
```

启用 `--sub2api` 后，只有“Grok 建号 + SSO 落盘 + SUB2API OAuth 创建”全部成功才计为
本轮成功。SUB2API 暂时不可用时脚本返回非零，但本地 `tokens/grok/*.sso.json` 会保留。

### ④ 普通 ChatGPT 网页号 → chatgpt2api
普通网页号（非 codex/OAuth 三件套）单独走 chatgpt2api（basketikun/chatgpt2api）。注册成功时
顺手落 `tokens/chatgpt/c2a-*.json`；上传两种方式：
```bash
# 方式 A：注册时即时导入（推荐，需先配 CHATGPT2API_URL/KEY）
python register_chatgpt.py --count 5 --import-c2a
python run_full_flow.py --platforms chatgpt --import-c2a

# 方式 B：事后批量聚合上传 / 导出
python export_chatgpt2api.py --post https://<host> --key <admin>   # 直接 POST /api/accounts
python export_chatgpt2api.py                                       # 导出 access_token 列表（粘进批量框）
python export_chatgpt2api.py --json                                # 导出 {accounts:[...]} JSON
```
> 只认 `access_token`（**不带 `type:"codex"`**，否则被对端当 codex 源）。网页号无真 `refresh_token`，
> access_token 约 10 天过期后对端续不了命，属预期。重复 token 对端按 skipped 幂等处理。

### ⑤ Claude / SuperGrok CDK 订阅授权 🔜
订阅入口走环境变量 `CLAUDE_SUB_URL`（`https://6661231.xyz/#/claude`）、
`GROK_SUB_URL`（`https://6661231.xyz/#/grok`）。**激活码 CDK 流程 + 授权到 SUB2API / CPA
敬请期待**，CDK 池预留 `CLAUDE_SUB_CDK` / `GROK_SUB_CDK`。

### ⑥ Gmail 注册机 → Google One 授权 → SUB2API / CPA 🔜
后续将加入完整链路：**Gmail 自动注册机 → Google One 授权 → SUB2API / CPA 导入**，
覆盖账号注册、订阅授权与下游中转接入。

---

## 6. 项目结构 / 模块职责

> 多人协作速查：入口脚本在根目录，可复用基建在 `common/`。所有密钥走环境变量（`config.py` 统一读取）。

**入口脚本（根目录）**

| 脚本 | 职责 |
|---|---|
| `run_full_flow.py` | 端到端编排：注册邮箱 → 三平台注册 |
| `register_three_platforms.py` | 三平台（Claude/ChatGPT/Grok）注册编排 |
| `register.py` / `register_chatgpt.py` | Claude / ChatGPT 注册主流程 |
| `register_grok_http.py` | Grok 注册主流程（纯 HTTP；支持 SSO → SUB2API Grok OAuth 即时导入） |
| `outlook_reg_loop.py` / `register_outlook_standalone.py` | Outlook 自注册养号 |
| `unlock_outlook.py` / `extract_graph_tokens.py` | Outlook 解锁 / 提取 Graph refresh_token |
| `oauth_codex.py` | Codex OAuth → SUB2API + CPA（带 refresh_token，自动接码过 add-phone，支持 `--manual-phone`） |
| `upload_tokens.py` | 把 `tokens/` 标准 token 上传到 CPA / SUB2API / webchat2api |
| `export_chatgpt2api.py` | 聚合普通网页号 → chatgpt2api 导入（`--post` 直传 / 导出 txt/json） |
| `export_accounts.py` | 导出已注册账号 cookie |
| `mailbox_broker.py` | 共享取码服务（避免并发登录同一邮箱） |

**可复用基建（`common/`）**

| 模块 | 职责 |
|---|---|
| `browser.py` | BitBrowser/AdsPower 连接、stealth、React 受控输入 |
| `mailbox.py` / `emails.py` | 邮箱取码（Graph/浏览器）、邮箱池管理 |
| `cookies.py` | 平台 cookie 保存 |
| `sms.py` | 参数化接码客户端（sms-man 主用 + firefox.fun + hero-sms 兜底） |
| `oauth_codex.py` | Codex OAuth 授权驱动、add-phone 处理、SUB2API 调用 |
| `session_export.py` | 登录态导出成 CPA / SUB2API 标准 token（对齐 FlowPilot） |
| `uploaders.py` | 上传到 CPA / SUB2API（含 Grok SSO 转 OAuth）/ webchat2api |
| `proxy_switch.py` | Clash 节点切换 |
| `agent_captcha.py` | Arkose 验证视觉投票求解器：变体分派 + 多模型并发投票 + 图片增强/拼接 + 复盘标注 |

**通用验证码求解库（`vision_solver/`）**

| 模块 | 职责 |
|---|---|
| `schema.py` | `CaptchaSpec`：用 frame/选择器/prompt/交互模式描述一类验证码（4 mode），不写死平台 |
| `vision.py` | 视觉 LLM 问询 + 多模型并发投票内核（`vote_picklist` 多选 / `vote_answer` 单选 / `vote_points` 拖拽两点） |
| `imaging.py` | 截图 / 编号网格叠加 / 本地增强 / 复盘标注 |
| `drivers.py` | 4 种 driver：`grid_select`(DOM多选) / `single_pick`(单选导航) / `canvas_grid`(新版 hCaptcha 画布点击) / `canvas_drag`(画布拖拽) |
| `presets/` | 预置 spec：`recaptcha_v2` / `arkose_match` / `hcaptcha`(画布点击) / `hcaptcha_drag`(画布拖拽) |
| `tests/` | hCaptcha 过码端到端 / 鲁棒性跑分脚本 |

> 由 `common/agent_captcha.py` 的内核泛化而来，独立成库、独立演进。详见 `vision_solver/README.md`。

**协作约定**

- 密钥/凭据**只走环境变量**（见 `.env.example`），严禁明文进库。
- 运行期数据（`cookies/`、`tokens/`、`recordings/`、`*.log`、截图等）均已 `.gitignore`。
- 新增可复用逻辑放 `common/`，对应的命令行入口放根目录，复用 `config.py` 读配置。

---

## 7. 目录约定

| 路径 | 内容 |
|---|---|
| `emails.txt` | 邮箱池（`email----password----refresh_token----client_id`），运行时生成 |
| `cookies/` | 注册成功导出的 cookie（`full_*.json` / `sk_*.txt`） |
| `_outlook_pool/` | outlook_reg_loop 产出的待用号（JSON 内含 `refresh_token` / `client_id`） |
| `tri_register_logs/` | 三平台注册日志 |
| `screenshots*/` | 调试截图 |

以上运行期数据均被 `.gitignore` 忽略，发布包内为空。

---

## 8. 常见问题

- **claude 报 app-unavailable-in-region**：claude.com 对本机 IP 区域封锁，需开 Clash 走干净
  节点（`run_full_flow` / `register.py` 的 `--node auto`）。
- **claude 出现“Performing security verification”或图形验证**：脚本会先等待 Cloudflare
  自动通过，再在提交邮箱前轮换节点；magic-link 的 hCaptcha 会优先使用本地视觉求解，
  YesCaptcha 作为备用，并在求解前排除地区限制或 Cloudflare 节点。需要人工接管时在 `.env` 设置
  `CLAUDE_CAPTCHA_MANUAL_TIMEOUT=180`，在 BitBrowser 完成后流程会自动继续。
- **grok 全页 Cloudflare 拦截**：必须切 Clash 节点；`register_grok_http.py` 会用 curl_cffi 指纹
  逐个试节点找能过的。台湾/香港/新加坡节点常被 403，日本/美国较易通过。
- **三窗口登录同一 outlook 报并发登录**：用 `mailbox_broker.py` 共享取码（每号只登一次）。
- **缺 secret 连不上 Clash 控制面**：确认 External Controller 已开 API 且 `CLASH_SECRET` 正确。
- **更新后页面还是旧功能**：不要在服务运行时只执行 `git pull`；使用 `update.bat` / `update.sh`，它会更新依赖、重启 WebUI 并校验加载的提交版本。

---

## 9. 交流 / 支持

- 💬 **QQ 交流群：`1048143135`**（使用问题、避坑、更新通知）
- [Telegram：**@TIANTIANAIPRO**](https://t.me/TIANTIANAIPRO)

<div align="center">

<a href="https://t.me/TIANTIANAIPRO">
  <img src="assets/telegram_qr.jpg" alt="Telegram @TIANTIANAIPRO 二维码" width="320" />
</a>

</div>

---

## 🔗 Friend Links

- 🐧 [**LinuxDO**](https://linux.do) — A community for tech enthusiasts

---

## ☕ 打赏

<div align="center">

<img src="assets/reward_qr.jpg" alt="打赏码" width="280" />

**谢谢老板打赏，您的打赏是我更新的动力！！！**

</div>


