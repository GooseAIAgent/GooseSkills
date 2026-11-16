# Music Suite

**一站式本地音乐库管理技能** —— 一个 Skill，四大能力。

遵循 [`SKILL.md`](SKILL.md) 开放标准，适用于 WorkBuddy、Claude Code、Codex、OpenCode 等所有支持该标准的 AI Agent 宿主。安装后只显示**一个**技能，却拥有完整功能。

## 它能做什么

| 模块 | 能力 | 典型场景 |
|---|---|---|
| **歌单导出** | 网易云 / QQ 歌单全量抓取（无需登录）、与本地库比对、导出 CSV + m3u8 | “看看这个歌单我本地缺哪些歌” |
| **下载补全** | 三级音源下载 + 多源兜底、FLAC 优先、版本严格校验、台账幂等 | “把缺的歌下载下来”“下载某歌手全部歌曲” |
| **音乐库整理** | 批量重命名、修标签、补歌词封面、按年份-专辑归档、查重 | “整理一下我这几千首歌”“播放器显示乱码” |
| **导入飞牛 NAS** | 歌单同步进飞牛音乐，三级匹配 + 幂等写入 + 服务端复核 | “把这个歌单导入飞牛 NAS” |

四个模块可单独使用，也能串成一条龙：**抓歌单 → 下载补齐 → 整理归档 → 导入 NAS**。

## 设计亮点

- **版本正确性 > 覆盖率**：自动过滤伴奏 / 纯音乐 / 有录音室版时的 Live 版；拿不准的版本绝不硬凑。
- **幂等可重跑**：统一操作台账（按文件指纹判失效），下次只处理新增 / 变动的文件。
- **数据安全**：批量删除 / 移动 / 改名全部要求先 `--dry-run` 预览；本地库只读与整理操作严格分离。
- **凭证零入库**：所有 cookie / 账号走环境变量或 `~/.config`，绝不写进技能目录。

## 安装

把本目录整体复制到你所用宿主的 skills 目录即可（只需这一个目录）：

```bash
# WorkBuddy
cp -r music-suite ~/.workbuddy/skills/
# Claude Code
cp -r music-suite ~/.claude/skills/
# Codex / OpenCode 等：复制到对应的 skills 目录
```

安装后在技能列表里只会看到一个 **music-suite**，直接用自然语言提需求即可，技能会自动路由到对应模块。

## 依赖

- Python 3.8+（脚本仅用标准库）
- `mutagen`：写音频标签 / 歌词 / 封面必需 —— `pip3 install --user mutagen`
- `curl`：飞牛 NAS 模块必需（系统自带）
- `ffmpeg`：部分音源兜底与查重脚本可选

## 配置（可选）

匿名即可工作。若要提高歌词 / 封面命中率或下载 VIP 曲目，把 [`config.example.json`](config.example.json) 复制到：

- 下载补全：`~/.config/music-downloader/config.json`
- 音乐库整理：`~/.config/music-library-organizer/config.json`

填入网易云 / QQ 凭证。**凭证文件绝不要提交到 Git。**

飞牛 NAS 模块用环境变量：

```bash
export FNOS_SERVER=http://<NAS_IP>:5666
export FNOS_USER=<音乐账号>
export FNOS_PASS=<密码>
```

## 目录结构

```
music-suite/
├── SKILL.md              # 唯一入口：路由决策 + 命令速查 + 安全红线
├── config.example.json   # 凭证模板
├── scripts/              # 25 个脚本（四个模块平铺，共享台账只保留一份）
└── references/           # 详细文档，按模块分组
    ├── exporter/         # 歌单导出流程与 API
    ├── completer/        # 下载补全、踩坑手册、歌手全集
    ├── organizer/        # 整理流程与平台 API
    └── fnos/             # 飞牛音乐 API
```

## License

[MIT](LICENSE)
