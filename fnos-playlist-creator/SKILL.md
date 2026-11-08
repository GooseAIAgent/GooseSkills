---
name: fnos-playlist-creator
description: 自动创建飞牛 NAS（fnOS）音乐歌单。当用户要求把网易云歌单、QQ音乐歌单、本地 m3u8 导入/同步/搬到飞牛音乐，在飞牛里按歌单链接新建歌单时使用。匿名抓取云歌单全量曲目，与飞牛曲库三级匹配（路径精确/歌手歌名/歌名+时长校验），幂等写入（重跑只补缺不重复），服务端复核。触发词：飞牛、fnOS、NAS 音乐、网易云歌单导入飞牛、QQ歌单导入飞牛、飞牛建歌单、歌单同步到飞牛。
license: MIT
---

# fnOS Playlist Creator（网易云/QQ/m3u8 歌单 → 飞牛音乐）

## Overview

飞牛音乐官方**不支持导入任何歌单文件**。本技能走飞牛音乐网页端同源 API（`http://<NAS>:5666/music/api/v1`）+ 云歌单匿名抓取，一条命令完成：

**抓取网易云/QQ 歌单全量曲目（或读本地 m3u8）→ 与飞牛曲库三级匹配 → 创建/幂等复用歌单 → 批量写入 → 服务端复核**

实测记录（2026-11-08）：
- 本地 m3u8 2353 首 → **2353/2353**（路径精确匹配为主）
- QQ 歌单 28 首 → 命中 7/28（与本地库比对结果一致，其余本地没有），写入成功，重跑幂等 0 补充
- 网易云歌单 --limit 5 → 5/5

## 快速决策

| 用户需求 | 命令 |
|---|---|
| 网易云歌单 → 飞牛 | `fnos_playlist_creator.py --netease <ID或链接>` |
| QQ 音乐歌单 → 飞牛 | `fnos_playlist_creator.py --qq <disstid或链接>` |
| 本地 m3u8 → 飞牛 | `fnos_playlist_creator.py --m3u8 <文件>` |
| 先看匹配率不动 NAS | 任一命令加 `--dry-run`（生成未匹配清单 CSV） |
| 更新已有歌单 | 重跑同命令——幂等：已存在歌单只补齐缺失曲目 |

## 标准用法

```bash
# 凭证一律走环境变量（绝不写入文件/skill 目录）
export FNOS_SERVER=http://<NAS_IP>:5666
export FNOS_USER=<音乐账号>
export FNOS_PASS=<密码>

python3 $SKILL/scripts/fnos_playlist_creator.py --netease 331332108
python3 $SKILL/scripts/fnos_playlist_creator.py --qq "https://y.qq.com/n/ryqq/playlist/8888050823"
python3 $SKILL/scripts/fnos_playlist_creator.py --m3u8 歌单.m3u8 --name "自定义歌单名"
```

- 歌单名默认取源歌单名（QQ 名自动 html.unescape）；`--name` 覆盖
- `--limit N` 测试模式：只取前 N 首
- `--dedupe` 同一曲目只保留首次出现（源歌单重复收录时用），相对顺序不变；实测 2443 条 → 去重丢弃 90 条
- `--rebuild` 同名歌单已存在时**先删除再重建**——只在需要修顺序/彻底重刷时用，正常补歌不要加

## 匹配机制

1. **路径精确匹配**（仅 m3u8 且路径含 `/music/`）：本地路径与曲库 `audioSpec.path` 的相对片段 casefold 比较——最可靠，优先级最高
2. **歌手+歌名**：`norm(全部歌手 - 歌名)`，时长差 ≤3s
3. **主歌手+歌名**：`norm(第一歌手 - 歌名)`（合唱曲歌手顺序/人数不一致的兜底）
4. **歌名+时长**：`norm(歌名)`，时长差 ≤1s（防同名不同版本）
5. **标题互相包含+时长 ≤2s**：标题被影视剧/品牌后缀包裹时用（如曲库 `《小空》(茶小空二周年品牌歌)` vs 歌单 `小空`）；**版本标记必须一致**，拒绝「录音室 ↔ Live/伴奏/翻唱」跨版本误配
6. 均未命中 → 写入 `<歌单名>-飞牛未匹配.csv`（含歌名/歌手/专辑/时长/链接），供人工处理或走 music-playlist-completer 下载补全后再同步

所有键统一 `unicodedata.normalize("NFC")` + 标点归零——**macOS NFD vs NAS NFC 的文件名差异靠这层抹平（实测 +74 首命中）**。

## 幂等与安全

- 重跑同命令：读取歌单现有曲目（`size` 上限 100，必须分页），只添加缺失的；不删除、不重排、不重复
- 中途变更过顺序（如旧版脚本写入的倒序歌单）→ 加 `--rebuild` 重建
- 凭证仅环境变量/命令行参数；密码传输层 sha256；**任何情况下不写入文件、日志、skill 目录**
- 写入速率：add-track 200/批、批间 0.3s；2351 首全流程约 40 秒

## 铁律

1. **局域网请求必须绕过代理**。`HTTP(S)_PROXY` 会让请求返回 `502 upstream connect failed`，症状与"NAS 崩溃"一模一样——先 `curl --noproxy '*'` 验证再下结论。脚本内部已强制 `--noproxy`。
2. **必须用 curl 子进程发请求**，不要改回 urllib/requests 直连——macOS「本地网络」TCC 权限会拦截 python 进程（Errno 65 No route to host），curl 不受限。
3. **匹配键必须 NFC 归一化**（见上）。
4. **正式写入前先 `--dry-run`**；未匹配率异常高时先确认 NAS 曲库是否已扫描到对应文件（新下载的歌需要先在飞牛里「扫描媒体库」，否则匹配不到——实测扫描后 8 首未匹配降到 2 首）。
5. **写完看服务端复核数字**（脚本自动打印 `data.total`），应等于写入数。
6. **`add-track` 是批内倒序落库**（实测：一批发 `A B C D E`，列表回 `E D C B A`；跨批仍按批先后追加）。脚本已对每批 `reversed()` 后再发，**不要"优化"回去**，否则 200 首一批的歌单顺序会碎成 12 段倒序。
7. 云歌单抓取的 API 细节（网易 trackIds+song/detail 分批、QQ fcg 接口参数、私密歌单限制）见 `references/api.md`。

## 依赖

仅 python3 标准库 + 系统 `curl`。飞牛音乐需在 NAS 上正常运行；音乐独立账号可用。

## References

- `references/api.md` — 飞牛音乐 API + 网易云/QQ 抓取接口的完整清单与踩坑记录
