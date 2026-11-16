---
name: music-suite
description: 一站式本地音乐库管理技能，涵盖四大模块：(1) 歌单获取与导出——抓取网易云/QQ 歌单全量曲目（无需登录）、与本地库比对、导出 CSV 与 m3u8；(2) 歌曲下载补全——缺失清单三级音源下载（网易云直下→替代 ID→QQ 兜底，含 B 站/酷狗酷我咪咕兜底）、FLAC 优先、版本严格校验、歌手全集下载（内置伴奏/Live 过滤）、下载后补歌词封面元数据；(3) 音乐库整理——批量重命名「歌手 - 歌名」、外语歌名「原名（中文译名）」、修复内嵌标签、按年份-专辑归档、补歌词封面写标签、查重；(4) 导入飞牛 NAS——网易云/QQ/m3u8 歌单同步进飞牛音乐（fnOS），三级匹配 + 幂等写入 + 服务端复核。当用户提出任何与音乐、歌曲、歌单、音乐库相关的任务时使用本技能，先做意图分发再按流程执行。触发词：音乐、歌曲、歌单、音乐库、下载歌、歌手全集、整理音乐、歌词、封面、标签、重命名、归档、查重、m3u8、网易云、QQ音乐、飞牛、fnOS、NAS 音乐、媒体库。
license: MIT
---

# Music Suite（一站式本地音乐库管理）

## Overview

本技能把原先四个独立音乐技能合并为**一个自包含技能**，一次安装即可使用全部能力。所有脚本都在 `scripts/` 下，所有详细文档都在 `references/` 下按模块分组。开始任务前先按「路由决策表」判断属于哪个模块，再读对应模块的参考文档，避免只跑一半流程。

> 路径约定：`$S` = 本技能目录下的 `scripts/`；`$R` = 本技能目录下的 `references/`。宿主会把技能展开到自己的目录（如 `~/.workbuddy/skills/`、`~/.claude/skills/`），以实际安装位置为准。

| 模块 | 一句话定位 | 关键能力 |
|---|---|---|
| **exporter**（歌单导出） | 平台歌单 → 本地清单 | 抓取网易云/QQ 歌单全量曲目（无需登录）→ 与本地库三层比对（校验歌手一致性）→ 导出 CSV + 双版本 m3u8 |
| **completer**（下载补全） | 缺失清单 → 本地音频 | 三级音源下载（网易云 ID 直下 → 替代 ID → QQ 兜底），缺口再用 B 站/酷狗酷我咪咕兜底；FLAC 优先、版本严格校验、下载台账幂等；含**歌手全集下载**（内置伴奏/Live 过滤）+ 元数据补全 |
| **organizer**（音乐库整理） | 杂乱文件 → 规整库 | 统一「歌手 - 歌名」命名、外语歌名「原名（译名）」、修复内嵌标签、按年份-专辑归档、批量补歌词与封面、查重 |
| **fnos**（导入飞牛 NAS） | 歌单 → 飞牛音乐 | 网易云/QQ/m3u8 → 飞牛音乐：匿名抓取 → 曲库三级匹配 → 幂等写入 → 服务端复核 |

## 路由决策表

| 用户意图 | 路由到 |
|---|---|
| 导出/读取网易云或 QQ 歌单，看缺哪些歌 | exporter |
| 把歌单缺失的歌曲下载到本地 | exporter（生成清单）→ completer（下载） |
| 下载某歌手的**全部歌曲**（全集） | completer 歌手全集流程（内置伴奏/Live 过滤 + 下载后补元数据） |
| 补歌词、补封面、写标签、修乱码标签 | organizer |
| 整理/重命名/归档本地音乐文件 | organizer |
| 按「年份-专辑」分类存放 | organizer（先查归属再 organize） |
| 查重复文件、清理音乐库 | organizer（find_duplicates，只读） |
| 把歌单（云歌单或本地 m3u8）导入飞牛 NAS | fnos |
| 歌单里有些歌本地没有，导入 NAS 时缺歌 | completer 先下载补齐 → organizer 可选整理 → fnos 导入 |

## 典型组合流程

### A. 歌单下载一条龙
```
exporter（抓歌单 + 本地比对 + CSV/m3u8）
  → completer（build_missing_list → 五步下载补全 → 补元数据）
  → 回填 m3u8（fill_playlist_paths.py）
  → organizer（可选：统一命名/补歌词封面/归档）
```

### B. 歌手全集一条龙
```
completer（artist_songs.py 抓全集清单，自动过滤伴奏与可替代 Live 版）
  → 五步下载补全 → enrich_meta.py 补歌词/封面/标签
  → organizer（按「年份-专辑」归档）
```

### C. 上 NAS 听歌
```
方案1（歌单已在本地）: 直接 fnos（--netease/--qq/--m3u8）
方案2（本地库不全）:   exporter → completer 下载补齐 → organizer 整理
                       → fnos（--m3u8 本地可播版，命中率最高）
```

## 各模块命令速查

开始任一模块任务前，**先读该模块的参考文档**（见文末 References），里面有完整流程、铁律与踩坑记录。下面只是命令索引。

### exporter — 歌单导出
```bash
# 网易云歌单全流程（抓取 + 比对 + CSV + 双 m3u8）
python3 $S/netease_to_m3u8.py "<歌单链接>" --music-root <本地库目录> --outdir ./歌单导出
# QQ 音乐歌单（链接或纯数字 disstid）
python3 $S/qq_to_m3u8.py "<歌单链接>" --music-root <本地库目录> --outdir ./歌单导出
# 离线重比对（不重新抓取）
python3 $S/netease_to_m3u8.py --csv "歌单完整清单.csv" --name "歌单名" --music-root <本地库目录>
# 回填 m3u8 留空路径
python3 $S/fill_playlist_paths.py 歌单.m3u8 --music-root <本地库目录>
```

### completer — 下载补全
```bash
# 歌手全集清单（内置伴奏/Live 过滤）
python3 $S/artist_songs.py "<歌手名或ID>" --out <工作目录>
# 生成缺失清单
python3 $S/build_missing_list.py --playlist 歌单.csv --lib <本地库> --out ./music-downloads
# 第2步 网易云按 ID 直下（主力 ~90%）
python3 $S/netease_dl.py --list ./music-downloads/missing.json --workers 4 --out ./music-downloads
# 第3步 网易云替代 ID 搜索
python3 $S/netease_reid.py --list <无音源.json> --out ./music-downloads
# 第4步 QQ 兜底（先验证权益）
python3 $S/qq_verify.py && python3 $S/qq_dl.py --list <剩余缺失.json> --workers 3 --out ./music-downloads
# 兜底音源：B 站 → 酷狗/酷我/咪咕
python3 $S/bilibili_dl.py --artist <歌手> --todo missing.json --out <目录> --dry-run
python3 $S/alt_source_dl.py --artist <歌手> --todo missing.json --out <目录> --dry-run
# 下载后补元数据（歌词+封面+标签）
python3 $S/enrich_meta.py --dir <音乐目录> --csv <清单.csv>
# 台账状态
python3 $S/library_ledger.py status
```

### organizer — 音乐库整理
```bash
# 统一改名（先 dry-run）
python3 $S/rename_songs.py <目录> --dry-run
# 修复乱码/水印标签
python3 $S/fix_tags.py <目录> --check
# 补专辑名与发行年
python3 $S/album_tags.py <目录> --dry-run
# 平台查询（专辑/歌手/发行日）
python3 $S/music_api.py search "歌手 歌名"
# 按映射表分类移动
python3 $S/organize_music.py <目录> map.json --dry-run
# 外语歌名「原名（译名）」
python3 $S/native_title_rename.py <目录> --scan-only
# 扫描缺歌词/封面状态 → 批量补全
python3 $S/scan_tag_state.py <目录>
python3 $S/lyrics_cover_embed.py --lib <目录> --dry-run
python3 $S/lyrics_cover_embed.py --lib <目录> --workers 4
# 查重（只读）
python3 $S/find_duplicates.py <目录> --report dup.csv
```

### fnos — 导入飞牛 NAS
```bash
# 凭证走环境变量
export FNOS_SERVER=http://<NAS_IP>:5666 FNOS_USER=<账号> FNOS_PASS=<密码>
python3 $S/fnos_playlist_creator.py --netease <ID或链接>       # 网易云
python3 $S/fnos_playlist_creator.py --qq <disstid或链接>       # QQ
python3 $S/fnos_playlist_creator.py --m3u8 歌单.m3u8           # 本地 m3u8
# 先看匹配率不动 NAS：任一命令加 --dry-run
```

## 接力与数据约定

- 各模块之间通过**文件**交接：exporter 输出的清单 CSV 可直接喂给 completer 的 `build_missing_list.py`；completer 输出可直接回填 m3u8 或交给 organizer 整理。
- **统一操作台账**：completer 与 organizer 共用同一份台账（`~/.cache/music-library-organizer/ledger.json`，环境变量 `MUSIC_LIB_LEDGER` 可覆盖），本包内只有一份 `library_ledger.py`。下载/改名/写标签成功即记账，下次只处理 `undone` 文件，不重扫全库。
- **运行时数据路径保持一致**：`~/.cache/music-library-organizer/`（台账/状态/日志）、`~/.config/music-downloader/config.json`（下载凭证）、`~/.config/music-library-organizer/config.json`（整理凭证）。这些是用户数据，不随技能走，合并后旧数据继续可用。
- 涉及删除、移动、改名的批量操作，**一律先 `--dry-run`/`--check` 预览再执行**；跨卷移动前先备份。

## 安全红线（全模块通用）

1. **凭证绝不入库**：QQ cookie、网易云 cookie、飞牛账号等一律走环境变量或 `~/.config` 下的配置文件，绝不写进技能目录或仓库。模板见 `config.example.json`。
2. **本地库只读（exporter/completer）**：下载与导出侧绝不移动/改名/删除音乐库文件；整理需求交给 organizer。
3. **版本正确性 > 覆盖率**：拿不准的版本不下载不写入，记录候选交给用户；伴奏/纯音乐/Live（有录音室版时）一律过滤。
4. **匹配先归一化**：macOS NFD 与平台 NFC 的文件名差异靠 NFC 归一化抹平；宽松匹配必须校验歌手一致性，防止「 Sarah McLachlan - Angel」配到「周杰伦 - Angel」。
5. **LAN 请求绕过代理**（fnos）：`HTTP(S)_PROXY` 会让请求 502，脚本已强制 `--noproxy`；必须用 curl 子进程而非 urllib 发请求（macOS 本地网络 TCC 会拦 python 直连）。
6. **写完看服务端复核数字**（fnos），应等于写入数；`--dry-run` 先行。
7. **试运行 → 人工核对 → 执行 → 校验总数**，四步缺一不可。

## References（按模块）

- `references/exporter/workflow.md` — 歌单抓取全流程、API 细节、分页限流、三层匹配、踩坑记录。
- `references/completer/workflow.md` — 五步补全流水线：输入格式、比对口径、统计口径、m3u8 重建、并发预算。
- `references/completer/pitfalls.md` — 真实踩坑手册：CDN 403、搜索限流、QQ 权益分级、cookie 获取、vkey uin 陷阱等。
- `references/completer/artist_discography.md` — 歌手全集六步流程（含 B 站兜底管线）。
- `references/organizer/workflow.md` — 整理任务全流程与「通用安全红线」（八个任务，务必先读对应章节）。
- `references/organizer/api_reference.md` — QQ/网易平台 API 字段、专辑归属判定、外语原名专用字段。
- `references/fnos/api.md` — 飞牛音乐 API + 网易云/QQ 抓取接口清单与踩坑记录。

## 依赖

- Python 3.8+（全部脚本仅用标准库）。
- **mutagen**（organizer 写标签、completer 写元数据必需）：`python3 -c "import mutagen"`，缺失则 `pip3 install --user mutagen`。
- **curl**（fnos 必需，系统自带）。
- 部分下载兜底与查重脚本会调用 `ffmpeg`（可选，缺失时脚本会降级或提示）。
