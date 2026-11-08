# Music Playlist Completer（missing.json → 三级音源下载补全）

把歌单里缺失的歌曲批量下载补齐：**网易云按 ID 直下（~90%）→ 替代 ID 搜索 → QQ 音乐兜底**，FLAC 无损优先，版本严格校验，下载台账自动跳过已下载曲目。

纯 Python 标准库实现（mutagen 可选），实测 2544 首歌单补全后覆盖率 97.1%（FLAC 约 91%，67GB+）。

## 快速开始

```bash
# 凭证：先复制 config.example.json 到 ~/.config/music-downloader/config.json 填写
# ① 生成缺失清单
python3 scripts/build_missing_list.py --playlist 歌单.csv --lib /path/to/music --out ./music-downloads
# ② 网易云直下
python3 scripts/netease_dl.py --list ./music-downloads/missing.json --workers 4 --out ./music-downloads
# ③ 替代 ID 搜索（无音源曲目）
python3 scripts/netease_reid.py --list ./music-downloads/missing.json --out ./music-downloads
# ④ QQ 兜底（先验证权益）
python3 scripts/qq_verify.py
python3 scripts/qq_dl.py --list ./music-downloads/剩余缺失.json --workers 3 --out ./music-downloads
```

歌单抓取与本地比对见姊妹技能 **music-playlist-exporter**；同步到飞牛 NAS 见 **fnos-playlist-creator**。

详见 `SKILL.md`、`references/workflow.md` 与 `references/pitfalls.md`。
