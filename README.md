# GooseSkills

个人 AI Agent Skills 合集（Agent Skills / SKILL.md 标准，适用于 WorkBuddy、Claude Code、Codex、OpenCode 等支持该标准的宿主）。

## 目录结构

```
GooseSkills/
├── music-suite/              # 音乐技能组（独立仓库，含总台路由 + 四个子技能）
│   ├── music-suite/          # 总台：意图路由与组合流程编排（无脚本）
│   ├── music-playlist-exporter/
│   ├── music-playlist-completer/
│   ├── music-library-organizer/
│   ├── fnos-playlist-creator/
│   └── README.md
├── obsidian-vault-manager/   # Obsidian 仓库管理：双链织网、摘要标签、MOC、断链检查
└── tools/                    # 工作数据（查重报告、删除计划等，不纳入版本管理）
```

## 技能速览

| 技能 | 说明 |
|---|---|
| [music-suite](music-suite/README.md) | 音乐工具总台：歌单获取 → 下载补全 → 音乐库整理 → 导入飞牛 NAS 的完整链路，含歌手全集下载、歌词封面补全 |
| [obsidian-vault-manager](obsidian-vault-manager/SKILL.md) | 管理 Obsidian 本地仓库：扫描结构、生成与修复双链、反链索引、摘要与标签、MOC 地图、孤立笔记与断链发现 |

## 安装

```bash
git clone https://github.com/GooseAgentAI/GooseSkills.git
# 单个技能
cp -r GooseSkills/obsidian-vault-manager ~/.workbuddy/skills/
# 音乐全家桶（推荐：带总台自动路由）—— 需另行获取 music-suite 仓库
cd GooseSkills/music-suite
cp -r music-suite music-playlist-exporter music-playlist-completer \
      music-library-organizer fnos-playlist-creator ~/.workbuddy/skills/
```

各技能独立可用；凭证（网易云/QQ cookie、飞牛账号）一律走环境变量或 `~/.config` 下的配置文件，不随仓库分发。

## 约定

- 批量修改类操作（改名 / 移动 / 写标签 / 删除）一律先试运行预览，确认后执行。
- 下载类任务版本校验优先于覆盖率：不下翻唱、现场、消音等错误版本；伴奏与可替代的 Live 版默认过滤。
- 所有工作数据统一放在本仓库目录下，产物不散落到其他路径。

## License

MIT。各技能目录内附 LICENSE 文件。
