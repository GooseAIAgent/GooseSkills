# 工作流参考：平台歌单获取（网易云/QQ → 本地 m3u8）

本文记录歌单获取侧的完整流程、API 细节与实战踩坑。下载补全侧见 music-playlist-completer 的 references/workflow.md。

## 一、全流程（首次导出）

1. **解析歌单 ID**：从用户给的链接中取 `id=` 参数（`uct2=` 等登录参数忽略）。
2. **抓歌单骨架**：`GET /api/v6/playlist/detail?id=<id>&n=1000`
   - `playlist.name` → 输出文件命名
   - `playlist.trackIds` → **全量**曲目 ID（`playlist.tracks` 只有前 6 首，别用）
   - 请求头带 `Referer: https://music.163.com/` 和浏览器 UA
3. **分批补详情**：`GET /api/song/detail/?ids=[...]`，100 个/批，批间 `sleep ≥1.5s`
   - 字段：`name`（歌名）、`artists[].name`（多歌手用「、」连接，与本地库命名约定一致）、`album.name`、`duration`（毫秒→秒）
4. **扫描本地库**：递归遍历 `--music-root`，收集音频扩展名（flac/mp3/m4a/wav/ape/ogg/opus/dsf/dff/aac/wma/aiff）；跳过 `.@__thumbnails`、`@eaDir`、`#recycle`、`.Trash` 等系统/回收目录。
5. **三层匹配**（见 SKILL.md），命中写相对路径，未命中留空。
6. **导出**：2 份 CSV（UTF-8 带 BOM）+ 2 份 m3u8（UTF-8 无 BOM）。
7. **校验**：命中数 == m3u8 非空路径行数；命中路径逐条 `os.path.exists`；报告给用户「命中/留空/待下载」三个数字。

## 二、离线重跑（本地新增了歌）

```bash
python3 netease_to_m3u8.py --csv 歌单完整清单.csv --name "歌单名" \
    --music-root /path/to/music --outdir ./歌单导出
```

- 不联网，秒级完成。
- **旧「本地文件」列会被清空全量重评**——因为匹配逻辑升级后旧命中可能被判定为误配（这正是想要的）。
- 歌单名从 CSV 文件名推不出来时必须给 `--name`。

## 三、回填留空条目（日后补歌）

```bash
python3 fill_playlist_paths.py 歌单.m3u8 --dry-run   # 先看能补几首
python3 fill_playlist_paths.py 歌单.m3u8             # 确认后写回
```

- 解析规则：`#EXTINF` 行 + 紧随其后的路径行构成一个条目；空行=留空条目，原样保留结构。
- 只处理留空条目和**已失效**（文件不存在）的本地路径；http(s) 链接不碰。
- 写回前无需备份，格式幂等；但建议先 `--dry-run` 人工过一遍匹配清单。

## 四、踩坑记录（勿重蹈）

| 坑 | 教训 |
|---|---|
| `tracks` 只有 6 首 | 用 `trackIds` 拿全量，再分批补详情 |
| 限流 `-462/-431` | 指数退避重试 + 批间 ≥1.5s；连续限流等 10 分钟 |
| 外链 65% 是废的 | 本地没有就留空，不写外链 |
| 歌名盲匹配张冠李戴 | 宽松层强制歌手集合校验（无交集拒绝）；「Angel」「Try」「I Do」这类通用歌名是重灾区 |
| Live/Remix 版本误配 | 严格层不拆括号；「夜曲」≠「夜曲(Live)」。歌单本身标了 Live 的条目，可经歌手兼容层命中录音室版（有意的取舍，宁可有歌听也不留假空白） |
| macOS 文件名 NFD | 一切字符串比较前 `unicodedata.normalize('NFC')`，韩文/中文必踩 |
| m3u8 带 BOM | 首行 `#EXTM3U` 识别失败，部分播放器整单拒读 |
| 合唱署名顺序 | 歌手比较用「token 集合」而非整串：「薛之谦、韩红」==「韩红、薛之谦」；「周杰伦、梁心颐」⊇「周杰伦(feat. 梁心颐)」 |
| 同文件被歌单重复引用 | `used_paths` 只占用一次，但完整版 m3u8 保留重复条目（忠实于原歌单）；本地可播版按文件去重 |

## 五、通用安全红线

1. **本地音乐库只读**：本技能不移动、不改名、不删除任何库内文件。
2. **输出先落盘到新目录**再替换旧文件；替换前向用户报数（命中/留空）。
3. **不索要账号凭证**：整套流程不需要 cookie；用户给的歌单打不开（私密歌单）就如实说明，不要诱导用户交出登录态。
4. **数字要自洽**：命中数、留空数、CSV 行数、m3u8 条目数必须能互相推导，交付前全部核对。

## 六、QQ 音乐歌单（qq_to_m3u8.py）

流程与网易云版完全一致（同一套三层匹配 + CSV/m3u8 导出，直接 import 复用），只换抓取层：

1. **解析 disstid**：支持 `y.qq.com/n/ryqq/playlist/{id}`、`/playsquare/{id}`、`i.y.qq.com/n2/m/share/details/taoge.html?id={id}`、纯数字。
2. **抓取**：`GET https://c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg`
   - 参数：`type=1&disstid=<id>&loginUin=0&hostUin=0&format=json&inCharset=utf8&outCharset=utf-8&notice=0&platform=yqq.json&needNewCode=0&g_tk=5381&begin=<offset>&num=100`
   - 请求头：`Referer: https://y.qq.com/`（带歌单页更好）+ 浏览器 UA；**公开歌单匿名即可，无需登录**。
   - 响应：`cdlist[0].dissname`（歌单名）、`total_song_num`（总数）、`songlist`（曲目，公开歌单通常一次回全量）。
   - 字段：`songname`、`singer[].name`、`albumname`、`interval`（已是秒）、`songmid`（歌曲链接 `https://y.qq.com/n/ryqq/songDetail/{songmid}`）。
   - **歌单名/歌名含 HTML 实体**（如 `&#10024;`），必须 `html.unescape`，否则文件名/CSV 里出现转义串。
3. **分页**：脚本按 `begin` 推进，直到 `len(rows) >= total_song_num` 或本页无新增（防死循环）；一次回全量时首屏即结束。
4. **限流**：code≠0 或网络错误指数退避重试（3s 起，上限 30s，共 4 次）。

踩坑记录：

- 新版 `u.y.qq.com/cgi-bin/musicu.fcg` 的 `musicPlaylist.playlistInfo` 模块，匿名请求返回 `code 500003`（要求登录态），**不要用**；fcg 接口匿名可用。
- fcg 接口对 `begin/num` 可能直接忽略并回全量（实测 28 首歌单 num=5 也回 28 首），分页逻辑必须以 `total_song_num` 为准收敛。
- 私密歌单（需登录可见）匿名抓不到，`cdlist` 为空——如实告知用户，不要硬试。

---
