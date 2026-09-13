# -*- coding: utf-8 -*-
"""profiles.py — 双场景差异数据与分支函数（quiz 测评版 / code 笔试版）。

由 tools/gen_profiles.py 生成（锚点校验后可重跑），字段与方法体文本逐字来自旧单体
旧 quiz/code 单体，禁止手改（要改先改源再重新生成）。

场景差异收容表（新增差异时按类往哪放）：
  · 文本类（system_prompt/vision_prompt/vision_max_tokens/vision_retry/就绪横幅）
      → 本模块 Profile 字段，共享主本经 ACTIVE.字段 取用（调用时解析）
  · 窗口类（初始几何 / 缩放手柄）→ place_window / mount_window_extra 覆写
  · 热键类（vision/type 键位、Alt+2/Alt+3、ESC 退出节奏）→ engine.main 内 flavor 分叉段
  · 状态栏/一次性短文本 → engine.main 分叉段内原文行（两版相同则留共享行）

零包内 import（config 等下层模块反向依赖也不会成环）；tkinter 只做局部 import。
engine.main(profile) 首行执行 profiles.ACTIVE = profile；模块只读期 ACTIVE 为 None。
"""

class Profile:
    """场景差异容器。字段值全部为旧单体原文；窗口差异方法由子类覆写。"""

    def __init__(self, key, *, system_prompt, vision_prompt, vision_max_tokens,
                 vision_retry, banner_auto, banner_manual):
        self.key = key                          # "quiz" | "code"：flavor 分叉判定
        self.system_prompt = system_prompt
        self.vision_prompt = vision_prompt
        self.vision_max_tokens = vision_max_tokens
        self.vision_retry = vision_retry        # do_vision 答崩提示里的按键名
        self.banner_auto = banner_auto
        self.banner_manual = banner_manual

    def place_window(self, root, load_window_geometry):
        raise NotImplementedError                # QuizProfile/CodeProfile 覆写

    def mount_window_extra(self, root, fam, bg):
        raise NotImplementedError                # QuizProfile 空实现 / CodeProfile 手柄


class QuizProfile(Profile):
    """quiz 测评版：560x160 小窗顶中，右下角可拉大看长答案"""

    def place_window(self, root, load_window_geometry):
        """quiz 版：水平居中、垂直顶边贴屏幕最上（用户要求）；之后可正常拖动"""
        # 初始化：水平居中、垂直顶边贴屏幕最上（用户要求）；之后可正常拖动
        _sw = root.winfo_screenwidth()
        root.geometry(f"560x160+{(_sw - 560) // 2}+0")

    def mount_window_extra(self, root, fam, bg):
        """quiz 版：右下角缩放柄与 code 版共用（2026-09-13 起：长答案也要拉大窗口看）"""
        import tkinter as tk   # 局部 import：与旧 show_answer_window 同款
        # 右下角缩放柄：无边框窗没有系统缩放手柄，长代码答案拉大窗口看（最小 300x160）。
        # 返回 "break" 阻断冒泡到 root 的拖拽绑定（同 close_btn 的防打架手法）
        grip = tk.Label(root, text="⣿", bg=bg, fg="#7a7a7a",
                        cursor="size_nw_se", font=(fam, -10))
        grip.place(relx=1.0, x=-14, rely=1.0, y=-15)
        def on_grip_press(e):
            root._grip0 = (e.x_root, e.y_root)
            root._grip_geo = root.geometry()
        def on_grip_move(e):
            try:
                wh, xy = root._grip_geo.split("+")[0], root._grip_geo.split("+")[1:]
                gw, gh = int(wh.split("x")[0]), int(wh.split("x")[1])
                gw = max(300, gw + (e.x_root - root._grip0[0]))
                gh = max(160, gh + (e.y_root - root._grip0[1]))
                root.geometry(f"{gw}x{gh}+{xy[0]}+{xy[1]}")
            except Exception:
                pass
        grip.bind("<ButtonPress-1>", lambda e: (on_grip_press(e), "break")[1])
        grip.bind("<B1-Motion>", lambda e: (on_grip_move(e), "break")[1])

class CodeProfile(Profile):
    """code 笔试版：恢复上次窗口或 760x460 大窗，带右下角缩放手柄"""

    def place_window(self, root, load_window_geometry):
        """code 版：重启回到上次的尺寸+位置（560x160 代码根本排不下）；无记录则 760x460 默认放大"""
        # 初始化：水平居中、垂直顶边贴屏幕最上（用户要求）；之后可正常拖动
        _sw = root.winfo_screenwidth()
        _saved = load_window_geometry()
        if _saved:                      # 重启回到上次的尺寸+位置（560x160 代码根本排不下）
            root.geometry(_saved)
        else:
            root.geometry(f"760x460+{(_sw - 760) // 2}+0")   # 笔试版默认放大：思路+代码一屏起步

    def mount_window_extra(self, root, fam, bg):
        """code 版：右下角缩放柄原文——长代码答案拉大窗口看（最小 300x160）"""
        import tkinter as tk   # 局部 import：与旧 show_answer_window 同款
        # 右下角缩放柄：无边框窗没有系统缩放手柄，长代码答案拉大窗口看（最小 300x160）。
        # 返回 "break" 阻断冒泡到 root 的拖拽绑定（同 close_btn 的防打架手法）
        grip = tk.Label(root, text="⣿", bg=bg, fg="#7a7a7a",
                        cursor="size_nw_se", font=(fam, -10))
        grip.place(relx=1.0, x=-14, rely=1.0, y=-15)
        def on_grip_press(e):
            root._grip0 = (e.x_root, e.y_root)
            root._grip_geo = root.geometry()
        def on_grip_move(e):
            try:
                wh, xy = root._grip_geo.split("+")[0], root._grip_geo.split("+")[1:]
                gw, gh = int(wh.split("x")[0]), int(wh.split("x")[1])
                gw = max(300, gw + (e.x_root - root._grip0[0]))
                gh = max(160, gh + (e.y_root - root._grip0[1]))
                root.geometry(f"{gw}x{gh}+{xy[0]}+{xy[1]}")
            except Exception:
                pass
        grip.bind("<ButtonPress-1>", lambda e: (on_grip_press(e), "break")[1])
        grip.bind("<B1-Motion>", lambda e: (on_grip_move(e), "break")[1])

QUIZ = QuizProfile(key="quiz", system_prompt='你是实时面试陪练助手：用户正在面试中，会把面试官的问题转写给你。请直接给出简洁、口语化、可以照着念的答案要点，用中文回答，不要铺垫，不要反问，不要 Markdown 装饰。如果题目要求手撕代码/写算法：直接给出完整可运行的代码，注释只保留关键一行，代码后附一句时间/空间复杂度。用户消息末尾可能出现【附：你此前的回答】段——那是用户实际口头说出的回答，供你参考以承接追问、避免重复，它本身不是新问题，不要把它当问题回答。',
                   vision_prompt='这是测评/行测题的屏幕截图（言语理解、逻辑填空、图形推理、数量关系、图表资料分析、性格测评等），不是编程题。请直接作答：①答案选项（如“选 B”）②一句话理由（引题干关键句或图表数字；注意看清图表里的小数字再算，绝不编数据）。输出简短，不要代码，不要长篇解析。', vision_max_tokens=2000,
                   vision_retry='P', banner_auto='✅ 就绪（自动模式）。双轨全程录音：面试官说话自动断句攒问题，你开口（或停顿 2.5s）自动发送 → DeepSeek 作答。全程 WAV 落盘可复盘。🕶️ 防捕获常驻开，📱 手机推送常驻开（F7/F6 可关）。F1 手动录问题兜底，F3 识图，F4 隐藏，F8 提词器，识图后按 1 自动打进答题框，F9 按住强制收录你的话，F10 附注你的回答开关，↑↓ 翻历史，ESC/Ctrl+Q 退出，Ctrl+Esc 暂停', banner_manual='✅ 就绪（手动模式）。F1 开始录音 → 面试官提问 → F2 结束 → 转写提问上屏 → DeepSeek 作答。🕶️ 防捕获常驻开，📱 手机推送常驻开（F7/F6 可关）。F3 识图，F4 隐藏窗口，F8 提词器，识图后按 1 自动打进答题框，F10 全听，↑↓ 翻历史，ESC/Ctrl+Q 退出，Ctrl+Esc 暂停')
CODE = CodeProfile(key="code", system_prompt="你是实时面试陪练助手：用户正在面试中，会把面试官的问题转写给你。请直接给出简洁、口语化、可以照着念的答案要点，用中文回答，不要铺垫，不要反问，不要 Markdown 装饰。严禁输出思考摸索过程（内心推演、草稿、多方案对比、'让我想想'类填充）——只输出最终结论：分点清晰、可直接照念，宁可精炼不要冗长。如果题目要求手撕代码/写算法：直接给出完整可运行的代码，注释只保留关键一行，代码后附一句时间/空间复杂度。用户消息末尾可能出现【附：你此前的回答】段——那是用户实际口头说出的回答，供你参考以承接追问、避免重复，它本身不是新问题，不要把它当问题回答。",
                   vision_prompt="你是笔试/手撕代码助手。本次请求可能附带【历史截图】（同一道题此前截的片段，旧→新排列，可能是没截全的题目拼图、运行报错、测试用例输出）和【此前解答】文本，最后一张是【最新截图】。\n规则1：先判断最新截图与历史内容是否同一道题——\n  · 同一题的补充（题目分两次截没拼全 / 报错信息 / 测试用例输出 / 要求继续优化）：必须结合历史与上次解答处理。拼图类把题目信息拼全后正常作答。\n  明显是全新题目：忽略历史，按新题作答。\n规则2：严禁输出思考摸索过程（试错、草稿、多方案对比、'我看看'之类）——只给结论。\n规则3：输出固定三块（必要时按规则4加【框架】成四块）——\n【思路】最多 5 条分点，每条一行内，点明算法名/关键步骤；\n【代码】（你是一个模拟输入器，生成的代码会模拟输入到对应位置 不要给现在已有的代码 会造成重复）代码放 markdown 代码块（按题目要求语言，默认 js，注意题目要求 范围 输出时不要解释，变量名用 三个以内字母 简写，只给最终代码，代码减少ai痕迹。同时注意 对于图片已有的注释或者已有代码 不再生成  你生成的代码 会连同格式 直接在开始写代码那一行 最前面 自己控制 缩进等等）；\n【复杂度】一行。\n规则4：如果题目自带代码框架（截图里的代码编辑器已预填头文件、类声明、函数签名、主函数壳等占位代码），在【代码】之前加一块【框架】：把框架中已有的代码原样抄进 markdown 代码块（一字不改、保留缩进）；题目没有现成框架（编辑器空的/没有编辑器）则不要输出【框架】块。\n中文简洁，直接给结论，不要任何前言。", vision_max_tokens=4096,
                   vision_retry='Alt+P', banner_auto='✅ 就绪（自动模式）。双轨全程录音：面试官说话自动断句攒问题，你开口（或停顿 2.5s）自动发送 → DeepSeek 作答。全程 WAV 落盘可复盘。🕶️ 防捕获常驻开，📱 手机推送常驻开（F7/F6 可关）。F1 手动录问题兜底，Alt+P 截屏识图（同题续截自动带上下文，换题先按 Alt+3 清），F4 隐藏，F8 提词器，识图后 Alt+1 自动打进答题框 / Alt+2 整段粘贴，F9 按住强制收录你的话，F10 附注你的回答开关，↑↓ 翻历史，ESC×2/Ctrl+Q 退出（0.8s内双按），Ctrl+Esc 暂停', banner_manual='✅ 就绪（手动模式）。F1 开始录音 → 面试官提问 → F2 结束 → 转写提问上屏 → DeepSeek 作答。🕶️ 防捕获常驻开，📱 手机推送常驻开（F7/F6 可关）。Alt+P 截屏识图（同题续截自动带上下文，换题先按 Alt+3 清），F4 隐藏窗口，F8 提词器，识图后 Alt+1 自动打进答题框 / Alt+2 整段粘贴，F10 全听，↑↓ 翻历史，ESC×2/Ctrl+Q 退出（0.8s内双按），Ctrl+Esc 暂停')

ACTIVE = None       # engine.main(profile) 首行赋值；其余模块一律经 profiles.ACTIVE 取用
