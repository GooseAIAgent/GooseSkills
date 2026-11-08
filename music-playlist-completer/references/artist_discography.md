# 歌手全集下载工作流（artist_discography）

> 用户明确要求的固化流程（2026-11-08 毛不易全集实战验证：262 首清单 → 主歌 154/158 在库，全流程零错版）。
> 核心原则不变：**版本正确性 > 覆盖率**，无音源如实报告不硬凑。

## 总流程

```
1. artist_songs.py 抓全量清单（含长期过滤规则）
2. build_missing_list.py 比对本地库 → missing.json
3. 三级音源补全（本技能五步流程第 2–4 步）
4. B 站音源兜底（VIP/数字专辑独家曲目）
5. enrich_meta.py 补歌词/封面/标签（用户长期要求，必做）
6. 统计交付 + m3u8
```

## 第 1 步：抓全量清单

```bash
python3 scripts/artist_songs.py <歌手名或网易云ID> --out <工作目录>
```

- **抓取路线**：歌手歌曲接口 `/api/artist/songs` 需要加密请求不可用，走「专辑列表 `/api/artist/albums`（分页 100）→ 逐张 `/api/album/<id>`」。
- **限流**：专辑详情高频触发网易云 `-462`，`get()` 已内置退避（10s 起步、上限 60s）+ 带 cookie，99 张专辑约 3–4 分钟。
- **内置长期过滤规则（用户明确要求，默认开启）**：
  1. `(伴奏)` `(和声伴奏)` `(纯音乐)` `(Instrumental)` 等伴奏类版本不进入清单；
  2. 同一录音既有录音室版又有 `(Live)/(现场)` 版 → 只保留录音室版（分组键须**剥离 Live 后缀**后再比对，否则漏滤）；仅 Live 独有的保留。`--keep-live` 可临时关闭此规则。
- **去重**：同录音多专辑收录按 `(norm(歌名), norm(歌手), 时长)` 去重。
- 产出 CSV（`序号/歌名/歌手/专辑/时长/fee/网易云链接`，UTF-8 BOM）可直接喂第 2 步。

## 第 2 步：比对本地库

```bash
python3 scripts/build_missing_list.py --playlist <歌手>-全部歌曲.csv --lib /Volumes/media-2/music --out ./music-downloads
```

台账自动跳过历史下载（exists 模式），无需手工干预。

## 第 3 步：三级音源补全

同本技能五步流程第 2–4 步（网易云直下 → 替代 ID → QQ 兜底）。**注意沙箱坑见 pitfalls「沙箱网络守卫」**。

## 第 4 步：B 站音源兜底（VIP/数字专辑独家曲目）

三级音源全空时（多为影视 OST、新数字专辑），B 站官方/官方分站通常有录音室版本：

```
finger/spi 拿 buvid3 → /x/web-interface/search/type 搜索
  → 时长校验(±8s) + 排除翻唱/cover/DJ/教学 + 官方账号优先
  → /x/player/wbi/v2 或 view+playurl(fnval=16) 取 DASH 音频流
  → ffmpeg -c copy 封装 .m4a → mutagen 复核时长
```

要点：
- 搜索接口需要 `buvid3` cookie（`/x/frontend/finger/spi` 的 `b_3`），否则返回 -412。
- 上传者疑似「伴奏室/K歌工场」的时长吻合条目**不自动采信**，记入待复核清单交用户试听。
- MV 音源时长含片头/前奏（+20~25s 属正常，记录说明）。
- 群星合辑歌手段落超长会触发 `[Errno 63] 文件名过长`，用 `毛不易 - 歌名` 短名保存。

## 第 5 步：元数据补全（必做，用户长期要求）

```bash
python3 scripts/enrich_meta.py --dir <音乐目录> --csv <歌手>-全部歌曲.csv [--newermt "YYYY-MM-DD"]
```

- 歌词：网易云 `api/song/lyric` → QQ 兜底（写入标签 + 同名 `.lrc` 侧车）。
- 封面：网易云 `api/v3/song/detail` 的 `al.picUrl`（1500px，写入标签 + 专辑目录 `cover.jpg`）。
- 基本 title/artist/album 标签缺失时补全（B 站来源 m4a 常缺）。
- 幂等：已有歌词/封面的文件自动跳过。
- 详细坑见脚本头部与 pitfalls「元数据补全」。

## 第 6 步：统计交付

- 覆盖率按「主歌口径」统计（不含伴奏/Live）；未获取清单单独导出 CSV（区分主歌/衍生）。
- m3u8 路径逐条 `os.path.exists` 校验。
- B 站来源与 `--relax` 条目列出请用户试听确认。
