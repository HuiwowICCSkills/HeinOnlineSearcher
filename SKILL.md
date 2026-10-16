---
name: HeinOnlineSearcher
description: 独立检索 HeinOnline（广外图书馆机构访问），支持单框检索、多字段高级检索和分节 PDF 下载；机构会话由 gdufs-auth-session 提供。用于需要 HeinOnline 期刊、文集、条约与判例文献元数据、摘要或分节 PDF 的国际法研究。
---

# HeinOnlineSearcher

> **`<skill-dir>`** = 本 SKILL.md 所在目录。核心脚本为 `<skill-dir>/scripts/heinonline_search.py`；Windows 用 `<skill-dir>/run.ps1`，macOS/Linux 用 `<skill-dir>/run.sh`。部署与首次登录见 [README.md](README.md)。

这个 skill 只做 HeinOnline 一件事：

- `search`：单框检索（HeinOnline 原生 Search Help 语法），按 `--offset` / `--limit` 返回指定结果窗口的题名、作者、期刊、引证、主题和摘要；
- `advanced`：多字段高级检索（Section Title / Book Title / Author / Full Text，行间 `AND`/`OR`/`NOT`，可加年份范围）；
- `download`：按文献页或 `PrintRequest` 链接下载 "Download PDF of This Section" 分节 PDF（带 OCR 文字层）；
- `doctor`：部署自检（Python/bs4、PowerShell、Node.js、Chrome、会话状态）。

HeinOnline 通过广外图书馆 LibVPN 访问，登录与会话由独立的 [gdufs-auth-session](../gdufs-auth-session/SKILL.md) 负责。本 skill 不保存用户名密码，也不写会话文件。

## 依赖解析顺序

1. 命令行 `--auth-skill <path>`；
2. 环境变量 `GDUFS_AUTH_SKILL`；
3. 同级 skill 目录 `../gdufs-auth-session`。

## 命令

```powershell
# 单框检索（检索式原样传给 HeinOnline；布尔运算符须大写）
& '<skill-dir>/run.ps1' search 'command responsibility NOT Bemba' --limit 5 --out './hein-search.json'

# 取第 11-30 条结果
& '<skill-dir>/run.ps1' search 'command responsibility NOT Bemba' --offset 10 --limit 20 --out './hein-search-11-30.json'

# 多字段高级检索
& '<skill-dir>/run.ps1' advanced --field 'title=command responsibility' --field 'text=Article 28' --join AND --limit 5 --out './hein-advanced.json'

# 下载分节 PDF
& '<skill-dir>/run.ps1' download '<HeinOnline 结果 URL>' --pdf-out './section.pdf' --out './section.json'

# 部署自检
& '<skill-dir>/run.ps1' doctor
```

通用参数：`--auth-skill`、`--auth-session`、`--auth-config`、`--chrome-path`、`--out`。检索命令另有 `--limit`（1..50）、`--offset`（零基起始位置，`10` 表示从第 11 条开始）、`--timeout`；`download` 另有 `--pdf-out`（必填）、`--wait-ms`。

## 结果边界

- 检索结果是机构数据库的元数据和摘要，不等于取得全文；全文以 `download` 落盘的 PDF 为准。
- 检索命令使用 HeinOnline 的普通分页模式（`infinite_scroll=off`），以 `firstdoc` 对应 `--offset`、`maxresults` 为实际页大小；`pagination.next_offset` 给出下一批结果的起始位置。
- 下载 PDF 首页是 HeinOnline 生成的引证封面，同一文献每次下载的 SHA-256 不同，不要把哈希当文献标识。
- 只有 `status=ok` 才是成功；`unexpected-content` 等状态会把响应写到 `<out>.html` 供排查，`status=disabled` 表示浏览器来源未启用。
- 不绕过验证码、订阅或数据库自身的访问控制；目标站明确拒绝自动化访问时报告限制。

## 参考

- 检索语法、2026-10-16 实测数字与未验证项：[references/heinonline-search.md](references/heinonline-search.md)
- 部署、账号配置、目录结构与排错：[README.md](README.md)
