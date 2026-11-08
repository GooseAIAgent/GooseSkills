---
name: music-playlist-completer
description: 歌单歌曲下载补全技能。当用户要求把歌单里缺失的歌曲批量下载到本地、按三级音源策略补全音乐库（网易云按 ID 直下 → 替代 ID 搜索 → QQ 音乐兜底）、验证 QQ 登录态/无损权益、跳过已下载过的曲目时使用。FLAC 无损优先、版本严格校验（禁止错下翻唱/现场版）、下载台账自动跳过已下载曲目、幂等可重跑。歌单抓取由 music-playlist-exporter 技能负责，本技能直接消费其输出的缺失清单。
license: MIT
---

# Music Playlist Completer（missing.json → 三级音源下载补全）

## Overview

把歌单里本地缺失的歌曲批量下载补齐，全部在真实场景验证过（2544 首歌单补全后覆盖率 97.1%，FLAC 约 91%，总下载 67GB+）：

```
歌单 CSV/m3u8 → build_missing_list 生成 missing.json
    → 第2步 网易云按 ID 直下（主力 ~90%）
    → 第3步 网易云替代 ID 搜索（无音源曲目）
    → 第4步 QQ 音乐兜底（含权益验证）
    → 第5步 版本核验 → 统计交付 → 回填 m3u8
```

**开始任务前：先读 `references/workflow.md`（输入格式、比对口径、统计口径、并发预算）；遇到报错或异常结果查 `references/pitfalls.md`（真实踩坑记录，命中率最高的排查手册）。**

所有脚本只用 Python 标准库（mutagen 可选，用于写标签）。

## 快速决策

| 用户需求 | 做法 |
|---|---|
| 下载某歌手的全部歌曲（全集） | **歌手全集流程**：`scripts/artist_songs.py` 抓清单（含长期过滤规则）→ 五步补全 → `scripts/enrich_meta.py` 补元数据；详见 `references/artist_discography.md` |
| 把歌单缺失歌曲下载补齐 | 五步流程（见下），先 `build_missing_list.py` |
| 验证 QQ 登录态/无损权益 | `scripts/qq_verify.py` |
| 某些歌 QQ 有没有可下的版本 | `scripts/qq_probe.py` 宽松探查，输出候选 CSV 人工决策 |
| 网易云/QQ 都拿不到音源（VIP/下架/数字专辑） | **兜底第 6 步**：先 `scripts/bilibili_dl.py`（B 站 DASH，主力），仍无再用 `scripts/alt_source_dl.py`（酷狗→酷我→咪咕）；见下「兜底音源」 |
| 之前下过的歌不想重复下载 | 台账自动处理（见下），无需手工操作 |
| 查看累计下载台账 | `python3 scripts/library_ledger.py status` |

## 兜底音源（第 6 步，可选）

主流程三档（网易云按 ID → 替代 ID → QQ）都失败后，按此顺序兜底，两脚本均为幂等可重跑：

```bash
S=<本技能目录>/scripts
TODO=missing.json            # [{"t":"歌名","a":"歌手","dur":253}, ...]
OUT=/music/某歌手

# 1) B 站优先：搜索 → 排除翻唱/Live/教学 → 时长校验 → DASH 音频 → ffmpeg 封装 → mutagen 复核
python3 $S/bilibili_dl.py  --artist 某歌手 --todo $TODO --out $OUT --dry-run   # 先看能匹配到哪首
python3 $S/bilibili_dl.py  --artist 某歌手 --todo $TODO --out $OUT

# 2) 三家音源兜底（曲库质量差，命中率低，仅作最后一档）
python3 $S/alt_source_dl.py --artist 某歌手 --todo $TODO --out $OUT --dry-run
```

要点：

- 两脚本都按「平台时长 ±N 秒」严格校验，偏差超阈值记入 `B站需复核.jsonl` / 直接丢弃，**不会把翻唱、Live、消音版当正版下载**。
- `alt_source_dl.py` 额外拦截 <500KB 的响应——酷狗/酷我/咪咕匿名接口对近年新歌常只给 11 秒试听片段，文件头却合法，只看大小会误判。
- 台账 `alt_downloaded.jsonl` 与主流程台账分离，两脚本共享，重跑自动跳过已成功曲目。
- 网络环境若有沙箱拦截，下载阶段需前台运行（搜索能过、CDN 拉流会被拦）。

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
2. 下载输出目录默认 `./music-downloads`（可用 `--out` 或环境变量 `MUSIC_DL_OUT` 覆盖），输出根目录下会生成 `下载记录.jsonl`、`版本待核对.jsonl`、`QQ候选对照.csv` 等清单文件。
3. 若用户提供的 cookie 片段缺少 `MUSIC_U`（网易云）或 `uin`+`qm_keyst`（QQ），接口会返回未登录/无 VIP——见 `references/pitfalls.md` 中「凭证获取」一节的引导话术。

## 五步补全流程

### 第 1 步：生成缺失清单

```bash
python3 scripts/build_missing_list.py --playlist 歌单.csv --lib /path/to/music1 --lib /path/to/music2 --out ./music-downloads
```

- 支持 CSV（含「歌名」「歌手」列，网易云导出格式直接可用；music-playlist-exporter 输出的 `*-歌单完整清单.csv` 也可直接喂）与 m3u8。
- 本地匹配键为标准化后的「歌手 - 歌名」，递归扫描所有 `--lib` 目录。
- 产出 `missing.json`，每项 `{"t","a","alb","dur","sid"}`；`sid` 是网易云歌曲 ID，来自歌单里的链接，**第 2 步必需**。

### 第 2 步：网易云按 ID 直下（主力，约占 90%）

```bash
python3 scripts/netease_dl.py --list ./music-downloads/missing.json --workers 4 --out ./music-downloads
```

- 只用 `sid` 请求播放地址，**绝不用标题搜索**，从机制上杜绝下错版本。
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
- 生成/更新 m3u8（路径必须逐条 `os.path.exists` 校验后才写入），或用 music-playlist-exporter 的 `fill_playlist_paths.py` 回填原留空版。
- 列出所有「待核」「同歌手」条目请用户试听确认。

## 下载台账

下载成功的曲目按「歌曲身份键 norm(歌手 - 歌名)」自动记入统一操作台账（与 music-library-organizer 共用 `~/.cache/music-library-organizer/ledger.json`）。下次跑三个下载脚本时，**台账命中且文件仍存在的曲目在发请求前直接跳过**（打印「台账命中：跳过已下载 N 首」），文件被删才自动重下；补完歌词/封面导致文件 mtime/size 变化**不会**触发重复下载。`netease_dl.py` / `netease_reid.py` / `qq_dl.py` 均已集成，无需手工操作。

## 歌手全集下载（固化流程，2026-11-08 毛不易实战验证）

「下载某个歌手的所有歌曲」按六步走，**长期规则已内置在 `artist_songs.py`，勿删**：

1. **抓全量清单** `scripts/artist_songs.py <歌手名或ID> --out <目录>`：网易云歌手歌曲接口不可用，走「专辑列表+逐张专辑详情」（-462 限流退避已内置）。内置长期过滤规则：① `(伴奏)/(和声伴奏)/(纯音乐)/(Instrumental)` 不进清单；② 同录音既有录音室版又有 `(Live)/(现场)` 版只留录音室版（分组键须剥离 Live 后缀），仅 Live 独有的保留。同录音多专辑收录自动去重。
2. **比对本地库** `scripts/build_missing_list.py`（台账自动跳过已下载）。
3. **三级音源补全**（五步流程第 2–4 步）。⚠️ 本机沙箱网络守卫拦截 `*.music.126.net`，下载必须**前台绕过沙箱**运行。
4. **B 站兜底**（VIP/数字专辑独家曲目）：buvid3 → 搜索 → 时长 ±8s 校验 + 官方账号优先 → DASH 音频 ffmpeg 封装 → mutagen 复核。
5. **元数据补全（必做）** `scripts/enrich_meta.py --dir <音乐目录> --csv <清单.csv>`：歌词（网易云→QQ 兜底）写入标签 + `.lrc` 侧车；封面写入标签 + `cover.jpg`；基本标签补全。幂等。
6. **统计交付**：按主歌口径统计，未获取清单单独导出，B 站来源列请用户试听。

详见 `references/artist_discography.md`。

## 设计原则（改脚本时不得破坏）

1. **版本正确性 > 覆盖率**：拿不准的版本不下载，记录候选交给用户。
2. **无音源 ≠ 失败**：下架/版权受限是常态，如实分类报告（原版受限/仅翻唱可下/需付费），不要硬凑。
3. **幂等**：已存在文件直接跳过（查 `.flac/.mp3/.m4a/.ogg`），可安全重跑；临时文件用 `.part`，失败即清理。
3b. **下载台账**：成功即记账、执行前先查台账（见上节）。
4. **下载后必须核验**：文件头嗅探 + 时长比对，异常进「待核」而不是当成功报。
5. **并发与限流**：workers 3–6 足够；网易云搜索接口限流严重（退避重试已内置），不要盲目调大并发。
6. **本地库只读**：绝不移动/改名/删除音乐库文件；整理需求交给 music-library-organizer。
7. m3u8 用 UTF-8 无 BOM 写出；CSV 用 UTF-8 带 BOM（方便 Excel 直接打开）。

## Resources

- `scripts/mcommon.py` — 共享模块：配置加载、文件名清洗、文件头嗅探、标签写入、下载台账辅助（ledger_init/skip/mark/save）。**所有下载脚本共用，改这里影响全局。**
- `scripts/library_ledger.py` — 操作台账（与 music-library-organizer 的同名模块保持同步，勿单独修改）。
- `scripts/build_missing_list.py` — 歌单×本地库比对，产出 missing.json。
- `scripts/artist_songs.py` — 歌手全集清单抓取（专辑路线 + 长期过滤规则 + 去重）。
- `scripts/enrich_meta.py` — 下载后歌词/封面/标签补全（与 music-library-organizer 生态共用约定；如他处有同名脚本保持同步）。
- `references/artist_discography.md` — 歌手全集六步流程详解（含 B 站兜底管线）。
- `scripts/netease_dl.py` / `netease_reid.py` — 网易云直下 / 替代 ID。
- `scripts/qq_dl.py` / `qq_verify.py` / `qq_probe.py` — QQ 兜底 / 权益验证 / 候选探查。
- `references/workflow.md` — 补全流水线详解：输入格式细节、比对口径、统计口径、m3u8 重建、并发预算。
- `references/pitfalls.md` — 真实踩坑手册：CDN 403、搜索限流、QQ 权益分级、HttpOnly cookie 获取、vkey uin 陷阱等。
- `config.example.json` — 凭证配置模板（复制到 `~/.config/music-downloader/config.json` 后填写）。

## 已验证的实战数据（回归基准）

- 补全流水线：2544 首歌单补全后覆盖率 97.1%（2400+ 首，FLAC 约 91%，总下载 67GB+）。改下载/校验逻辑后用此基准回归。
- 台账：记账→跳过（0.2s 零请求）；文件被改写不触发重下；文件删除自动失效重下。

## 关联技能

- **music-playlist-exporter**：歌单抓取与本地比对（本技能的输入来源，其 CSV 直接可用）。
- **music-library-organizer**：音乐库本地整理（改标签/歌词封面/分类），与台账共用一份 ledger。
- **fnos-playlist-creator**：把网易云/QQ 歌单直接同步为飞牛 NAS 音乐歌单（不经本地下载）。
