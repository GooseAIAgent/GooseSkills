# 音乐平台数据查询参考（实战验证版）

查询歌曲归属专辑、发行日期、专辑曲目数的两个可靠数据源。**所有归类决定必须基于真实查到的数据，禁止凭记忆杜撰专辑名/年份。**

## QQ 音乐（首选，稳定且带发行日期）

### 歌曲搜索（含专辑与发行日期）

POST `https://u.y.qq.com/cgi-bin/musicu.fcg`，JSON body：

```json
{"req_1":{"method":"DoSearchForQQMusicDesktop","module":"music.search.SearchCgiService",
 "param":{"search_type":0,"query":"歌手 歌名","page_num":1,"num_per_page":10}}}
```

Headers：`Content-Type: application/json`、`Referer: https://y.qq.com/`、常规 UA。

结果路径：`req_1.data.body.song.list[]`，每项取 `name`、`singer[].name`、`album.name`、`album.mid`、`timePublic`（发行日期 YYYY-MM-DD，最可靠）。

**外语原名专用字段（`scripts/native_title_rename.py` 依赖）**：

| 字段 | 含义 | 例（IU - 唠叨） |
|---|---|---|
| `name` | **本国原名** | `잔소리(WITH 2AM 슬옹)` |
| `title` | 原名 + 中文译名，格式 `原名 (译名)` | `잔소리(WITH 2AM 슬옹) (唠叨)` |
| `interval` | 时长（秒），用于三重校验 | `194` |
| `singer[].name` | 歌手（可能是合作歌手列表） | `["IU","2AM 슬옹"]` |

取译名：从 `title` 尾部的全角/半角括号里抽中文；`title` 里没有中文（如 `BBIBBI`）说明该曲官方名就是英文，**不要自己造译名**。
网易云侧的对应字段是 `tns`（翻译名数组），但多数曲目为空，且 `name` 常已被官方译成中文（`坏日子`），所以只能作补充，不能当原名来源。

### 专辑详情（判定曲目数）

GET `https://c.y.qq.com/v8/fcg-bin/fcg_v8_album_info_cp.fcg?albummid={albummid}&format=json`

结果：`data.aDate`（发行日期）、`data.list[]`（曲目，取 `songname`）。**专辑曲目数以此为准**（判定「正规专辑 vs 合辑」的硬依据）。

注意：`GetAlbumDetail` (music.musichallAlbum.AlbumInfoServer) 模块返回的 `trackInfo` 字段时有时无，优先用上面的 `fcg_v8_album_info_cp.fcg` 接口。旧接口 `client_search_cp` 已基本无结果，不要用。

## 网易云音乐（备用/交叉验证）

- 搜索歌手拿 ID：`https://music.163.com/api/search/get/web?s={名}&type=100&limit=5` → `result.artists[].id`
- 歌手全部专辑：`https://music.163.com/api/artist/albums/{id}?limit=50&offset={n}`（列表接口自带 `publishTime`，**不易限流**，时间戳 ms 转 UTC+8）
- 专辑详情：`https://music.163.com/api/v1/album/{id}` 或 `/api/album/{id}` → `album.songs[]`

**限流警告**：网易云详情接口极易返回 `code: -462`（验证码）。对策：请求间隔 ≥1.5–4 秒、遇 -462 退避 8s×n 重试、优先使用列表接口、失败就换 QQ 音乐。Headers 必须带 `Referer: https://music.163.com/` 和桌面 UA。

**另一种限流是 `code: 405`**（批量请求歌词/封面接口时高频出现）：这是**IP 级**封禁，
比 -462 更狠，空闲数十分钟到数小时都不一定恢复。已登录 cookie 也不能豁免。
对策：限速 + 连续 3 次 405 熔断 5 分钟，熔断期间封面改用 QQ/iTunes、歌词改用 lrclib，
「无源」结论只在拿到 200 但字段为空时才记。

## 歌词与封面接口（补全标签专用）

### 网易云歌词（首选）

GET `https://music.163.com/api/song/lyric?id={sid}&lv=1&kv=1&tv=-1`

- `lrc.lyric`：原文，带 `[mm:ss.xx]` 时间轴
- `tlyric.lyric`：翻译（`tv=-1` 才会返回），同样带时间轴
- 无歌词时 `lrc.lyric` 可能是**占位文本**「纯音乐，请欣赏」——必须过滤
- 合并翻译：按时间戳把翻译行插到原文行的下一行，播放器即可双语显示

### 网易云专辑图

GET `https://music.163.com/api/song/detail?id={sid}&ids=[{sid}]` → `songs[0].album.picUrl`

拼 `?param=1000y1000` 控制尺寸。**注意**：被限流时返回 `code:405` 且 `songs` 为空，
一定要先校验 `code`，否则会把限流当成「没封面」。

### QQ 音乐封面（不受网易限流影响）

1. 用 `musicu.fcg` + `DoSearchForQQMusicDesktop` 搜索，取 `album.mid`
2. 直连 `https://y.qq.com/music/photo_new/T002R500x500M000{album_mid}.jpg`

图在独立的图片服务器上，与 vkey 鉴权无关，匿名可取，是覆盖率最高的一路。
把 `R500x500` 改成 `R800x800` 可得更大图。

### lrclib.net（免费歌词库）

GET `https://lrclib.net/api/search?track_name={t}&artist_name={a}&duration={sec}`

返回数组，取 `syncedLyrics`（带时间轴）。`plainLyrics` 是纯文本，不要用。
建议带 `User-Agent`，无鉴权、基本不限流。

### iTunes Search API（封面兜底）

GET `https://itunes.apple.com/search?term={歌名 歌手}&entity=song&limit=5`

取 `results[].artworkUrl100`，把 `100x100bb` 替换成 `600x600bb` 即得高清图。
全球曲库、几乎不限流，冷门华语歌命中率低于 QQ，但外文歌很强。

## 交叉验证

两个平台都查得到的专辑，发行年份必须一致；不一致时用 WebSearch 再核实。特别冷门的歌（OST、活动曲、校歌等）直接 WebSearch 确认首发信息。

## mutagen 标签操作要点

```python
# 读取（自动识别格式）
from mutagen import File
f = File(path, easy=True)
f.get('title'), f.get('artist'), f.get('album')

# MP3 写标签（UTF-8）并清水印字段
from mutagen.id3 import ID3, TIT2, TPE1, TALB
tags = ID3(path)
tags['TIT2'] = TIT2(encoding=3, text=title)
tags['TPE1'] = TPE1(encoding=3, text=artist)
tags['TALB'] = TALB(encoding=3, text=album)
for k in list(tags.keys()):          # 下载站常污染的字段
    if k.startswith('COMM') or k in ('TCOM','TCON','TPE2'):
        del tags[k]
tags.save(path)                      # APIC 封面自动保留

# FLAC 写标签
from mutagen.flac import FLAC
f = FLAC(path); f['title']=title; f['artist']=artist; f['album']=album; f.save()

# FLAC 写歌词 + 封面（封面是 PICTURE 块，与 vorbis comment 分离，不会互相误删）
from mutagen.flac import Picture
f = FLAC(path)
f['LYRICS'] = lrc_text
f['UNSYNCEDLYRICS'] = lrc_text        # 兼容只认这个字段的播放器
pic = Picture(); pic.type = 3         # 3 = front cover
pic.mime = "image/jpeg"; pic.desc = ""; pic.data = img_bytes
f.clear_pictures(); f.add_picture(pic)
f.save()

# MP3 写歌词 + 封面
from mutagen.id3 import USLT, APIC
tags = ID3(path)
tags.delall("USLT"); tags.add(USLT(encoding=3, lang="chi", desc="", text=lrc_text))
tags.delall("APIC"); tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=img_bytes))
tags.save(path)

# M4A
from mutagen.mp4 import MP4, MP4Cover
m = MP4(path); m["\xa9lyr"] = [lrc_text]
m["covr"] = [MP4Cover(img_bytes, imageformat=MP4Cover.FORMAT_JPEG)]
m.save(path)
```

写 `.lrc` 伴生文件：UTF-8 无 BOM、`\n` 换行、与音频同名同目录
（`open(p, "w", encoding="utf-8", newline="\n")`）。个别老播放器会把 BOM 显示成歌词首行。

若无 mutagen：`pip3 install --user mutagen`。
