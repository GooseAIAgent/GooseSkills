# 🎵 music-library-organizer（中文音乐库管理）

> 让 AI 助手真正会整理音乐库的 Agent Skill —— 在数千首真实 FLAC/MP3（中/韩/英/日多语言）上实战验证。

[English](#english) | 简体中文

---

## 这是什么

一个符合 **Agent Skills 开放标准**（SKILL.md）的技能包，让 Claude Code、OpenAI Codex CLI、OpenCode、WorkBuddy 等 AI 编程助手获得一套**经过实战验证的本地音乐库整理能力**：

- 📝 **统一文件名** —— 全库改为「歌手 - 歌名」格式（多歌手「歌手A、歌手B - 歌名」），自动修正「歌名-歌手」倒序、无空格连字符等混乱命名
- 💿 **智能分类归档** —— 逐首查询歌曲的真实专辑归属与发行年份，按「年份-专辑名」或「年份」分类存放（支持"正规专辑 >5 首才独立建夹、其余进合辑"等自定义规则）
- 🏷️ **修复内嵌标签** —— 播放器显示 `ifulicn.com-ifulicn.com` 这类下载站水印？从干净的文件名反写标题/艺术家/专辑，清除被污染的 7 个 ID3 字段，保留封面
- 🌐 **外语歌名「原名（中文译名）」** —— 韩语/日语等歌曲统一命名为 `IU - 분홍신（粉红色高跟鞋）`：原名与译名都取自平台字段，保留 `(Live)`、`(Inst.)` 等版本后缀，匹配不上就保持原样，绝不自行翻译
- 🎤 **批量补歌词 + 封面** —— 自动抓 LRC 歌词（含中文翻译合并）与专辑封面，**同时写进音频标签和同名 `.lrc`**，换播放器、换 NAS 都不丢
- 🧹 **批量修正后缀** —— 删除文件名尾部的数字串、孤立连字符等下载残留
- 🔍 **只读查重** —— 四层判定（字节 SHA-256 → 音频流 MD5 → 同名同长 → 同名近长），分级输出「可安全删 / 同曲多份 / 不同版本勿删」，绝不自动删文件

## 为什么不自己写脚本就够了

这套技能沉淀的是**踩坑经验**，每个坑都真实发生过：

| 坑 | 后果 | 技能中的对策 |
|---|---|---|
| NAS 卷宗 `shutil.move` 偶发"目标已写入但源未删" | 静默产生重复文件 | 强制 `/bin/mv` + 移动后 MD5 校验，幻影副本自动隔离 |
| macOS 文件名 NFC/NFD 规范化差异 | 韩文/中文匹配莫名失败 | 所有匹配前强制 NFC 归一化 |
| 网易云 API 间歇性 `-462` 验证码限流 | 批量查询中途崩溃 | 自动退避重试 + QQ 音乐备用数据源 + 交叉验证 |
| 播放器显示乱码但文件名是好的 | 误判为文件名问题 | 明确"播放器读内嵌标签不读文件名"的诊断思路 |
| 批量改名遇到重名冲突 | 文件被互相覆盖 | 冲突预判 + 自动 " (2)" 后缀 + 未登记文件报错拦截 |
| 删除不可恢复（卷宗无废纸篓） | 数据损失 | 删除前 MD5 举证 + 用户确认 + 空文件夹才可 rmdir |
| 网易云批量取歌词/封面后返回 `405` | 这是 IP 级限流，不是"没这首歌"，一次误判能废掉几百首 | 限速 + 连续 3 次熔断，熔断期封面转 QQ/iTunes、歌词转 lrclib，只为"真拿到空字段"记无源 |
| 纯音乐返回「纯音乐，请欣赏」占位文本 | 被当成歌词写进标签，整库一堆假歌词 | 正则识别并过滤，视作无歌词继续兜底 |
| 同名不同版本（原版/Live/DJ）配错歌词 | 歌词与音频错位 | 歌名全等 + 歌手有交集 + **时长差 ≤2s** 三重校验 |
| 补完 `.lrc` 但 NAS 音乐服务不显示 | 以为脚本没生效 | 服务只在首次入库扫描时读取，须重扫媒体库；新歌应在入库前补齐 |
| 外语歌名按「时长最近」匹配原名 | 配成完全不相干的曲子（「蜡笔」曾配到别的歌） | **语义优先**（中文名出现在平台 `title` 中）+ 歌手交集 + 时长三重校验 |
| 只改文件名不改 `title` 标签 | 播放器与歌词匹配仍读旧中文名，等于白改 | 改名同时写 `title`，`.lrc` 同步改名 |
| macOS 把韩文存成 NFD（Jamo 分解） | 用 `\uAC00-\uD7A3` 检测为 0，误判"没改成" | 所有判定前 `unicodedata.normalize("NFC")` |

## 安装

把整个 `music-library-organizer/` 文件夹复制到所用助手的技能目录即可：

| 助手 | 用户级技能目录 |
|---|---|
| OpenAI Codex CLI | `~/.agents/skills/` |
| Claude Code | `~/.claude/skills/` |
| OpenCode | `~/.config/opencode/skills/`（或项目内 `.opencode/skill/`） |
| WorkBuddy | `~/.workbuddy/skills/` |

```bash
git clone https://github.com/<你的用户名>/music-library-organizer.git
mkdir -p ~/.agents/skills
cp -R music-library-organizer ~/.agents/skills/
```

安装后在助手中输入 `/skills`（Codex）或直接说「帮我整理音乐库」即可触发；各助手的具体技能目录以其文档为准。

**没有 AI 助手也能用** —— 四个脚本都是独立的 Python 3 程序，可直接命令行使用（见下文）。

## 依赖

- Python 3.8+（脚本仅用标准库）
- [mutagen](https://mutagen.readthedocs.io/)（标签读写/歌词封面封装需要）：`pip3 install --user mutagen`
- 网络访问（查询专辑归属、抓歌词与封面时需要：QQ 音乐 / 网易云公开接口 / lrclib.net / iTunes）

## 快速开始（直接用脚本）

```bash
S=path/to/music-library-organizer/scripts

# 0. 所有脚本均支持 --dry-run 试运行，先看计划再执行

# 1. 统一改名「歌手 - 歌名」（多歌手自动转「、」，.lrc 歌词与 .cover 封面同步改名）
python3 $S/rename_songs.py ~/Music/我的歌手 --dry-run
python3 $S/rename_songs.py ~/Music/我的歌手            # 加 --tags 可以内嵌标签为准判定顺序

# 2. 修复播放器显示异常（下载站水印标签）：从文件名反写，清水印，保留封面
python3 $S/fix_tags.py ~/Music/我的歌手 --check        # --check 只检查不修改
python3 $S/fix_tags.py ~/Music/我的歌手

# 3. 查询歌曲专辑归属（QQ音乐为主，网易云备用）
python3 $S/music_api.py search "薛之谦 演员"           # 搜索：专辑/歌手/发行日期
python3 $S/music_api.py album 003j9ta912S5XX           # 专辑详情：曲目数/发行日
python3 $S/music_api.py netease-artist 毛不易           # 网易云歌手ID
python3 $S/music_api.py netease-albums 13159024         # 网易云歌手全部专辑+日期

# 4. 按映射表分类移动（未登记文件报错拦截；MD5 防幻影副本）
cat > map.json << 'EOF'
{
  "毛不易 - 消愁.flac": "2018-平凡的一天",
  "毛不易 - 入海.flac": "2020-合辑"
}
EOF
python3 $S/organize_music.py ~/Music/毛不易 map.json --dry-run
python3 $S/organize_music.py ~/Music/毛不易 map.json

# 5. 补歌词 + 封面（先扫状态，再小批试跑，最后全量；幂等可重跑）
python3 $S/scan_tag_state.py ~/Music
python3 $S/lyrics_cover_embed.py --lib ~/Music --dry-run
python3 $S/lyrics_cover_embed.py --lib ~/Music --limit 20 --workers 3   # 先验证 20 首
python3 $S/lyrics_cover_embed.py --lib ~/Music --workers 4              # 确认无误再全量

# 6. 外语歌名统一为「原名（中文译名）」（先扫候选，再预览，最后执行；幂等）
python3 $S/native_title_rename.py ~/Music --scan-only          # 本地候选，不联网
python3 $S/native_title_rename.py ~/Music --artist IU          # 预览该歌手完整清单
python3 $S/native_title_rename.py ~/Music --artist IU --apply  # 执行（文件名 + .lrc + title）
```

## 技能结构

```
music-library-organizer/
├── SKILL.md                    # 技能入口：决策表 + 脚本用法 + 安全铁律
├── README.md
├── LICENSE
├── config.example.json         # 可选凭证模板（网易云 cookie / QQ 凭证）
├── scripts/
│   ├── music_api.py            # QQ音乐/网易云查询库（搜索、专辑详情、歌手专辑）
│   ├── rename_songs.py         # 统一「歌手 - 歌名」改名（含伴生文件同步）
│   ├── fix_tags.py             # 标签修复：文件名反写、清水印、保封面
│   ├── album_tags.py           # 批量写专辑标签：查平台补 album+date（限流退避、断点续跑）
│   ├── organize_music.py       # 按映射表分类移动（MD5 防幻影、防遗漏）
│   ├── fill_playlist_paths.py  # 歌单路径补全：留空条目按本地库回填（可重跑）
│   ├── scan_tag_state.py       # 扫描缺歌词/缺封面（增量、断点续扫）
│   ├── lyrics_cover_embed.py   # 批量补歌词+封面并封装进标签（含 .lrc 落盘、多源回退）
│   ├── native_title_rename.py  # 外语歌名「原名（中文译名）」：平台取原名、语义匹配、同步标签
│   └── find_duplicates.py      # 只读查重：字节→音频流→同名，分级报告（含现场版识别）
└── references/
    ├── workflow.md             # 七大任务完整流程 + 通用安全红线 + 边界判定
    └── api_reference.md        # 平台 API 细节（含 QQ name/title 字段）+ mutagen 操作要点
```

## 典型工作流：按「年份-专辑名」分类

1. **读内嵌标签**（mutagen）获取 album/date —— 可信度最高
2. **逐首查平台**：QQ 音乐搜索拿专辑与 `timePublic` 发行日期（批量时脚本化，间隔 ≥1.2s 防限流）
3. **拉专辑曲目数**，按规则判定：如"官方曲目 >5 首的正规录音室专辑 → `年份-专辑名`；单曲/EP/OST/综艺 Live/群星合辑 → `年份-合辑`"
4. **生成映射表 JSON → `--dry-run` 试运行 → 人工核对 → 执行 → 校验总数**

完整的边界判定原则（精编版归属、同歌多版本、原创首发优先等）见 [`references/workflow.md`](references/workflow.md)。

## 典型工作流：歌单（m3u8）留空待补

1. 从平台导出歌单时，本地已具备的曲目写入**绝对路径**，本地暂无的曲目**只保留 `#EXTINF` 标题、路径行留空**（不写平台外链，避免放进播放器后大量失效）
2. 日后把新歌下载进本地库，重新归档命名
3. 跑 `fill_playlist_paths.py 歌单.m3u8`，按「歌手 - 歌名」三层匹配自动回填，未命中的继续留空，可反复重跑

## 典型工作流：纯年份目录补全专辑标签

适用于「目录只有年份、内嵌标签整片为空」的库（`fix_tags.py` 从文件夹取专辑，这种结构取不到）：

1. **摸现状**：统计标签为空的首数、已有 album 的首数
2. **试运行**：`album_tags.py /path/to/歌手 --dry-run`，先看能写多少、缺多少
3. **联网补全**：逐首查 QQ 音乐，结果落 JSON 缓存，限流中断可断点续跑
4. **抽查异常**：重点看「专辑名 == 歌手名」「XX的电台」这类合辑型结果，多半配错了正主单曲
5. **写入复检**：全量扫一遍，确认无空值、无水印、title/artist 与文件名一致

三个必须绕开的坑（详见 workflow.md 任务六）：**空结果＝限流不是查不到**、
**排序必须优先「专辑名==歌名」**、**带版本标记的曲目不要去掉括号再比对**。

## 典型工作流：补歌词 + 封面

适用于「文件很干净但播放器里一片空白」的库，也可以给 NAS 音乐服务（飞牛音乐、Navidrome、Jellyfin）准备歌词：

1. **扫状态**：`scan_tag_state.py ~/Music`，先看「共多少首、缺多少歌词/封面」
2. **试跑**：`lyrics_cover_embed.py --lib ~/Music --dry-run` 看计划；再 `--limit 20` 实跑并抽检
3. **全量**：`--workers 4`，数千首约 0.5–2 小时，中断可续跑（状态文件 + 日志双重幂等）
4. **补第二遍**：剩下的「无源」多是纯音乐/冷门曲；中文译名对不上韩文原名时建 `alias.json` 再跑
5. **重扫媒体库**：若给 NAS 用，务必在服务端重新扫描——它只在首次入库时读歌词

写入策略：**内嵌标签 + 同名 `.lrc` 双写**。
歌词来自网易云（自动合并中文翻译）→ lrclib.net；封面来自 `.cover/` → 网易云 → QQ 音乐 → iTunes。
所有匹配都要求「歌名全等 + 歌手有交集 + 时长差 ≤2 秒」，宁缺勿错。

## 典型工作流：外语歌名统一为「原名（中文译名）」

韩语、日语歌被存成中文译名（`IU - 粉红色高跟鞋`）时，播放器看不出原曲名，歌词接口也常因此匹配不上。统一改成：

```
IU - 분홍신（粉红色高跟鞋）
IU - 크레파스（蜡笔） (电视剧《漂亮男人》插曲)
```

1. **看量**：`native_title_rename.py ~/Music --scan-only`（不联网，秒出候选数）
2. **预览**：`--artist IU` 或直接跑全库（dry-run 默认），逐条检查 `high` / `medium`
3. **执行**：`--apply`，同步改文件名、同名 `.lrc` 和 `title` 标签
4. **兜底**：平台查不到的写进 `ov.json`（`{"IU|毕业日": {"native":"졸업하는 날","zh":"毕业日"}}`），`--overrides ov.json` 重跑
5. **收尾**：`rename-record.csv` 里 `已执行` / `保持原样` 一目了然，可回滚

原则：**原名必须来自平台字段，不自己翻译**；`(Live)`、`(Inst.)`、`(Acoustic Ver.)` 等后缀原样保留；中文翻唱保持原样。

## 安全设计


- ✅ 所有批量操作先 `--dry-run` 出完整计划清单
- ✅ 映射表驱动：未登记文件**报错停下**而非静默跳过
- ✅ 移动后 MD5 校验，幻影副本自动隔离到「重复文件待核对」
- ✅ 删除操作永远需要用户确认（默认只移动不删除）
- ✅ 专辑数据全部可溯源（平台 API / 网络搜索），禁止编造

## 免责声明

- `music_api.py` 调用的是 QQ 音乐与网易云音乐**未公开文档化的公开 Web 接口**，仅供个人学习研究，可能随平台更新而失效；请控制请求频率（脚本已内置间隔与退避重试），勿用于大规模抓取。
- 歌词与封面版权归原权利人所有；本工具仅为你已合法持有的本地音频补齐元数据，请勿用于分发。
- 本工具只做文件重命名/移动/标签修复，不做任何音频转码或内容修改；尽管如此，操作媒体库前仍建议自行备份。

## 致谢

工作流沉淀自对多个真实音乐库（海来阿木、IU、毛不易、薛之谦、周杰伦、田馥甄等，共数千首）的整理实践。

## License

[MIT](LICENSE)

---

<a name="english"></a>
## English

An [Agent Skills](https://agentskills.io) (SKILL.md) package that gives AI coding assistants (Claude Code, Codex CLI, OpenCode, etc.) battle-tested local music library organization capabilities:

- **Normalize filenames** to `Artist - Title` (multi-artist: `Artist A、Artist B - Title`), fixing reversed order and missing-space hyphens
- **Classify by album/year** — queries real album attribution & release dates per track via QQ Music / NetEase public APIs, then organizes as `Year-Album` or `Year` folders
- **Fix embedded tags** — rewrite title/artist/album from clean filenames, strip download-site watermarks (e.g. `ifulicn.com`), preserve cover art
- **Foreign titles as `Native (Chinese translation)`** — Korean/Japanese tracks are renamed to `IU - 분홍신（粉红色高跟鞋）`, with both the native title and the translation taken from platform fields (never machine-translated), version suffixes such as `(Live)`/`(Inst.)` preserved, and unmatched tracks left untouched
- **Batch lyrics & cover art** — fetch LRC lyrics (with Chinese translation merged) and album covers, writing them **both into tags and as sibling `.lrc` files**, so nothing is lost when switching players or NAS servers
- **Batch-suffix cleanup** — remove trailing numeric IDs, stray hyphens, etc.
- **Read-only duplicate scan** — four-level matching (SHA-256 → audio-stream MD5 → same title & length → near length), graded into *safe to delete / same song multiple copies / different versions — keep*, never deletes anything by itself

Scripts are standalone Python 3 programs (stdlib only; `mutagen` needed for tag editing) and work without any AI assistant. All batch operations support `--dry-run`, are mapping-driven (unregistered files abort instead of being skipped), and verify MD5 after moves to guard against NAS "ghost copy" issues.

See [`SKILL.md`](SKILL.md) for the skill entry point and [`references/workflow.md`](references/workflow.md) for detailed workflows.

> ⚠️ The music APIs used are undocumented public endpoints for personal study only; they may change without notice. Please keep request rates low.
