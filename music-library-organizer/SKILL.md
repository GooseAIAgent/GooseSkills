---
name: music-library-organizer
description: 音乐库整理技能。当用户要求整理、重命名、分类本地音乐文件时使用：统一文件名为「歌手 - 歌名」格式、**外语（韩/日/俄等）歌名统一为「本国原名（中文译名）」并保留版本后缀**、按「年份-专辑名」或年份分类存放、修复 MP3/FLAC 内嵌标签（如播放器显示 ifulicn.com 等下载站水印）、从平台核实专辑名与发行年并批量写入标签、删除文件名多余后缀等；也覆盖批量补全歌词与封面——下载 LRC 歌词（含中文翻译合并）并封装进音频标签、同时落盘同名 .lrc 供音乐服务器识别、抓取专辑封面写入 FLAC PICTURE / MP3 APIC / M4A covr。涉及音频文件名批量修改、专辑归属查询、ID3/FLAC 标签写入与修复、纯年份目录补全专辑信息、.cover 封面与 .lrc 歌词伴生文件处理的场景均适用。
license: MIT
---

# Music Library Organizer（中文音乐库整理）

## Overview

本技能固化了一套在真实音乐库（数千首 FLAC/MP3，含中/韩/英/日文）上验证过的整理流程，覆盖：文件名批量修正、统一「歌手 - 歌名」命名、**外语歌名统一为「原名（中文译名）」**、按专辑/年份分类、内嵌标签修复、**歌词与封面批量补全并封装进标签**。所有数据（专辑名/年份/归属/歌词/封面/原名）必须从平台 API 或互联网核实，禁止杜撰。

适用于任何支持 Agent Skills 标准（SKILL.md）的 AI 编程助手（Claude Code、Codex、OpenCode 等）。

**开始任何任务前：先读 `references/workflow.md` 中对应任务的流程和「通用安全红线」；查平台数据时参考 `references/api_reference.md`。**

## 环境要求

- **Python 3.8+**（唯一运行时依赖，脚本全部只用标准库）。
- **mutagen**（标签读写必需）：执行前先检查 `python3 -c "import mutagen"`，缺失则 `pip3 install --user mutagen`。改名/分类任务可无 mutagen 运行，**标签修复与歌词/封面封装必须安装**。
- **网络访问**：`scripts/music_api.py` 需访问 QQ 音乐（u.y.qq.com）与网易云音乐（music.163.com）；`scripts/lyrics_cover_embed.py` 需访问网易云、lrclib.net、y.qq.com、itunes.apple.com。若运行环境有沙箱，查询时需网络权限；拒绝时降级为 WebSearch 逐首核实（歌词/封面则只能靠本地 `.lrc` 与 `.cover`）。
- **文件系统写入**：音乐库常在工作目录之外（如外置硬盘、NAS 挂载卷），执行移动/改名/写标签前确认有写权限，并提醒用户此类操作需批准。

## 快速决策

| 用户需求 | 做法 |
|---|---|
| 统一命名「歌手 - 歌名」 | `scripts/rename_songs.py`（先 `--dry-run`） |
| 播放器显示乱码/网站名（如 ifulicn.com） | `scripts/fix_tags.py`（先 `--check`）——播放器读内嵌标签不读文件名 |
| 标签为空 / 目录是纯年份，需要真实专辑名 | `scripts/album_tags.py`（先 `--dry-run`）——逐首查平台补 album+date，再连同文件名反写 |
| 按「年份-专辑名」或年份分类 | 先逐首查归属（`scripts/music_api.py`），生成映射 JSON，再 `scripts/organize_music.py`（先 `--dry-run`） |
| 外语歌名（韩/日/俄等）统一成「原名（中文译名）」 | `scripts/native_title_rename.py`（先 `--scan-only` 看量，再预览，最后 `--apply`；见 workflow.md 任务五） |
| 专辑名/歌名需要中文译名 | 译名只作为括号内容，**绝不覆盖原名**；原名取自平台 `name`/`title` 字段，查不到就保持原样并点名 |
| 删文件名多余后缀（数字串等） | 显式规则 + 冲突预判 + 试运行（见 workflow.md 任务一） |
| 「上次做过的操作，这次只处理新增/变动的」 | 用 `scripts/library_ledger.py` 操作台账（见下文「操作台账」）——批量操作完成后记账，下次 `undone` 直接列出待办，不重扫全库 |
| 播放器/音乐服务器没有歌词 | `scripts/scan_tag_state.py` → `scripts/lyrics_cover_embed.py`（见 workflow.md 任务七） |
| 歌曲没有封面图 / 封面是灰块 | 同上，`--only cover`；来源链 `.cover` → 网易 → QQ → iTunes |
| 想让 NAS 音乐服务（飞牛音乐等）显示歌词 | 同上，脚本会同时写内嵌标签与同名 `.lrc`；**注意首次入库扫描前就必须就位** |
| 查有没有重复文件 / 清理重复 | `scripts/find_duplicates.py`（只读，四层判定：字节→音频流→同名同长→同名近长；见 workflow.md 任务八） |

## 脚本用法

所有脚本都是幂等可重跑的，且支持 `--dry-run`。**必须先试运行，检查完整计划清单后再执行。**

路径约定：`$S` 为本技能目录下的 `scripts/`（各 agent 会将技能展开到自己的技能目录，如 `~/.agents/skills/`、`~/.claude/skills/`、`~/.workbuddy/skills/`，以实际安装位置为准）。

```bash
S=<本技能目录>/scripts

# 1. 统一改名「歌手 - 歌名」（多歌手自动转「、」，伴生 .lrc/.cover 同步改名）
python3 $S/rename_songs.py /path/to/music --dry-run
python3 $S/rename_songs.py /path/to/music --tags   # 文件名顺序歧义时以内嵌标签为准

# 2. 修复标签：从文件名反写 标题/艺术家/专辑，清除下载站水印字段，保留封面/APEv2
python3 $S/fix_tags.py /path/to/歌手 --check
python3 $S/fix_tags.py /path/to/歌手
python3 $S/fix_tags.py /path/to/歌手 --watermark "ifulicn,chenjiebiao.cn,雅音FM"  # 多个站名

# 3. 批量写专辑标签：目录是纯年份（2019/、2020/）或标签为空时，逐首查平台补专辑名+发行日
python3 $S/album_tags.py /path/to/歌手 --dry-run    # 先用已有缓存出计划
python3 $S/album_tags.py /path/to/歌手              # 查询 + 写入（带限流退避与断点续跑）
python3 $S/album_tags.py /path/to/歌手 --write-only # 只写缓存，不重新查询

# 4. 平台查询（单次或 import 到循环脚本中批量用）
python3 $S/music_api.py search "薛之谦 演员"      # QQ音乐搜索: 专辑/歌手/发行日期
python3 $S/music_api.py album <albummid>          # QQ音乐专辑详情: 曲目数/发行日
python3 $S/music_api.py netease-artist 毛不易     # 网易云歌手ID
python3 $S/music_api.py netease-albums <歌手id>   # 网易云全部专辑+日期

# 5. 按映射表分类移动（未登记文件会报错拦截；MD5 防幻影副本）
python3 $S/organize_music.py /path/to/歌手 map.json --dry-run
python3 $S/organize_music.py /path/to/歌手 map.json

# 6. 歌单（m3u8）路径补全：把留空的 #EXTINF 条目按本地库回填绝对路径
python3 $S/fill_playlist_paths.py 歌单.m3u8 --dry-run
python3 $S/fill_playlist_paths.py 歌单.m3u8 --music-root ~/Music

# 7. 扫描「哪些歌缺歌词/缺封面」（增量、可断点续扫）
python3 $S/scan_tag_state.py /path/to/music
python3 $S/scan_tag_state.py /path/to/music --state ~/lib_state.json   # 自定义状态文件

# 8. 批量补歌词 + 封面并封装进标签（幂等，重跑自动跳过已完成）
python3 $S/lyrics_cover_embed.py --lib /path/to/music --dry-run        # 先看要处理多少首
python3 $S/lyrics_cover_embed.py --lib /path/to/music --workers 4
python3 $S/lyrics_cover_embed.py --lib /path/to/music --only cover     # 只补封面
python3 $S/lyrics_cover_embed.py --lib /path/to/music --alias alias.json  # 中文译名/韩文原名对不上时

# 9. 外语歌名统一为「本国原名（中文译名）」（同步 .lrc 与 title 标签，幂等）
python3 $S/native_title_rename.py /path/to/music --scan-only          # 只列本地候选，不联网
python3 $S/native_title_rename.py /path/to/music --artist IU          # 预览某个歌手
python3 $S/native_title_rename.py /path/to/music --artist IU --apply  # 执行
python3 $S/native_title_rename.py /path/to/music --overrides ov.json --apply  # 人工核实表兜底

# 10. 查重（只读，不删不改）：四层判定，输出 CSV/Markdown 报告
python3 $S/find_duplicates.py /path/to/music --report dup.csv
python3 $S/find_duplicates.py /path/to/music --min-level 2      # 只看「音频内容相同」的强重复
python3 $S/find_duplicates.py /path/to/music --no-ffmpeg        # 跳过 MP3 解流比对，快很多
```

歌单约定：本地未收录的曲目只保留 `#EXTINF` 标题、路径行**留空**（不写平台外链），待下载入库后用脚本 6 回填。
`fill_playlist_paths.py` 三层匹配（文件名全等 → NFC 归一后相等 → 歌名宽松匹配 **且歌手集合兼容**、候选唯一），未命中的继续留空，可反复重跑。
宽松层必须校验歌手：`Sarah McLachlan - Angel` 不能配到 `周杰伦 - Angel.flac`，`莫文蔚 - 爱` 不能配到 `小虎队 - 爱.flac`。

`fix_tags.py` 与 `album_tags.py` 的分工：前者专辑名取自「年份-专辑名」文件夹，适合已按专辑归档的库；
后者才查平台，适合**纯年份目录**或标签全空的库。查不到就留空并点名，**不要拿文件夹年份冒充专辑名**。

分类映射 JSON 格式：`{"歌手 - 歌名.flac": "2020-小王", "歌手 - 单曲.flac": "2020-合辑"}`。

## 歌词 + 封面封装（脚本 7/8）

三步走：**扫状态 → 试跑看量 → 正式跑**。状态文件（`$MUSIC_LIB_STATE`，默认
`~/.cache/music-library-organizer/lib_state.json`）与日志（`~/.cache/music-library-organizer/embed_log.jsonl`）
共同保证长任务可断点续跑：中断后重跑只处理剩下的；「无源/无ID」这类确定性结论不再重试。

写入位置与数据源：

| 项 | FLAC | MP3 | M4A | 数据源（按优先级） |
|---|---|---|---|---|
| 歌词 | `LYRICS` + `UNSYNCEDLYRICS` | `USLT` | `©lyr` | 网易云 `/api/song/lyric`（自动合并中文翻译）→ lrclib.net |
| 封面 | `PICTURE` type=3 | `APIC` type=3 | `covr` | 同目录 `.cover/` → 网易云专辑图 → QQ 音乐 `photo_new` → iTunes |

歌词还会**同时落盘同名 `.lrc`**（UTF-8），因为不少播放器/音乐服务器只读伴生文件而不认内嵌标签。
不需要时加 `--no-sidecar`。

匹配一律三重校验（歌名归一全等 + 歌手有交集 + 时长差 ≤ `--dur-tol`），宁缺勿错。
标签名与平台名对不上时（典型：库里是中文译名、平台只有韩文原名）用 `--alias alias.json`：

```json
{"IU - 唠叨": "잔소리",
 "IU - 好日子": {"q": "좋은 날 IU", "title": "좋은 날"}}
```

网易云 cookie 与 QQ 凭证是**可选**的（匿名也能取到大部分数据），需要时写进
`~/.config/music-library-organizer/config.json` 或用环境变量：

```bash
export NETEASE_COOKIE='MUSIC_U=...'       # 提高歌词/封面命中率
export QQ_UIN=123456789 QQ_KEYST='Q_H_L_...' # QQ 侧取封面更稳
```

参考 `config.example.json`。

## 操作台账（library_ledger.py）

任何批量操作（改名 / 写标签 / 补歌词 / 补封面 / 分类移动……）完成后，把成功处理的文件记入统一台账（`~/.cache/music-library-organizer/ledger.json`，环境变量 `MUSIC_LIB_LEDGER` 可覆盖）。台账按「文件 mtime + size 指纹」判定有效性：文件被动过，条目自动失效——所以它永远是「做过且没再动过」的准确集合，**下次同类型操作只处理 `undone` 列出的文件，不必重扫全库**。

```bash
S=<本技能目录>/scripts

# 批量操作做完后记账（脚本集成会自动记；手工补记也可以）
python3 $S/library_ledger.py mark lyrics <文件路径>... --detail netease
python3 $S/library_ledger.py mark classify --from-json 处理记录.jsonl   # 每行 {"p": 路径}

# 下次做同类型操作：先列出待办（只查 mtime，秒级），把清单喂给操作脚本
python3 $S/library_ledger.py undone lyrics ~/Music
python3 $S/library_ledger.py status                 # 各操作各完成多少、多少条过期

# 维护
python3 $S/library_ledger.py forget lyrics <路径>   # 撤销单条（重跑前清掉）
python3 $S/library_ledger.py prune                  # 清理文件已不存在的条目
```

约定：操作名用 `rename` / `tags` / `lyrics` / `cover` / `native-title` / `classify` / `dedupe`。
已集成：`lyrics_cover_embed.py` 成功后自动记 `lyrics`/`cover`；`scan_tag_state.py` 会跳过台账认定「歌词+封面双全且未变动」的文件（一次都不用打开音频）。
**新写的批量脚本都应遵守同一模式：成功即记账，执行前先查 `undone`。**

跨技能共用：`music-playlist-completer` 也用这同一份台账（操作名 `download`，脚本 `netease_dl.py`/`netease_reid.py`/`qq_dl.py` 已集成，下载成功自动记账、下次命中自动跳过）。下载类条目按「歌曲身份键」记账，完成判定用 **exists 模式**（文件还在即算完成——之后补歌词/封面会改 mtime/size，但歌已经在了不应重下；文件被删才自动失效）；标签/写入类条目按 **fingerprint 模式**（文件被再编辑即失效重做）。两个 skill 里各有一份 `library_ledger.py` 副本，内容必须保持同步。



## 外语歌名命名规范（脚本 9）

**默认约定：外语歌曲一律命名为 `歌手 - 本国原名（中文译名） [原版本后缀]`。**

```
IU - 분홍신（粉红色高跟鞋）
IU - 크레파스（蜡笔） (电视剧《漂亮男人》插曲)
IU - 잔소리(WITH 2AM 슬옹)（唠叨）
米津玄師 - 打上花火（烟花）
```

适用范围：韩语、日语、俄语、泰语……凡「平台有本国原名」的外语歌都按此命名；英语原名本身就是国际通用名，默认不动（`--scripts` 可显式加入 `latin`）。专辑名暂不做此处理。

关键设计（照做，别改）：

| 环节 | 规则 |
|---|---|
| 触发条件 | 文件名里标题含中文、且不含本国文字（Hangul/Jamo、假名）→ 视为「被译成中文的外语歌」候选 |
| 原名来源 | **QQ 音乐 `name` 字段**（`title` 字段本身就是「原名 (中文译名)」）；网易云 `tns` 补漏 |
| 译名来源 | 候选 `title` 括号内的中文；取不到就用库里现有中文名（不新造译名） |
| 三重校验 | 歌名语义（本地中文名出现在候选 `title` 中，或字面重合 ≥60%）+ 歌手有交集 + 时长差 ≤6s |
| 版本后缀 | `(Live)` `(Inst.)` `(Acoustic Ver.)` `(小剧场版)` `(电视剧《…》插曲)` 等原样保留；后缀本身就是重复译名的（如 `(望过之后)`）丢弃 |
| 同名文件 | 同目录撞名时补 ` (2)`、` (3)` |
| 人工核实 | 匹配不上的写入 `--overrides ov.json`：`{"IU|毕业日": {"native": "졸업하는 날", "zh": "毕业日"}}` |
| 幂等 | 标题已含本国文字的自动跳过，可反复重跑 |
| 执行门槛 | 默认 `--min-conf medium`；`--min-conf high` 只改三重校验全过的 |

执行顺序固定为：`--scan-only`（看本地候选量）→ 预览（联网出完整清单，写 `rename-record.csv`）→ 人工扫一遍 medium 条目 → `--apply`。

## 铁律（违反会造成数据损失或错误归类）

1. **试运行 → 人工核对 → 执行 → 校验总数**，四步缺一不可。
2. **移动文件必须 MD5 校验**：部分卷宗（尤其 NAS 挂载）有「目标写入但源未删」的幻影副本问题；用 `/bin/mv` 子进程而非 `shutil.move`。
3. **删除需用户确认**（卷宗可能无废纸篓/回收站，不可恢复）；空文件夹用 `/bin/rmdir` 绝对路径直调。
4. **文件名匹配前做 NFC 归一化**（macOS NFD 陷阱，韩文/中文必踩）；**写入标签的文本也要归一成 NFC**，否则同一首歌在 macOS 与 Windows 播放器里可能显示成两个条目（`Zezé` 的 é 会分成 e+́）。
5. **映射驱动防遗漏**：未登记文件报错停下，不静默跳过。
6. **专辑数据可溯源**：QQ 音乐 `timePublic` + `fcg_v8_album_info_cp.fcg` 曲目数为准；网易云易限流(-462)作备用与交叉验证；冷门歌 WebSearch。
7. **伴生文件同步**：`.lrc` 歌词、`.cover/<原名>.<ext>.<img>` 封面随歌曲一起改名。
8. **平台搜索返回空列表 = 限流，不是查不到**：QQ 音乐会成片返回空结果（特征是连续编号区间全空）。必须退避重试，直接记为「未找到」会漏掉大批本可命中的歌。
9. **判断重复看音频流，不看文件大小**：FLAC 无损，体积只反映压缩率不反映音质。是否同一份音频要比对 `FLAC.info.md5_signature`（STREAMINFO 内的原始 PCM MD5）。
10. **写标签前先看已有内容**：已有歌词/封面默认不覆盖（脚本按状态文件跳过）；确需重刷时用 `--only` + 手动清理该项，不要无脑全量重写——用户可能已经手工修正过歌词。
11. **平台返回的「纯音乐，请欣赏」不是歌词**：这类占位文本必须过滤，写进标签会让整库出现大量假歌词。
12. **封面别塞巨图**：默认 `--cover-max-kb 1500`，超限自动降尺寸或换源。单张 3–5MB 的 PNG 会让整库膨胀几个 GB，且移动端加载明显变慢。
13. **NAS 音乐服务器（飞牛音乐、Navidrome、Jellyfin 等）的歌词识别时机**：多数实现是「内嵌标签优先，其次同目录同名 `.lrc`」，且**只在首次入库扫描时读取**。入库之后再补的歌词/封面往往不会自动刷新，需要手动「重新扫描媒体库」或清缓存。**新增歌曲务必在服务端扫描前就把 `.lrc`/封面放好**；已入库的批量补完后台重扫一次。
14. **外语歌名不翻译、只加译名**：原名必须来自平台字段（QQ `name` / 网易 `tns`）或用户维护的 overrides，**禁止自行音译/意译**；查不到原名就保持原样并写进报告，不能猜。
15. **「本国文字」要按 NFD 检测**：macOS 会把韩文存成分解形式（Jamo `U+1100–11FF`），只用 `[\uAC00-\uD7A3]` 判断会漏掉整个库的韩文文件名。判定/去重前先 `unicodedata.normalize("NFC")`。
16. **改名要连坐 title 标签**：只改文件名不改 `title`，播放器与后续歌词匹配仍读旧中文名，等同白改。脚本已同步写标签，手工改名单时必须补做。
17. **批量操作成功必须记台账**：用 `library_ledger.py` 的 `record`/`mark` 记下处理过的文件，下次同类型操作先查 `undone`，只处理新增/变动文件，不重扫全库。台账按 mtime+size 指纹判失效，文件动过自动重做——不要用「日期 + 全量重跑」代替台账。

## 边界判定速查（分类时常见）

- 曲目数判定正规专辑：以专辑详情接口的曲目列表长度为准，Live 现场版/伴奏不算正式曲目。
- 单曲后收入专辑 → 默认按**原创首发**归（OST/单曲年份），向用户说明可改「按专辑收录归」。
- 精编版/纪念版归入自身（以其发行年），不归原专辑。
- 同歌多版本（独唱/合唱/DJ版）可能属不同专辑，逐版本查。
- 群星翻唱合辑不算该歌手正规专辑。
- 无官方中文名的老歌翻唱 → 直译或官方英文名，并明确告知用户哪些是直译。
