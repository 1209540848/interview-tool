# interview-tool

面试/测评实时辅助工具（个人备份仓库）。会话制面试助手 + 在线测评/笔试识图快答，双版本（quiz 测评 / code 笔试）共享一套功能域包。

## 提PR

https://github.com/any86/Notes/issues/22

## 架构

2026-09 模块化重构：两个功能等价的旧单体（quiz 版 / code 版，已从工作区移除，原名见 git 历史）收敛为 **功能域包 + 两个薄入口**：

```
interview-tool/
├─ run_quiz.py / run_code.py   # 薄入口：engine.main(profiles.QUIZ/CODE)
├─ interview_tool/
│  ├─ profiles.py              # 双场景差异收容所（提示词/键位/窗口/ESC 策略原文）
│  ├─ engine.py                # 统一编排主本（由 tools/gen_engine.py 从旧 code 版 main 生成）
│  ├─ config.py log.py state.py dsp.py audio.py asr.py chat.py
│  ├─ winfx.py vision.py typing_quiz.py typing_code.py push.py ui.py
│  ├─ prompt_presets.py prompt_store.py prompt_settings.py  # 单选技术场景
├─ notes/                      # code 版只读笔记专区（支持多个 Markdown 和子目录）
├─ interview-tool-api.py      # 遗留单文件 API 版（独立演进）
├─ hidden-start-*.vbs          # 静默启动器（指向 run_*.py / api）
└─ tools/                      # parity.py（按符号对比门禁）/ gen_engine.py / gen_profiles.py
```

场景差异（提示词/键位/窗口/ESC 退出节奏）一律收容在 `profiles.py` 的 `QUIZ`/`CODE` 两个 Profile，主本零场景判断。包内模块各司一职，多人协作改各自的文件，冲突面小。

## 文件说明

| 文件 | 版本 | 用途 |
|---|---|---|
| `run_quiz.py` | 测评版入口 | 行测/性格测评/选择题：`P`/`F3` 截屏走「测评快答」模板（直接给选项+一句话理由），`max_tokens` 更小、只求快；ESC 单按即退 |
| `run_code.py` | 笔试版入口 | 手撕代码/算法题：`Alt+P` 截屏识图走「详解」模板（思路/代码/复杂度），`Alt+1` 自动打字进答题框、`Alt+2` 粘贴、`Alt+3` 清识图多轮记忆；ESC 0.8s 内双按才退 |
| `interview-tool-api.py` | 祖本 | 通用分支（不再直接跑），功能与 quiz 版相当（P/F3 识图，无 Alt+ 方案） |
| `hidden-start-quiz.vbs` / `hidden-start-code.vbs` / `hidden-start-chameleon.vbs` | 启动器 | 静默启动（`--chameleon` 无控制台闪窗） |

## 功能链路

- **实时面试**（自动模式，默认开）：双轨录音（回环轨=对方、麦克风轨=自己，外放免耳机）→ 回环 VAD 断句攒问题 → 你开口/停顿自动发送 → 云端转写 → 问答模型按“先概括关键点、再展开说明”作答（优先 DeepSeek，未配置则复用主视觉模型；带历史+简历上下文）→ 屏幕小窗显示；打断自动作废在途答案；全程 WAV + JSONL 落盘复盘。
- **测评/笔试快答**：截图当前题 → 视觉模型直接出答案 → 答案窗显示 + Telegram 推送（F6）+ 可选自动键入（code 版 Alt+1/Alt+2）。
- **Prompt 场景**：任意时刻只启用一个技术场景（通用 / AI Infra / 前端 / 自定义），语音回答与截图回答同步切换；点击答案窗右上角的场景名称可编辑。
- **code 笔记专区**：悬浮窗顶部通过「回答 / 笔记」按钮切换；笔记页递归读取 `notes/` 下的 Markdown，只在本地展示，不发送给模型。
- 防捕获常驻开启：共享屏幕/录屏时答案窗从捕获画面消失，不提供关闭热键。

## 热键速查（以 code 版为例，quiz 版差异见 `profiles.py` 与文件头注释）

```
Alt+P     截屏识图（同题可续截，自动带多轮记忆）
Alt+1     答案就位后自动打字（再按停止）
Alt+2     剪贴板粘贴整段答案
Alt+3     换新题：清空识图记忆和当前答案显示（历史仍可回看）
F4        隐藏/显示答案窗（也可点击右上角“—”隐藏）
F6        Telegram 手机推送开关
F9        按住解除门控
↑ / ↓     回答页翻历史；笔记页切换上一篇 / 下一篇
Ctrl+Esc  紧急暂停     ESC×2 或 Ctrl+Q  退出
```

## 配置

复制 `.env.example` 为 `.env` 并填入：

- `ARK_API_KEY` / `ARK_VISION_MODEL` / `VISION_BASE_URL` — 识图视觉模型（OpenAI 兼容）
- `DEEPSEEK_API_KEY` — 可选；配置后语音问答使用 DeepSeek，留空则复用上述主视觉模型
- `BOT_TOKEN` / `ALLOWED_IDS` / `PROXY` — Telegram 推送
- `ISI_APPKEY` / `ALIYUN_AK_ID` / `ALIYUN_AK_SECRET` / `DASHSCOPE_API_KEY` — 转写/备用 ASR（可选）

`.env` 已在 `.gitignore`，不入库。真实 key 私下分发，勿提交进仓库。

### Prompt 场景

答案窗右上角显示当前场景，点击场景名称或齿轮打开设置窗口。场景是单选的：保存
`AI Infra` 后，语音和截图都会使用 Infra Prompt；再保存`前端`后，Infra 场景立即失效。

- 内置 `通用 / AI Infra / 前端`，均可修改并恢复默认。
- 可以把当前内容另存为自定义场景，自定义场景可删除。
- 每个场景分别保存语音 Prompt、截图 Prompt 和默认编程语言。
- 修改只影响下一次请求；正在生成的回答不会被中断。
- 应用场景会清空模型对话上下文和截图多轮记忆，但答案窗里的历史仍可回看。
- 最后选中的场景保存在本地 `prompt-scenes.json`，该文件已忽略，不会提交 Git。

`quiz/code` 仍只负责热键、窗口和截图输出协议，技术场景由独立的 PromptStore 管理，
两者不会互相复制或组合出额外入口。

## 启动

```bash
python run_quiz.py --chameleon   # 测评版
python run_code.py --chameleon   # 笔试版
```

### 变色龙自动背景

`--chameleon` 用于开启变色龙背景模式。程序会定时采样答案窗口外侧的屏幕颜色，自动更新窗口底色，并根据背景亮度切换文字深浅，使答案窗尽量融入当前桌面或应用背景：

```bash
python run_code.py --chameleon
```

也可以双击 `hidden-start-chameleon.vbs` 静默启动。另有 `--acrylic` 磨砂玻璃背景模式，建议与 `--chameleon` 二选一使用。

### 手动录音模式

默认不加 `--manual` 时使用双轨 VAD 自动断句模式。需要手动控制一道题的录音边界时，使用：

```bash
python run_code.py --manual
```

手动模式操作流程：

1. 按 `F1` 开始录音。
2. 等面试官说完问题后按 `F2`。
3. 程序停止本轮录音，将音频提交云端转写；转写文本上屏后，自动调用问答模型生成答案。

手动模式默认同时启动电脑回环和麦克风，因此既能收电脑播放的面试官声音，也能直接用麦克风讲话测试；按 `F10` 可在“全听”和“只听电脑输出”之间切换。

手动模式也可以和其他启动参数组合，例如：

```bash
python run_code.py --manual --chameleon
```

或双击对应 `.vbs`（静默启动）。会话日志落 `logs/session-*.jsonl`（`.gitignore`）。

## 开发

- 生成文件（`engine.py` / `profiles.py`）禁止手改：改旧版单体或 profiles 文本后跑 `python tools/gen_engine.py` / `tools/gen_profiles.py` 重新生成（锚点校验失败即中止，源文本被 git 历史保留：`git show <旧commit>:interview-cheat-code.py`）。
- 每次搬移/收敛改动过包模块后跑门禁：`python -m py_compile run_quiz.py run_code.py interview_tool/*.py` + `python tools/parity.py`（改动需 ⊆ allow 清单）。

## 依赖

Python 3.12；`pip install pyaudiowpatch websockets requests numpy Pillow`（tkinter 内置）。

启动前先杀旧进程再开新版（同屏双窗会混）。
