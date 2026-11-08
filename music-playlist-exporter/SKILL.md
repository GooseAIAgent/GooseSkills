---
name: music-playlist-exporter
description: 平台歌单获取与导出技能。当用户要求读取/导出网易云或QQ音乐歌单、把歌单转成本地 m3u8、对比歌单与本地音乐库看哪些歌已有/缺失、生成待下载清单 CSV 时使用。覆盖：歌单全量抓取（网易云无需登录，QQ 公开歌单无需登录）、与本地库三层比对（严格+宽松，歌手一致性校验）、导出完整清单 CSV、生成「本地路径+留空待补」完整版 m3u8 与「纯本地可播」m3u8、日后回填留空路径。下载补全由 music-playlist-completer 技能负责。
license: MIT
---

# Music Playlist Exporter（网易云/QQ 歌单 → 本地 m3u8）

## Overview

本技能固化了一套在真实 2544 首歌单 + 944 个本地音频上验证过的完整流程：
**抓取平台歌单全量曲目（网易云/QQ，均无需登录）→ 与本地音乐库逐首比对 → 导出 CSV 清单 → 生成 m3u8 歌单（本地缺失的条目路径留空）→ 日后补全回填**。

**开始任务前：先读 `references/workflow.md`（API 细节、分页、踩坑记录）。**

所有脚本只用 Python 标准库，支持任何能执行 shell/Python 的 Agent。

## 快速决策

| 用户需求 | 做法 |
|---|---|
| 读取/导出网易云歌单 | `scripts/netease_to_m3u8.py <歌单链接>` 一条命令全流程 |
| 读取/导出 QQ 音乐歌单 | `scripts/qq_to_m3u8.py <歌单链接或disstid>` 一条命令全流程 |
| 只想看本地缺哪些歌 | 跑全流程，看 `*-未在本地库中的歌曲.csv` |
| 只生成能播的歌单 | 用输出的 `*-本地可播N首.m3u8`（已去重，全部绝对路径） |
| 歌单已抓过，本地新增了歌，重新比对 | 离线模式 `--csv 已有清单.csv --name 歌单名`（不联网） |
| 已生成留空版 m3u8，日后补路径 | `scripts/fill_playlist_paths.py 歌单.m3u8`（可反复重跑） |
| 把缺失歌曲下载补齐 | **交给 music-playlist-completer 技能**（本技能输出的 CSV 可直接喂给它的 build_missing_list.py） |
| 外语歌单想译成中文 | 属整理范畴，配合 music-library-organizer 技能处理 |

## 标准用法

```bash
S=<本技能目录>/scripts

# 网易云：全流程（抓取 + 本地比对 + CSV + 双 m3u8）
python3 $S/netease_to_m3u8.py "https://music.163.com/playlist?id=331332108" \
    --music-root /Volumes/media-2/music --outdir ./歌单导出

# QQ 音乐：链接或纯数字 disstid 均可（公开歌单无需登录）
python3 $S/qq_to_m3u8.py "https://y.qq.com/n/ryqq/playlist/8888050823" \
    --music-root /Volumes/media-2/music --outdir ./歌单导出

# 小样本联网测试（验证 API 可用性，只抓前 N 首）
python3 $S/netease_to_m3u8.py <链接> --limit 5 --outdir /tmp/test

# 离线模式：不重新抓取，基于已有清单重新比对 + 导出
python3 $S/netease_to_m3u8.py --csv 歌单完整清单.csv --name "歌单名" \
    --music-root /Volumes/media-2/music --outdir ./歌单导出
```

无需任何凭证或登录；`--music-root` 默认 `/Volumes/media-2/music`，按实际库传参。

## 输出文件（`--outdir` 下，按歌单名命名）

| 文件 | 内容 |
|---|---|
| `<歌单名>-歌单完整清单.csv` | 全部曲目：序号/歌名/歌手/专辑/时长/**本地文件（命中为相对路径）**/歌曲链接 |
| `<歌单名>-未在本地库中的歌曲.csv` | 未命中的曲目，即**待下载清单** |
| `<歌单名>-完整N首.m3u8` | 完整歌单：本地有的写绝对路径，**没有的路径行留空** |
| `<歌单名>-本地可播M首.m3u8` | 只含本地文件（按文件去重），可直接导入播放器 |

## 核心设计决策（踩坑换来，勿改）

1. **本地没有的歌，路径留空，绝不写平台外链。** 网易云外链对 VIP/版权受限歌曲返回的是网页而非音频（实测抽样仅约 35% 可播），写进 m3u8 就是大量废条目。
2. **网易云用 `trackIds` 拿全量曲目**（`tracks` 字段只回前 6 首），再分批（100/批）补详情；全程无需 cookie。
3. **宽松匹配必须校验歌手一致性**——只按歌名匹配会把「Sarah McLachlan - Angel」配到「周杰伦 - Angel」上（真实踩坑）。三层匹配规则见下。
4. **QQ 歌单走 `c.y.qq.com` fcg 接口**（`g_tk=5381` 匿名即可），新版 `musicu.fcg` 匿名返回 500003 勿用；返回的 HTML 实体必须 `html.unescape`。

## 匹配规则（三层，由严到宽）

1. **严格**：本地文件名（不含扩展名）归一化后 == `歌手 - 歌名`（归一化 = NFC + 全半角括号统一 + 去空白 + 小写；不拆括号，「夜曲」≠「夜曲(Live)」）。
2. **宽松**：歌名去掉括号说明后相等，**且歌手 token 集合兼容**（相等或互为子集）；无交集直接拒绝。
3. 同一本地文件只被占用一次（`used_paths`）；多候选时取归一化标签相似度最高者（阈值 0.9）。

## 铁律

1. **本地库只读**：绝不移动/改名/删除音乐库文件；整理需求交给 music-library-organizer。
2. **m3u8 用 UTF-8 无 BOM 写出**（BOM 会破坏 `#EXTM3U` 首行识别）；CSV 用 UTF-8 带 BOM（方便 Excel 直接打开）。
3. **路径留空是特性不是缺陷**：向用户说明留空条目 = 待下载，可用 fill_playlist_paths.py 日后回填；不要擅自填外链。留空条目在部分精简播放器上会被跳过或错位，如实告知。
4. **音乐库根目录必须存在**，脚本启动即校验。
5. 歌单链接里的 `uct2=` 等登录参数无用，脚本只取 `id=` / disstid；**私密歌单无法匿名抓取，如实告知，不要硬试**。
6. 分页/限流：网易云 song/detail 批间 1.5s；QQ 分页以 `total_song_num` 收敛 + 无新增断路。

## Resources

- `scripts/netease_to_m3u8.py` — 网易云歌单全流程（抓取/离线重比对/CSV/双 m3u8）。
- `scripts/qq_to_m3u8.py` — QQ 音乐歌单全流程（import 复用 netease 版匹配与导出，只换抓取层）。
- `scripts/fill_playlist_paths.py` — 回填 m3u8 留空条目（可反复重跑）。
- `references/workflow.md` — 完整流程详解：全流程步骤、API 细节、QQ 接口、踩坑记录。

## 已验证的实战数据（回归基准）

- 歌单 331332108（2544 首）vs 本地库 944 个音频：命中 91（旧版盲匹配 139 中含约 40 处歌手张冠李戴，已修正）；完整版留空 2453 条；本地可播去重 85 条；91 条路径全部实存校验通过。改匹配逻辑后务必用此基准对比命中/丢失清单。
- QQ 歌单抓取：公开歌单 8888050823（28 首）全流程通过，本地命中 7/28，输出路径全部实存。

## 关联技能

- **music-playlist-completer**：把本技能输出的缺失清单下载补齐（三级音源策略）。
- **music-library-organizer**：音乐库本地整理（改标签/歌词封面/分类）。
- **fnos-playlist-creator**：把网易云/QQ 歌单直接同步为飞牛 NAS 音乐歌单。
