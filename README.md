# HeinOnlineSearcher 部署指引

独立的 HeinOnline 检索 skill：单框检索、多字段高级检索、分节 PDF 下载。机构访问不内置在本仓库，而是复用同组织的公开 skill [gdufs-auth-session](https://github.com/HuiwowICCSkills/gdufs-auth-session)；本 skill 不保存凭据，也不写会话文件。

## 1. 运行环境

| 组件 | 要求 | 说明 |
| --- | --- | --- |
| Python | 3.10+ | 唯一第三方依赖是 `beautifulsoup4` |
| PowerShell | Windows PowerShell 5.1 或 PowerShell 7+（`pwsh`） | 调用 gdufs-auth-session 的 `run.ps1`；macOS/Linux 也需安装 `pwsh` |
| Node.js | 22+ | 无头浏览器辅助脚本使用全局 `WebSocket` API |
| 浏览器 | Chrome / Chromium / Edge | 可用 `CHROME_PATH` 指定；未设置时辅助脚本自动探测常见安装路径 |
| 广外账号 | 已开通图书馆 VPN 权限的统一身份认证账号 | 只写入本机配置文件或环境变量，不进仓库 |
| GitHub 权限 | HuiwowICCSkills 组织成员，或对私仓有读权限的部署密钥/令牌 | 用于获取 `gdufs-auth-session` |

## 2. 安装

### 2.1 让两个 skill 互为同级目录

`HeinOnlineSearcher` 按「命令行参数 → `GDUFS_AUTH_SKILL` → 同级目录」的顺序查找 `gdufs-auth-session`。推荐直接放进同一个 skills 目录：

```text
<skills-root>/
├── HeinOnlineSearcher/
│   ├── SKILL.md
│   ├── README.md
│   ├── run.ps1
│   ├── run.sh
│   ├── requirements.txt
│   ├── agents/
│   ├── references/
│   └── scripts/
└── gdufs-auth-session/     # 公开仓库，同样必需
    ├── SKILL.md
    ├── run.ps1
    ├── requirements.txt
    ├── references/
    └── scripts/
```

Codex 用户 `<skills-root>` 通常是 `%USERPROFILE%\.codex\skills`（macOS/Linux 为 `~/.codex/skills`）。

```powershell
# HeinOnlineSearcher 仓库（公开仓库）
git clone https://github.com/HuiwowICCSkills/HeinOnlineSearcher.git "$env:USERPROFILE\.codex\skills\HeinOnlineSearcher"

# gdufs-auth-session 是公开仓库：先确认当前账号已有访问权限
git clone https://github.com/HuiwowICCSkills/gdufs-auth-session.git "$env:USERPROFILE\.codex\skills\gdufs-auth-session"
```

公开仓库认不出凭据时，改用 SSH（`git@github.com:HuiwowICCSkills/gdufs-auth-session.git`）或在 Git Credential Manager 中登录有组织权限的账号。不想放同级目录时，设置环境变量即可：

```powershell
$env:GDUFS_AUTH_SKILL = 'D:\skills\gdufs-auth-session'
```

### 2.2 安装 Python 依赖

```powershell
python -m pip install -r "$env:USERPROFILE\.codex\skills\HeinOnlineSearcher\requirements.txt"
```

`run.ps1` / `run.sh` 会依次探测 `venv/`、随附运行时和 PATH 上的 Python，并挑选第一个能 `import bs4` 的解释器；没有可用解释器时会直接报错并提示安装依赖。

### 2.3 配置广外账号（只做一次）

凭据放在 gdufs-auth-session 的配置里，默认路径 `%USERPROFILE%\.gdufs-auth\config.json`（macOS/Linux：`~/.config/gdufs-auth/config.json`）：

```json
{
  "username": "<广外账号>",
  "password": "<密码>",
  "verify_ssl": true,
  "timeout_seconds": 30
}
```

也可以用环境变量 `GDUFS_USERNAME` / `GDUFS_PASSWORD`，或用 `--auth-config` 指向别处的配置文件。把配置文件放在仓库之外并限制本机权限；密码和会话 Cookie 都不要提交到 Git。

### 2.4 建立会话

```powershell
& "$env:USERPROFILE\.codex\skills\gdufs-auth-session\run.ps1" ensure --config "$env:USERPROFILE\.gdufs-auth\config.json"
```

`ensure` 会话有效时返回 `status=reused`，失效时才重新登录并保存。后续检索里 HeinOnlineSearcher 会自动调用同一个 `ensure`，不需要重复登录，也不会与其它检索工具互相踢下线。

### 2.5 自检

```powershell
& "$env:USERPROFILE\.codex\skills\HeinOnlineSearcher\run.ps1" doctor
```

`doctor` 逐项检查 Python/bs4、PowerShell、Node.js（要求 22+）、Chrome/Chromium/Edge、`gdufs-auth-session` 定位和会话状态，并给出 `next_steps`。整体 `ok: true` 就表示可以开始检索。

## 3. 使用

```powershell
$hein = "$env:USERPROFILE\.codex\skills\HeinOnlineSearcher\run.ps1"

# 单框检索：HeinOnline 原生 Search Help 语法
& $hein search 'command responsibility NOT Bemba' --limit 5 --out './hein-search.json'

# 从第 11 条开始取 20 条
& $hein search 'command responsibility NOT Bemba' --offset 10 --limit 20 --out './hein-search-11-30.json'

# 多字段高级检索：Section Title / Book Title / Author / Full Text + 年份范围
& $hein advanced --field 'title=command responsibility' --field 'text=Article 28' --join AND --year-lo 2005 --year-hi 2015 --limit 5 --out './hein-advanced.json'

# 下载某条结果的「Download PDF of This Section」分节 PDF
& $hein download '<HeinOnline 结果 URL>' --pdf-out './section.pdf' --out './section.json'
```

不加 `--out` 时报告直接打印到标准输出。`--limit` 取 1..50，`--offset` 是零基结果位置（`10` 表示从第 11 条开始），`--timeout` 取 1..1800 秒，`download --wait-ms` 取 0..120000。检索命令会关闭 HeinOnline 无限滚动，用 `firstdoc` 和 `maxresults` 请求稳定的结果窗口。

检索结果的 `query_echo` 字段显示 HeinOnline 实际解释的检索式，是核对布尔运算符、通配符和邻近检索是否生效的直接依据。语法表与实测数字见 [references/heinonline-search.md](references/heinonline-search.md)。

## 4. 排错

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `Cannot locate gdufs-auth-session` | 两个 skill 不在同级目录，也没设 `GDUFS_AUTH_SKILL` | 按 2.1 调整目录，或传 `--auth-skill` |
| `No Python with beautifulsoup4 was found` | 解释器缺依赖 | `python -m pip install -r requirements.txt` |
| `Node.js is required...` / Node 版本过低 | 未安装 Node 或版本 < 22 | 安装 Node.js 22+ |
| `gdufs-auth-session status failed` | 会话过期或凭据错误 | 先跑 `ensure`；仍失败则检查 config.json |
| 渲染结果是 Service Worker 安装页 | LibVPN 首次请求未接管 | 本适配器已改写为 `/-https://<host><path>` 入口并预热；重跑仍失败时确认 Chrome 可用、会话未过期 |
| `status=unexpected-page` | 检索式被拒绝或结果页超时 | 看报告里的 `requested_url` 与 `query_echo`，简化检索式后重试 |
| `status=disabled` | 未启用浏览器来源 | 本 skill 的命令行调用默认启用；出现该状态说明调用方传了 `browser_enabled=False` |
| PDF 下载 `status != ok` | 该文献对本账号是 metadata-only，或链接过期 | 报告限制，不要把它当成数据库整体不可用 |

## 5. 安全与合规边界

- 仅用于用户已获授权的广外账号和图书馆资源；不绕过验证码、订阅或数据库自身的访问控制。
- 凭据、`session.json`、`cookies.json`、`browser_sources.json` 只留在本机，不进入仓库、检索报告或引用。
- 检索结果是数据库元数据与摘要，引用时以 `download` 落盘的 PDF 或 HeinOnline 页面为准，不把摘要当原文。
- 下载 PDF 首页是 HeinOnline 生成的引证封面，同一文献每次下载的 SHA-256 不同；核对页数、正文与引证头。

## 6. 更新

```powershell
git -C "$env:USERPROFILE\.codex\skills\HeinOnlineSearcher" pull
git -C "$env:USERPROFILE\.codex\skills\gdufs-auth-session" pull
```

两个仓库独立发版：HeinOnlineSearcher 只依赖 gdufs-auth-session 的公开命令面（`ensure`、`cookies`、`status`、`run.ps1`、`scripts/headless_fetch.mjs`、`scripts/headless_download.mjs`）。升级其中一个前，先确认另一个的对应命令仍存在。

## 实测效果（2026-11-09）

以下命令与结果来自本机 `~/.codex/skills/HeinOnlineSearcher` 的实网运行；检索日期 2026-11-09（Asia/Shanghai），输出为该次运行实际返回的 JSON 报告。机构会话由 `gdufs-auth-session` 提供，未登录或会话过期时检索不可用。

```powershell
& "$env:USERPROFILE\.codex\skills\gdufs-auth-session\run.ps1" ensure --config "$env:USERPROFILE\.gdufs-auth\config.json"
$hein = "$env:USERPROFILE\.codex\skills\HeinOnlineSearcher\run.ps1"
& $hein search 'command responsibility' --limit 5
```

- 状态：`ok`；服务端报告 `total = 337430`（All Databases 全库命中）；本次返回 5 条（`pagination.page_result_count = 9`）。
- `query_echo = "(command responsibility)"`：HeinOnline 实际执行的检索式，可与输入对照确认布尔词、通配符是否生效。
- `scope` 原文：`HeinOnline All Databases search through the GDUFS library session; paginated results with title, journal, author, citation, subjects and summary.`
- 代表性结果：

| 题名 | 类型 | 期刊 / 引证 | 作者 | 链接 |
| --- | --- | --- | --- | --- |
| Command Responsibility [article] | search_result | United States Air Force Academy Journal of Legal Studies , Vol. 8, pp. 1-18；8 U.S. A.F. Acad. J. Legal Stud. 1 (1997-1998) | Levie, Howard S. (Cited 249 times) | https://libvpn.gdufs.edu.cn/HOL/Page?public=true&handle=hein.journals/usafa8&div=5&start_page=1&collection=journals&set_as_cursor=0&men_tab=srchresults&__proto__=https&__host__=access.heinonline.com |
| Command Responsibility [reviews] | search_result | LAW - Theory and Practice , Vol. 38, Issue 1 (2021), pp. 70-96；DOI: 10.5937/ptp2101070D | Dinic, Slavica S. ; Turkovic, Emil M. | https://libvpn.gdufs.edu.cn/HOL/Page?public=true&handle=hein.journals/ltyape38&div=10&start_page=70&collection=journals&set_as_cursor=1&men_tab=srchresults&__proto__=https&__host__=access.heinonline.com |
| Command Responsibility [article] | search_result | Croatian Annual of Criminal Law and Practice , Vol. 8, Issue 2 (2001), pp. 139-146；8 Croatian Ann. Crim. L. & Prac. 139 (2001) | Bacic, Franjo | https://libvpn.gdufs.edu.cn/HOL/Page?public=true&handle=hein.journals/cranmscip8&div=50&start_page=139&collection=journals&set_as_cursor=2&men_tab=srchresults&__proto__=https&__host__=access.heinonline.com |
| Command Responsibility [notes] | search_result | Military Law and Law of War Review , Vol. 41, Issues 3 and 4 (2002), pp. 193-214；41 Mil. L. & L. War Rev. 193 (2002) | Keijzer, Nico (Cited 2 times) | https://libvpn.gdufs.edu.cn/HOL/Page?public=true&handle=hein.journals/mllwr41&div=24&start_page=193&collection=journals&set_as_cursor=3&men_tab=srchresults&__proto__=https&__host__=access.heinonline.com |
| Command Responsibility [notes] | search_result | Military Law and Law of War Review , Vol. 41, Issues 3 and 4 (2002), pp. 215-226；41 Mil. L. & L. War Rev. 215 (2002) | Dahl, Arne Willy (Cited 4 times) | https://libvpn.gdufs.edu.cn/HOL/Page?public=true&handle=hein.journals/mllwr41&div=25&start_page=215&collection=journals&set_as_cursor=4&men_tab=srchresults&__proto__=https&__host__=access.heinonline.com |

> 说明：以上是**元数据与检索摘要**，不是全文。每条 `access` 均为 `HeinOnline institutional search metadata and summary; full text not fetched by this adapter`，本次 `summary` 字段为空，因此不得据题名或引证补写裁判/论证内容。`total=337430` 是 All Databases（跨全部文库）的服务端命中数，不等于可引用的独立文献数；引用前须用 `download` 落盘分节 PDF 或打开 HeinOnline 页面核对正文、页码与引证头。链接走 GDUFS 代理域（`libvpn.gdufs.edu.cn`），需机构会话才可打开。
