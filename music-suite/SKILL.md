---
name: music-suite
description: 音乐工具总台（路由技能）。当用户提出任何与音乐相关的任务时先进入本技能做意图分发：下载歌单或歌手全集歌曲、把网易云/QQ 歌单转本地 m3u8、对比歌单与本地音乐库差异、批量整理/重命名/分类本地音乐文件、修复音频标签、补全歌词与封面、按年份-专辑归档、把歌单导入飞牛 NAS（fnOS）音乐、查重清理音乐库等。触发词：音乐、歌曲、歌单、音乐库、下载歌、歌手全集、整理音乐、歌词、封面、标签、m3u8、飞牛、fnOS、NAS 音乐。本技能自身不含任何脚本，只负责把任务路由到四个子技能之一或其组合。
license: MIT
---

# Music Suite 总台（音乐任务路由）

## Overview

本套件包含四个各司其职的音乐技能。**本技能不含任何脚本**，只做两件事：
1. 判断用户意图属于哪个（或哪几个）子技能；
2. 按「典型组合流程」编排多技能接力，避免只跑一半。

| 技能 | 一句话定位 | 关键能力 |
|---|---|---|
| **music-playlist-exporter** | 歌单获取与导出 | 抓取网易云/QQ 歌单全量曲目（无需登录）→ 与本地库三层比对 → 导出 CSV + 双版本 m3u8 |
| **music-playlist-completer** | 歌曲下载补全 | missing 清单 → 三级音源下载（网易云直下 → 替代 ID → QQ 兜底），仍有缺口再用 B 站 / 酷狗酷我咪咕 兜底脚本 → FLAC 优先、版本校验 → 下载某歌手全部歌曲（含伴奏过滤与元数据补全） |
| **music-library-organizer** | 音乐库整理 | 统一「歌手 - 歌名」命名、外语歌名「原名（译名）」、修复内嵌标签、按年份-专辑归档、批量补歌词与封面、查重 |
| **fnos-playlist-creator** | 歌单导入飞牛 NAS | 网易云/QQ/m3u8 → 飞牛音乐：抓取 → 曲库三级匹配 → 幂等写入 → 服务端复核 |

## 路由决策表

| 用户意图 | 路由 |
|---|---|
| 导出/读取网易云或 QQ 歌单，看缺哪些歌 | → exporter |
| 把歌单缺失的歌曲下载到本地 | → exporter（生成清单）→ completer（下载） |
| 下载某歌手的**全部歌曲**（全集） | → completer（歌手全集流程，内置伴奏/Live 过滤 + 下载后补元数据） |
| 补歌词、补封面、写标签、修乱码标签 | → organizer |
| 整理/重命名/归档本地音乐文件 | → organizer |
| 按「年份-专辑」分类存放 | → organizer（先查归属再 organize） |
| 查重复文件、清理音乐库 | → organizer（find_duplicates，只读） |
| 把歌单（云歌单或本地 m3u8）导入飞牛 NAS | → fnos-playlist-creator |
| 歌单里有些歌本地没有，导入 NAS 时缺歌 | → completer 先下载补齐 → organizer 可选整理 → fnos 导入 |

## 典型组合流程

### A. 歌单下载一条龙
```
exporter（抓歌单 + 本地比对 + CSV/m3u8）
  → completer（build_missing_list → 五步下载补全 → 补元数据）
  → 回填 m3u8（exporter 的 fill_playlist_paths.py）
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

## 接力约定

- 各技能之间通过**文件**交接：exporter 输出的清单 CSV 可直接喂给 completer 的 `build_missing_list.py`；organizer 的台账（library_ledger.py）与 completer 的下载台账同目录用法一致。
- 涉及删除、移动、改名的批量操作，**所有子技能都要求先 `--dry-run`/`--check` 预览再 apply**；跨卷移动前先备份。
- 凭证（QQ cookie、飞牛账号等）一律走环境变量或 `~/.config` 下的配置文件，绝不写入 skill 目录或仓库。

## 详细文档

每个子技能开始任务前先读其 `references/workflow.md`；遇到报错查对应 `references/pitfalls.md` 或 `api_reference.md`。
