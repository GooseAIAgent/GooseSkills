# 飞牛音乐（fnOS Music）API 参考

> 来源：社区第三方客户端逆向（kuilei0926/FeiNiuMusic、qianlipp/fn-music-bridge、saaak/fnos-music-importer）+ 本机实测（2026-11-08，fnOS 音乐 v1.x）。协议非官方公开，字段可能随版本变化，遇到 100002 invalid arguments 先怀疑参数名。

## 云歌单抓取（输入侧）

### 网易云歌单

1. `GET https://music.163.com/api/v6/playlist/detail?id=<pid>&n=1000`（UA + `Referer: https://music.163.com/`，匿名可用）
   - `playlist.name` 歌单名；`playlist.trackIds[].id` 是**全量**曲目 ID（`tracks` 字段只回前若干首，不要用）
2. 分批补详情：`GET https://music.163.com/api/song/detail/?ids=<URL编码的JSON数组>`，100 个/批，批间 1.5s 防限流
   - 字段：`name`、`artists[].name`、`album.name`、`duration`（毫秒）
3. 链接参数只认 `id=` 数字；私密歌单匿名抓不到，如实告知。

### QQ 音乐歌单

`GET https://c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg?type=1&disstid=<id>&loginUin=0&hostUin=0&format=json&inCharset=utf8&outCharset=utf-8&notice=0&platform=yqq.json&needNewCode=0&g_tk=5381&begin=<offset>&num=100`
（UA + `Referer: https://y.qq.com/`，公开歌单匿名可用，通常一次回全量）

- `cdlist[0].dissname` 歌单名、`total_song_num` 总数、`songlist[]`
- 字段：`songname`、`singer[].name`、`albumname`、`interval`（**已是秒**）、`songmid`
- **歌单名/歌名含 HTML 实体**（如 `&#10024;`），必须 `html.unescape`
- 分页以 `total_song_num` 收敛 + 本页无新增断路（fcg 可能忽略 begin/num 直接回全量）
- 新版 `musicu.fcg` 的 `musicPlaylist.playlistInfo` 匿名返回 500003（要求登录态），不要用
- 私密歌单 `cdlist` 为空——如实告知，不要硬试

## 基础

- 网关：`http://<NAS地址>:5666/music/api/v1`（与网页端桌面同源）
- 响应统一包装：`{ "code": 0, "msg": "", "data": {...} }`
  - `code=0` 成功；`120001` token 失效/账号密码错误；`100002` 参数名或参数错；`100001` 未知错误（登录体字段不齐）
- 认证：登录拿 `userToken`（32 位 hex），后续所有请求带 **`Cookie: music-token=<userToken>`**
- 连通性探测（无需登录）：`GET /initialization/state` → `{"initialized": true}`

## 登录

```
POST /user/password-login
{"username": "...", "password": "<sha256(password) 64位hex>", "deviceId": "<32位hex随机>"}
```

- `deviceId` 缺失会返回 100001 unknown error（实测踩坑）
- 响应：`data.userToken` + `data.user{guid, name, role}`

## 曲库

```
GET /track/list?page=1&size=500
```

- 返回 `data.list[]` + `data.total`；size=500 实测可用（客户端默认 30）
- 单条曲目关键字段：
  - `guid` — 曲目唯一 ID（写入歌单用）
  - `title`、`artists[{name,guid}]`、`album{name,guid}`
  - `duration` — **毫秒**
  - `audioSpec.path` — **NAS 端真实绝对路径**（如 `/vol02/1000-0-xxxx/music/IU/IU - 좋은 날.flac`），路径匹配的主数据来源
  - `audioSpec{codec,bitrate,bitDepth,sampleRate,size}`

## 歌单

| 端点 | 方法 | 说明 |
|---|---|---|
| `/playlist/list` | GET | 歌单列表 `data.list[]{name, guid, ...}`（无曲目数、无顺序字段） |
| `/playlist/create` | POST | `{"name": "..."}`（coverId 可省）；返回 `data.guid` |
| `/playlist/delete` | POST | `{"guid": "..."}` → `{"code":0}`（实测可用，删除后歌单立刻从列表消失） |
| `/playlist/add-track` | POST | `{"guid": "<歌单guid>", "trackGUIDs": ["...", ...]}`，批量 |
| `/track/playlist-detail/list` | GET | **参数名是 `playlistGUID`**（不是 guid！）+ `page`/`size`；返回 `data.list[]` + `data.total` |

⚠️ 踩坑：`/track/playlist-detail/list?guid=...` 返回 `100002 invalid arguments`。正确参数：`playlistGUID`。

### 曲目顺序（重要）

1. **`add-track` 是「批内倒序」落库**：一批发 `[A,B,C,D,E]`，`playlist-detail/list` 回 `[E,D,C,B,A]`；跨批仍按批的先后追加（`[批2倒序][批1倒序]` 的排列是错的——实测是 `[批1倒序][批2倒序]…`）。
   → 想让歌单按源顺序排列，**必须把每批 `trackGUIDs` 反转后再发**。2026-11-08 实测：正序发 2351 首得 12 段倒序；反转后逐首比对 `顺序完全一致: True`。
2. **`playlist-detail/list` 的 `size` 上限是 100**：传 `size=10000` 只回 50 条、`size=500` 回 500 条但可能被中间件截断。取全量必须分页（`playlist_guids()` 已实现）。
3. 服务端**不暴露顺序字段**（无 position/seq/sort），顺序完全由落库顺序决定，且没有 reorder 接口（前端 JS 里的 `reorder` 是视频解码器字段）——一旦写错只能 `--rebuild` 删除重建。

## 本机环境踩坑（重要）

1. **HTTP 代理劫持**：会话/系统若设置 `HTTP_PROXY`/`HTTPS_PROXY`（如指向 127.0.0.1 的出站代理），访问局域网 IP 会被代理拒绝，返回 `502 upstream connect failed: No route to host (os error 65)`。**症状与 NAS 服务崩溃完全一样**——先 `curl --noproxy '*' -v` 直连验证。脚本内部已强制绕过代理。
2. **macOS 本地网络 TCC 权限**：部分 python 发行版（如 ~/.workbuddy/binaries 下的 python）发起局域网连接直接 `Errno 65 No route to host`，同机 curl 正常（系统二进制有权限）。解决方案二选一：
   - 脚本用 **curl 子进程**发请求（当前实现，零依赖）
   - 系统设置里给对应 python 授予「本地网络」权限（不可控，不推荐依赖）
3. **文件名 Unicode 归一化**：macOS（HFS+/APFS/SMB 客户端）文件名为 NFD 分解形式，NAS 端为 NFC。比较路径前必须 `unicodedata.normalize("NFC", s)`。实测：不做归一化 2279/2353，做了 2353/2353。

## 写入建议

- add-track 每批 ≤200 条，批间 0.3s；2353 首一次性全量写入成功。
- 幂等：先拉 `/track/playlist-detail/list` 得到已有曲目 guid 集合，过滤后再 add。
- 曲库翻页间 0.2s；3216 首 7 页拉完约 5 秒。

## 官方能力现状（2026-11）

- 官方不支持导入 m3u/m3u8/PLS 歌单文件（论坛功能征集帖在排队）
- 官方移动 App 支持歌单读建、加删歌（同一套 API）
- 第三方工具可参考：saaak/fnos-music-importer（油猴脚本，网页端内注入）、qianlipp/fn-music-bridge（协议桥接为 Subsonic/Jellyfin 等）
