# Music Playlist Exporter（网易云/QQ 歌单 → 本地 m3u8）

把平台歌单变成可用的本地歌单：**全量抓取（无需登录）→ 与本地音乐库三层比对 → 导出清单 CSV 和双 m3u8（缺失留空待补）→ 日后回填**。

纯 Python 标准库实现，适用于任何支持 [Agent Skills](https://agentskills.io) 标准的 AI 助手，也可直接当命令行工具用。

## 快速开始

```bash
# 网易云歌单：抓取 + 比对 + CSV + 双 m3u8
python3 scripts/netease_to_m3u8.py "https://music.163.com/playlist?id=你的歌单ID" \
    --music-root /path/to/你的音乐库 --outdir ./歌单导出

# QQ 音乐歌单（公开歌单无需登录）
python3 scripts/qq_to_m3u8.py "https://y.qq.com/n/ryqq/playlist/你的歌单ID" \
    --music-root /path/to/你的音乐库 --outdir ./歌单导出

# 日后补齐了歌，回填留空路径
python3 scripts/fill_playlist_paths.py "歌单导出/歌单名-完整N首.m3u8"
```

缺失歌曲的下载补全见姊妹技能 **music-playlist-completer**；同步到飞牛 NAS 见 **fnos-playlist-creator**。

## 实测数据

2544 首网易云收藏歌单 × 944 本地音频：命中 91（含歌手一致性校验，修正了盲匹配约 40 处张冠李戴）；输出 91 条路径全部实存。QQ 公开歌单 8888050823（28 首）全流程通过。

详见 `SKILL.md` 与 `references/workflow.md`。
