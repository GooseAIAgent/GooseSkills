---
name: music-playlist-completer
description: 歌单缺失歌曲补全技能。当用户要求把歌单（网易云收藏歌单 CSV、m3u8）中没有的本地缺失歌曲从网易云音乐或QQ音乐下载补齐时使用：按歌单 ID 严格下载、FLAC 无损优先、版本严格校验（歌名+歌手+时长三重匹配，禁止错下翻唱/现场版）、CDN 节点轮换、批量并发下载、生成缺失清单与下载记录。涉及批量下载音乐、补齐本地曲库、按音质阶梯下载 FLAC/320k/128k、VIP cookie 登录态配置等场景均适用。
license: MIT
---

# Music Playlist Completer（歌单缺失歌曲补全）

## Overview

本技能固化了一套在真实场景中验证过的补全流水线：拿一份平台歌单（网易云收藏歌单 CSV 或 m3u8），与本地音乐库比对找出缺失曲目，再按「网易云按 ID 直下 → 网易云替代 ID 搜索 → QQ 音乐搜索兜底」三级音源策略下载，全程强制版本校验，最终把歌单覆盖率从个位数提升到 97%+。

所有脚本只用 Python 标准库（mutagen 可选，用于写标签），支持 Claude Code、Codex、WorkBuddy、OpenClaw 等任何能执行 shell/Python 的 Agent。

**开始任何任务前：先读 `references/workflow.md` 的完整流程；遇到报错或异常结果时查 `references/pitfalls.md`（真实踩坑记录，命中率最高的排查手册）。**

## 环境要求

- Python 3.8+（脚本全部只用标准库）。
- **mutagen**（可选）：`python3 -c "import mutagen"` 缺失则 `pip3 install --user mutagen`。不装也能下载，但不会写入标题/歌手/专辑标签、无法回读文件时长做版本核对。
- 登录态（可选但强烈建议）：见下方「凭证配置」。

## 凭证配置（安全红线）

配置文件 **不放在 skill 目录内**，默认路径 `~/.config/music-downloader/config.json`（可用 `--config` 或环境变量 `MUSIC_DL_CONFIG` 覆盖），模板见 `config.example.json`：

```json
{
  "netease_cookie": "MUSIC_U=xxxx",
  "qq_uin": "1007726770",
  "qq_keyst": "Q_H_L_xxxx",
  "qq_cookie": ""
}
```

硬性规则：

1. **凭证文件绝不提交 git、绝不复制进 skill 目录、绝不写入输出目录**。`.gitignore` 已排除 `config*.json`（`config.example.json` 除外）。
2. 输出目录默认 `./music-downloads`（可用 `--out` 或环境变量 `MUSIC_DL_OUT` 覆盖），输出根目录下会生成 `下载记录.jsonl`、`版本待核对.jsonl`、`QQ候选对照.csv` 等清单文件。
3. 若用户提供的 cookie 片段缺少 `MUSIC_U`（网易云）或 `uin`+`qm_keyst`（QQ），接口会返回未登录/无 VIP——见 `references/pitfalls.md` 中「凭证获取」一节的引导话术。

## 工作流（五步）

### 第 1 步：生成缺失清单

```bash
python3 scripts/build_missing_list.py --playlist 歌单.csv --lib /path/to/music1 --lib /path/to/music2 --out ./music-downloads
```

- 支持 CSV（含「歌名」「歌手」列，网易云导出格式直接可用）与 m3u8。
- 本地匹配键为标准化后的「歌手 - 歌名」，递归扫描所有 `--lib` 目录。
- 产出 `missing.json`，每项 `{"t","a","alb","dur","sid"}`；`sid` 是网易云歌曲 ID，来自歌单里的链接，**第 2 步必需**。

### 第 2 步：网易云按 ID 直下（主力，约占 90%）

```bash
python3 scripts/netease_dl.py --list ./music-downloads/missing.json --workers 4 --out ./music-downloads
```

- 只用 `sid` 请求 `/api/song/enhance/player/url/v1`，**绝不用标题搜索**，从机制上杜绝下错版本。
- 音质阶梯 lossless→exhigh→higher→standard，文件头嗅探真实格式（防 HTML 错误页冒充音频）。
- CDN 节点自动轮换（部分节点 403，见 pitfalls）。
- 文件名 `歌手 - 歌名.flac/mp3`；时长偏差 >3s 记入 `版本待核对.jsonl`。

### 第 3 步：网易云替代 ID 搜索（对「无音源」曲目）

```bash
python3 scripts/netease_reid.py --list ./music-downloads/missing.json --out ./music-downloads
```

原 ID 下架/转云盘时，同一录音常以另一个 ID（合集、再发行）存在。四重认定（歌名相等+歌手交集+专辑相等或时长差≤1.5s+排除翻唱/现场词），命中的记录带 `reid: true` 便于回溯。**用户强调版本不能错时，宁缺勿滥。**

### 第 4 步：QQ 音乐兜底

```bash
python3 scripts/qq_verify.py   # 先验证登录态与无损权益
python3 scripts/qq_dl.py --list <剩余缺失.json> --workers 3 --out ./music-downloads
python3 scripts/qq_dl.py --list <仍无音源.json> --relax --out ./music-downloads   # 宽松档，结果需人工复核
```

- 三重校验：歌名标准化相等 + 歌手有交集（或歌名括号内点名的歌手匹配）+ 时长差 ≤5s。
- `--relax` 只认同歌手版本且 match 标记为「同歌手」，**必须列出让用户试听确认，不得当作精确匹配报告**。
- 他人翻唱/现场版即使能下载也**不下载**，改为记录进 `QQ候选对照.csv`（用 `qq_probe.py`）供用户决策。

### 第 5 步：核对与交付

- 汇总 `下载记录.jsonl`（逐曲音质/码率/时长偏差）与各缺失清单，给出覆盖率统计。
- 生成/更新 m3u8（路径必须逐条 `os.path.exists` 校验后才写入）。
- 列出所有「待核」「同歌手」条目请用户试听确认。

## 设计原则（改脚本时不得破坏）

1. **版本正确性 > 覆盖率**：拿不准的版本不下载，记录候选交给用户。
2. **无音源 ≠ 失败**：下架/版权受限是常态，如实分类报告（原版受限/仅翻唱可下/需付费），不要硬凑。
3. **幂等**：已存在文件直接跳过（查 `.flac/.mp3/.m4a/.ogg`），可安全重跑；临时文件用 `.part`，失败即清理。
4. **下载后必须核验**：文件头嗅探 + 时长比对，异常进「待核」而不是当成功报。
5. **并发与限流**：workers 3–6 足够；网易云搜索接口限流严重（退避重试已内置），不要盲目调大并发。

## Resources

- `scripts/mcommon.py` — 共享模块：配置加载、文件名清洗、文件头嗅探、标签写入。**所有脚本共用，改这里影响全局。**
- `scripts/build_missing_list.py` — 歌单×本地库比对，产出 missing.json。
- `scripts/netease_dl.py` / `netease_reid.py` — 网易云直下 / 替代 ID。
- `scripts/qq_dl.py` / `qq_verify.py` / `qq_probe.py` — QQ 兜底 / 权益验证 / 候选探查。
- `references/workflow.md` — 完整流水线详解（含输入格式细节、统计口径）。
- `references/pitfalls.md` — 真实踩坑手册：CDN 403、搜索限流、QQ 权益分级、HttpOnly cookie 获取、vkey uin 陷阱等。
- `config.example.json` — 凭证配置模板（复制到 `~/.config/music-downloader/config.json` 后填写）。
