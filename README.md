# GooseSkills

一组可直接安装的 AI Agent Skills（遵循 `SKILL.md` 开放标准，适用于 WorkBuddy、Claude Code、Codex、OpenCode 等支持该标准的宿主）。

每个技能都是一个独立顶层目录，**按需安装，互不依赖**。当前提供两组：

- **音乐工具组**（5 个技能，可只装常用的一两个，也可全套安装）
- **Obsidian 仓库管理**（1 个技能，独立使用）

## 技能一览

### 音乐工具组

| 技能 | 定位 | 核心能力 |
|---|---|---|
| [music-suite](music-suite/SKILL.md) | **总台路由**（无脚本） | 识别音乐任务意图并分发到下面四个技能；编排「歌单下载 → 整理 → 上 NAS」组合流程 |
| [music-playlist-exporter](music-playlist-exporter/SKILL.md) | 歌单获取与导出 | 抓取网易云 / QQ 歌单全量曲目（均无需登录）→ 与本地库三层比对 → 导出 CSV + 双版本 m3u8 |
| [music-playlist-completer](music-playlist-completer/SKILL.md) | 歌曲下载补全 | 缺失清单 → 三级音源下载（网易云直下 → 替代 ID → QQ 兜底），仍有缺口可走 B 站 / 酷狗酷我咪咕 兜底；FLAC 优先、版本严格校验、台账幂等；含**歌手全集下载**与元数据补全 |
| [music-library-organizer](music-library-organizer/SKILL.md) | 音乐库整理 | 统一「歌手 - 歌名」命名、外语歌名「原名（中文译名）」、修复内嵌标签、按「年份-专辑」归档、批量补歌词与封面、查重 |
| [fnos-playlist-creator](fnos-playlist-creator/SKILL.md) | 歌单导入飞牛 NAS | 网易云 / QQ / 本地 m3u8 → 飞牛音乐（fnOS）：匿名抓取 → 曲库三级匹配 → 幂等写入 → 服务端复核 |

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

**只装 Obsidian**

```bash
cp -r obsidian-vault-manager ~/.claude/skills/
```

**只装音乐（全套 5 个，推荐——带总台自动路由）**

```bash
mkdir -p ~/.claude/skills
cp -r music-suite music-playlist-exporter music-playlist-completer \
      music-library-organizer fnos-playlist-creator ~/.claude/skills/
```

**只装音乐里的某一个**（例如只下载补全）

```bash
cp -r music-playlist-completer ~/.claude/skills/
```

> 说明：不装 `music-suite` 总台时，各子技能仍可独立使用，只是需要你自己明确说出要用哪一个；装了总台则由它按意图自动分发。

## 目录结构

```
GooseSkills/
├── music-suite/                  # 总台：意图路由与组合流程编排（无脚本）
├── music-playlist-exporter/      # 歌单获取：网易云/QQ 歌单抓取 → 本地库比对 → CSV + m3u8
├── music-playlist-completer/     # 下载补全：三级音源 + B 站/多音源兜底 + 歌手全集 + 元数据
├── music-library-organizer/      # 库整理：命名/标签/归档/歌词封面/查重
├── fnos-playlist-creator/        # 上 NAS：云歌单或 m3u8 → 飞牛音乐
├── obsidian-vault-manager/       # Obsidian：双链织网、摘要标签、MOC、断链检查
├── README.md
└── LICENSE
```

## 典型组合流程（音乐组）

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

- 网易云 / QQ 音乐 cookie：见各技能的 `config.example.json`（匿名也能用，登录后音质与命中率更高）
- 飞牛 NAS 账号：`FNOS_SERVER` / `FNOS_USER` / `FNOS_PASS` 环境变量

## 行为约定

- 批量修改类操作（改名 / 移动 / 写标签 / 删除）一律先试运行预览（`--dry-run` / `--check` / `--scan-only`），确认后执行。
- 下载类任务版本校验优先于覆盖率：不下翻唱、现场、消音、伴奏等错误版本；宁可缺，不可错。
- 数据一律从平台 API 或互联网核实，不杜撰专辑名、发行年、歌词与封面。

## 依赖

Python 3.8+（脚本基本只用标准库）；标签/歌词/封面写入需 `mutagen`。部分脚本需访问网易云、QQ 音乐、B 站等平台 API。

## License

MIT，见 [LICENSE](LICENSE)。
