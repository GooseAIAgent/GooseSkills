# music-playlist-completer

**Agent Skill** · 把平台歌单中本地缺失的歌曲批量补全下载：网易云按 ID 直下 → 替代 ID 搜索 → QQ 音乐兜底，全程严格版本校验（FLAC 无损优先）。

[English](#english) | [中文](#中文)

---

## 中文

### 这是什么

一份经过真实场景验证的补全流水线：在 2544 首网易云收藏歌单上实测，本地覆盖率从 **3.5% 提升到 97.1%**（2400+ 首，FLAC 占比约 91%，总下载 67GB+）。

三级音源策略：

| 级别 | 脚本 | 策略 | 版本保证机制 |
|---|---|---|---|
| 1 | `netease_dl.py` | 按歌单里的歌曲 ID 直下，FLAC→320k→192k→128k | 只用 ID，不用标题搜索，机制上杜绝错版本 |
| 2 | `netease_reid.py` | 原 ID 下架时搜索同录音的替代 ID | 歌名+歌手+专辑/时长≤1.5s 四重认定 |
| 3 | `qq_dl.py` | QQ 搜索兜底 | 歌名+歌手+时长≤5s 三重校验，逐 mid 试音源 |

拿不准的版本**一律不下载**，改为输出候选清单（`QQ候选对照.csv`）交由人工决策。

### 安装

各 Agent 的 skill 目录：

| Agent | 位置 |
|---|---|
| Claude Code | `~/.claude/skills/music-playlist-completer/` |
| WorkBuddy | `~/.workbuddy/skills/music-playlist-completer/` |
| Codex / OpenClaw 等 | 支持读取 `SKILL.md`（Agent Skills 标准）的目录即可 |

```bash
git clone https://github.com/<you>/music-playlist-completer.git
mkdir -p ~/.workbuddy/skills
cp -r music-playlist-completer ~/.workbuddy/skills/
```

依赖：Python 3.8+（纯标准库）；可选 `pip3 install mutagen`（写标签 + 时长核对）。

### 配置凭证

```bash
mkdir -p ~/.config/music-downloader
cp config.example.json ~/.config/music-downloader/config.json
# 编辑填入你的 cookie（见下方说明）
```

- **网易云**：需要 `MUSIC_U`（登录后的 cookie），VIP 才能取 FLAC/320k；匿名只能 128k。
- **QQ 音乐**：需要 `qq_uin` + `qq_keyst`。注意两者都是 HttpOnly cookie，浏览器控制台 `document.cookie` 读不到——需从 DevTools → Network → 请求头 `Cookie:` 整行复制（SKILL.md 与 pitfalls.md 有详细引导）。

> ⚠️ 配置文件含登录凭证，**不要提交到 git、不要分享**。本仓库 `.gitignore` 已排除。

### 使用

Agent 对话里直接说「帮我补齐这份歌单缺失的歌曲」，或手动分步：

```bash
# 1. 歌单 × 本地库比对 → missing.json
python3 scripts/build_missing_list.py --playlist 我的歌单.csv --lib /path/to/music --out ~/music-downloads

# 2. 网易云按 ID 直下
python3 scripts/netease_dl.py --list ~/music-downloads/missing.json --workers 4 --out ~/music-downloads

# 3. 下架曲目找替代 ID
python3 scripts/netease_reid.py --list ~/music-downloads/missing.json --out ~/music-downloads

# 4. QQ 兜底（先验证权益）
python3 scripts/qq_verify.py
python3 scripts/qq_dl.py --list ~/music-downloads/missing.json --out ~/music-downloads

# 5. 无音源的出候选清单（不下载，供人工决策）
python3 scripts/qq_probe.py ~/music-downloads/missing.json --out ~/music-downloads
```

所有脚本支持 `--config`（凭证路径）、`--out`（输出目录），或环境变量 `MUSIC_DL_CONFIG` / `MUSIC_DL_OUT`。幂等可重跑（已有文件自动跳过）。

### 输出

```
music-downloads/
├── 网易云音乐/          # 歌手 - 歌名.flac/mp3
├── QQ音乐/
├── missing.json          # 缺失清单（可重复用作输入）
├── 下载记录.jsonl        # 逐曲：音质/码率/时长偏差
├── 版本待核对.jsonl      # 时长差 >3s 的条目
├── 替代ID记录.jsonl      # reid 命中的条目
├── QQ未获取.jsonl        # QQ 无版权/无匹配及原因
└── QQ候选对照.csv        # 全量候选（含他人翻唱），供人工决策
```

## English

An [Agent Skills](https://github.com/anthropics/skills)–compatible skill that batch-downloads songs missing from your local library using a platform playlist (NetEase Cloud Music CSV export or m3u8), with a strict three-tier source strategy and **no wrong-version downloads**:

1. **`netease_dl.py`** — direct download by song ID (quality ladder: FLAC → 320k → 128k), CDN-host rotation, magic-byte sniffing.
2. **`netease_reid.py`** — re-search delisted songs for alternate IDs (4-way verification: title + artist + album/duration≤1.5s).
3. **`qq_dl.py`** — QQ Music fallback (triple check: title + artist + duration≤5s; tries every candidate `mid`).

Ambiguous matches are **never downloaded**; they are exported as candidate lists for human review.

Validated on a real 2,544-song playlist: local coverage **3.5% → 97.1%**.

### Quick start

```bash
git clone https://github.com/<you>/music-playlist-completer.git ~/.claude/skills/music-playlist-completer
pip3 install mutagen        # optional, for tags & duration check
mkdir -p ~/.config/music-downloader
cp ~/.claude/skills/music-playlist-completer/config.example.json ~/.config/music-downloader/config.json
# edit config.json: netease_cookie (MUSIC_U=...) and/or qq_uin + qq_keyst
```

Then ask your agent to "complete the missing songs from my playlist", or run the scripts manually (see 中文 section above — all flags accept English usage).

### Notes

- Credentials are **HttpOnly** cookies; see `references/pitfalls.md` for how to obtain them.
- VIP entitlement is per-copyright-holder (QQ Music may return `104003` for paid albums) — the skill reports rather than retries.
- All scripts are Python 3.8+ stdlib-only; `mutagen` optional.
- MIT License. For personal offline listening of songs you already favorited.
