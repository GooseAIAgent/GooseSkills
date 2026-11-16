# GooseSkills

一组可直接安装的 AI Agent Skills（遵循 `SKILL.md` 开放标准，适用于 WorkBuddy、Claude Code、Codex、OpenCode 等支持该标准的宿主）。

每个技能都是一个独立顶层目录，**按需安装，互不依赖**。当前提供两组：

- **音乐工具**（1 个技能 `music-suite`，四大模块合一）
- **Obsidian 仓库管理**（1 个技能，独立使用）

## 技能一览

### 音乐工具

| 技能 | 定位 | 核心能力 |
|---|---|---|
| [music-suite](music-suite/SKILL.md) | **一站式音乐库管理**（四大模块合一） | ① 歌单导出：网易云/QQ 歌单抓取（无需登录）→ 本地库三层比对 → CSV + 双版本 m3u8<br>② 下载补全：三级音源 + B 站/多音源兜底、FLAC 优先、版本校验、台账幂等、歌手全集、元数据补全<br>③ 音乐库整理：统一命名、外语歌名「原名（译名）」、修标签、按年份-专辑归档、补歌词封面、查重<br>④ 导入飞牛 NAS：云歌单/m3u8 → 飞牛音乐，三级匹配 + 幂等写入 + 服务端复核 |

> `music-suite` 把原先四个独立音乐技能合并为一个自包含技能：安装后只显示**一个**技能，却拥有全部功能，且模块之间可串成一条龙（抓歌单 → 下载补齐 → 整理归档 → 导入 NAS）。

### Obsidian

| 技能 | 核心能力 |
|---|---|
| [obsidian-vault-manager](obsidian-vault-manager/SKILL.md) | 管理 Obsidian 本地仓库：扫描结构、生成与修复双链（`[]()` 语法）、反链索引、批量摘要与建议标签、生成 MOC 地图、发现孤立笔记与断链、支持定时整理 |

## 安装

克隆仓库后，把需要的**技能目录**整体复制到宿主的 skills 目录即可（WorkBuddy 为 `~/.workbuddy/skills/`，Claude Code 为 `~/.claude/skills/`）。

```bash
git clone https://github.com/GooseAgentAI/GooseSkills.git
cd GooseSkills
```

**安装音乐工具（推荐，只需一个目录）**

```bash
mkdir -p ~/.claude/skills
cp -r music-suite ~/.claude/skills/
```

安装后在技能列表里只会看到一个 **music-suite**，直接用自然语言提需求即可，技能会自动路由到对应模块。

**安装 Obsidian**

```bash
cp -r obsidian-vault-manager ~/.claude/skills/
```

## 目录结构

```
GooseSkills/
├── music-suite/                  # 一站式音乐库管理（唯一入口 SKILL.md + scripts/ + references/）
│   ├── SKILL.md                  # 路由决策 + 命令速查 + 安全红线
│   ├── scripts/                  # 25 个脚本（歌单/下载/整理/飞牛 四模块平铺，共享台账一份）
│   └── references/               # 详细文档，按模块分组：exporter/completer/organizer/fnos
├── obsidian-vault-manager/       # Obsidian：双链织网、摘要标签、MOC、断链检查
├── README.md
└── LICENSE
```

## 典型组合流程（music-suite）

**A. 歌单下载一条龙**
```
exporter（抓歌单 + 本地比对 + CSV/m3u8）
  → completer（missing → 五步下载补全 → 补歌词封面）
  → 回填 m3u8 → organizer（可选：统一命名 / 归档）
```

**B. 歌手全集一条龙**
```
completer（artist_songs.py 抓全集清单，自动过滤伴奏与可替代的 Live 版）
  → 五步补全 → B 站/多音源兜底 → 补元数据 → organizer 按「年份-专辑」归档
```

**C. 上 NAS 听歌**
```
歌单已在本地 → fnos 直接导入（--netease / --qq / --m3u8）
本地库不全   → exporter → completer 补齐 → organizer 整理 → fnos 导入（本地可播 m3u8 命中率最高）
```

## 凭证配置（安全）

所有凭证一律走环境变量或 `~/.config` 下的配置文件，**绝不写入技能目录、绝不提交到仓库**：

- 网易云 / QQ 音乐 cookie：见 `music-suite/config.example.json`（匿名也能用，登录后音质与命中率更高）
- 飞牛 NAS 账号：`FNOS_SERVER` / `FNOS_USER` / `FNOS_PASS` 环境变量

## 行为约定

- 批量修改类操作（改名 / 移动 / 写标签 / 删除）一律先试运行预览（`--dry-run` / `--check` / `--scan-only`），确认后执行。
- 下载类任务版本校验优先于覆盖率：不下翻唱、现场、消音、伴奏等错误版本；宁可缺，不可错。
- 数据一律从平台 API 或互联网核实，不杜撰专辑名、发行年、歌词与封面。

## 依赖

Python 3.8+（脚本基本只用标准库）；标签/歌词/封面写入需 `mutagen`；飞牛模块需系统 `curl`；部分兜底与查重脚本可选依赖 `ffmpeg`。

## License

MIT，见 [LICENSE](LICENSE)。
