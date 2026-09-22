#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_engine.py — 从旧 code 版单体的 main() 生成 interview_tool/engine.py

engine.py = code 版主逻辑（行为主本）+ 登记点编辑：
  (a) def main(): → def main(profile): + ACTIVE 激活 + 打字实现按 flavor 注入
  (b) 原 global LOG_FILENAME 4 行块 → log.start_session(answer_model, args.no_inject)（R3 模块注入）
  (c) wav 落盘目录引用 LOG_FILENAME → log.LOG_FILENAME（R2 属主访问，只读）
  (d) 补 VK_F3 = 0x72（quiz 测评版主键；code 场景废弃——f3/p 槽无人读写）
  (e) hk dict 追加 f3/p/d1 三槽（quiz 测评版键位；code 场景无人读写=无害）
  (f) ESC 单按块：quiz 分支（原文：单按即退）+ code 分支（原文：0.8s 内双按才退）
  (g) 识图/注入分歧段 → if profile.key == "quiz": <quiz 原文整段> else: <code 原文整段>
      （两段文本各自从对应单体原行切片，程序化缩进）
  (h) 就绪横幅按当前快捷键配置动态生成
  (i) 问答后端选择：DEEPSEEK_API_KEY 优先；未配置时复用主视觉模型三件套
  (j) 移除 F7 防捕获开关；防捕获由 state 固定常驻开启
  (k) 注入单活动 PromptStore：设置窗口切场景、语音下一题换 system、截图按场景快照
  (l) 硬编码热键轮询替换为 HotkeyBindings / HotkeyRuntime（.env 可重绑）

所有点编辑的缩进一律从锚点行推导（不硬编码列号）；每个编辑的目标文本都做内容断言，
源文件漂移即中止 → 生成器可放心重跑。引擎自身除 profile.key 分叉外零场景判断——
全部差异已收容到 profiles.py / 各 flavor 注入实现。
用法：python tools/gen_engine.py
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE = os.path.join(ROOT, "interview-cheat-code.py")
QUIZ = os.path.join(ROOT, "interview-cheat-quiz.py")
OUT = os.path.join(ROOT, "interview_tool", "engine.py")


def read_lines(path):
    with open(path, encoding="utf-8") as f:
        return f.read().splitlines()


def find_line(lines, key):
    """返回首个包含 key 的行号(1-based)；必须恰好一个命中，否则中止"""
    hits = [i + 1 for i, l in enumerate(lines) if key in l]
    if len(hits) != 1:
        raise SystemExit(f"锚 '{key[:60]}' 命中 {len(hits)} 处（期望 1）")
    return hits[0]


def ind_of(line):
    return len(line) - len(line.lstrip())


def block_indent(lines):
    inds = [ind_of(l) for l in lines if l.strip()]
    return min(inds) if inds else 0


def reindent(lines, pad):
    """去掉自身公共缩进后再统一加 pad"""
    cut = block_indent(lines)
    return [(" " * pad + l[cut:]) if l.strip() else "" for l in lines]


def strip_blank(ls):
    """去首尾空行"""
    while ls and not ls[0].strip():
        ls.pop(0)
    while ls and not ls[-1].strip():
        ls.pop()
    return ls


def main():
    code_l = read_lines(CODE)
    quiz_l = read_lines(QUIZ)

    # ---------- 收集点编辑；统一语义 code_l[start-1:end] = new ----------
    #   end >= start   → 替换原 start..end 闭区间行
    #   end == start-1 → 在 start-1 处纯插入（插在原 start 行之前）
    #   end == start   → 替换单行；插入在某行 a 之后用 (a+1, a, new)
    edits = []

    # (b) LOG 4 行块 → log.start_session（保留原注释行原文）
    b_head = find_line(code_l, "# 本场日志：logs/session-时间戳.jsonl（重启提词器 = 新一场）")
    assert code_l[b_head + 0 - 1 + 1].strip().startswith("global LOG_FILENAME"), code_l[b_head]
    assert 'LOG_FILENAME = f"session-' in code_l[b_head + 2]
    assert 'log_event({"type": "session_start"' in code_l[b_head + 3]
    b_pad = " " * ind_of(code_l[b_head - 1])
    edits.append((b_head, b_head + 4, [
        code_l[b_head - 1],
        b_pad + "log.start_session(answer_model, args.no_inject)"
                "   # R3 注入：原 4 行块（global 声明+建目录+命名+session_start）封装",
    ]))

    # (i) 问答后端：DeepSeek key 优先；没有则复用主识图链路的多模态模型。
    # --model 默认必须改成 None，才能根据最终选中的后端取各自默认模型。
    model_line = find_line(code_l, 'ap.add_argument("--model", default=DEEPSEEK_MODEL)')
    model_pad = " " * ind_of(code_l[model_line - 1])
    edits.append((model_line, model_line, [
        model_pad + 'ap.add_argument("--model", default=None,',
        model_pad + '                help="问答模型名（默认随所选后端：DeepSeek 或主视觉模型）")',
    ]))

    select_head = find_line(code_l, 'api_key = args.api_key or _env_get("DEEPSEEK_API_KEY")')
    assert "if not args.no_inject and not api_key:" in code_l[select_head]
    assert "sys.exit(" in code_l[select_head + 1]
    assert 'print(f"🤖 API: {args.model}"' in code_l[select_head + 2]
    select_pad = " " * ind_of(code_l[select_head - 1])
    edits.append((select_head, select_head + 3, [
        select_pad + "# 问答后端优先级：显式 --api-key / DEEPSEEK_API_KEY → DeepSeek；否则复用",
        select_pad + "# 主识图链路的 key、模型和 URL。视觉模型是多模态模型，也可接收纯文本问题。",
        select_pad + 'deepseek_key = args.api_key or _env_get("DEEPSEEK_API_KEY")',
        select_pad + '_vision_tag, vision_key, vision_model, vision_url = _vision_providers()[0]',
        select_pad + "if deepseek_key:",
        select_pad + "    api_key = deepseek_key",
        select_pad + "    answer_model = args.model or DEEPSEEK_MODEL",
        select_pad + "    answer_url = DEEPSEEK_URL",
        select_pad + '    answer_backend = "DeepSeek"',
        select_pad + "else:",
        select_pad + "    api_key = vision_key",
        select_pad + "    answer_model = args.model or vision_model",
        select_pad + "    answer_url = vision_url",
        select_pad + '    answer_backend = "识图链路多模态模型"',
        select_pad + "if not args.no_inject and not api_key:",
        select_pad + '    sys.exit("❌ 缺少问答模型凭证：请填写 DEEPSEEK_API_KEY，或配置识图链路的 ARK_API_KEY")',
        select_pad + 'print(f"🤖 问答 API: {answer_backend} / {answer_model}", flush=True)',
    ]))

    agent_head = find_line(code_l, "agent = ChatAgent(api_key, model=args.model,")
    assert "system_prompt=build_system_prompt())" in code_l[agent_head]
    agent_pad = " " * ind_of(code_l[agent_head - 1])
    edits.append((agent_head, agent_head + 1, [
        agent_pad + "agent = ChatAgent(api_key, model=answer_model,",
        agent_pad + "                  system_prompt=build_system_prompt(), base_url=answer_url)",
    ]))

    # (j) 防捕获固定常驻开启：释放 F7，不生成按键常量、状态槽和切换逻辑。
    hotkey_comment = find_line(code_l, "/ F7 防捕获 /")
    edits.append((hotkey_comment, hotkey_comment, [
        code_l[hotkey_comment - 1].replace(" / F7 防捕获", ""),
    ]))

    f7_vks = find_line(code_l, "VK_F6, VK_F7, VK_F8, VK_F9, VK_F10")
    f7_vks_pad = " " * ind_of(code_l[f7_vks - 1])
    edits.append((f7_vks, f7_vks, [
        f7_vks_pad + "VK_F6, VK_F8, VK_F9, VK_F10 = 0x75, 0x77, 0x78, 0x79",
    ]))

    f7_slot = find_line(code_l, '"f7": False, "f6": False')
    edits.append((f7_slot, f7_slot, [
        code_l[f7_slot - 1].replace('"f7": False, ', ""),
    ]))

    f7_head = find_line(code_l, "# F7 防捕获开关：")
    f7_tail = find_line(code_l, 'hk["f7"] = f7')
    assert f7_tail > f7_head
    edits.append((f7_head, f7_tail, []))

    # (c) wav 落盘目录：裸 LOG_FILENAME（原 global）→ log.LOG_FILENAME 属主访问
    w_line = find_line(code_l, "wav_dir = os.path.join(LOG_DIR, LOG_FILENAME[:-6]")
    w_new = code_l[w_line - 1].replace("LOG_FILENAME", "log.LOG_FILENAME")
    assert w_new != code_l[w_line - 1] and "log.LOG_FILENAME" in w_new
    edits.append((w_line, w_line, [w_new]))

    # (d) VK_F4 行后插 VK_F3（quiz 测评版主键；code 场景废弃不读写）
    v4 = find_line(code_l, "VK_F4 = 0x73")
    assert code_l[v4 - 1].strip().startswith("VK_F4 = 0x73")
    v4_pad = " " * ind_of(code_l[v4 - 1])
    edits.append((v4 + 1, v4, [
        v4_pad + "VK_F3 = 0x72    # quiz 测评版主键：F3/P 裸键识图（code 场景废弃——f3/p 槽无人读写）",
    ]))

    # (e) hk dict 尾行 → 追加 quiz 三槽后收口
    h1 = find_line(code_l, '"altp": False, "alt1": False, "alt2": False, "alt3": False}')
    h1_pad = " " * ind_of(code_l[h1 - 1])
    assert code_l[h1 - 1].rstrip().endswith("}")
    edits.append((h1, h1, [
        code_l[h1 - 1].rstrip()[:-1] + ",",
        h1_pad + '"f3": False, "p": False, "d1": False}   # quiz 测评版槽位（code 场景无人读写）',
    ]))

    # (f) ESC 注释换双 flavor 说明；0.8s 分支前插 quiz 单按分支（quiz 原文 body 切片）
    esc_c = find_line(code_l, "# ESC 单独按：退出进程。")
    esc_c_pad = " " * ind_of(code_l[esc_c - 1])
    edits.append((esc_c, esc_c + 2, [
        esc_c_pad + "# ESC 单独按：退出进程。quiz 版：单按即退（下方第一支，原文整段）；⚠️ code 笔试版：",
        esc_c_pad + "# 写码时 IDE/编辑器单按 ESC 极常见（关补全、取消弹窗）——单按退出是事故",
        esc_c_pad + "# （2026-09-06 实测被连杀两次），需 0.8s 内连按两次才退（第二支原文）。",
        esc_c_pad + "# Ctrl+Esc 仍是暂停（上面分支），Ctrl+Q 仍一键退",
    ]))
    esc_body = find_line(code_l, "if time.time() - esc_exit_prev < 0.8:")
    esc_pad = " " * ind_of(code_l[esc_body - 1])
    # quiz 原文 body：log_event(ESC) … os._exit(0)（共 5 行）
    q_log = find_line(quiz_l, '"reason": "ESC"')
    q_rows = []
    i = q_log
    while not quiz_l[i - 1].strip().startswith("os._exit(0)"):
        q_rows.append(quiz_l[i - 1])
        i += 1
    q_rows.append(quiz_l[i - 1])
    assert len(q_rows) == 5, f"quiz ESC body 应 5 行: {q_rows}"
    assert q_rows[0].strip().startswith('log_event({"type": "session_end", "reason": "ESC"})')
    assert "save_window_geometry(root.geometry())" in q_rows[2]
    edits.append((esc_body, esc_body - 1, [
        esc_pad + 'if profile.key == "quiz":   # quiz 版：ESC 单按即退（原文整段）',
    ] + reindent(q_rows, ind_of(code_l[esc_body - 1]) + 4)))

    # (g) 识图/注入分歧段 → flavor 分叉（quiz / code 各自原文整段）
    g_head = find_line(code_l, "# ---- 识图/注入组合键全部收敛成 Alt+")
    g_tail = find_line(code_l, '            hk["alt3"] = alt3')
    assert code_l[g_tail - 1].strip() == 'hk["alt3"] = alt3'
    code_div = code_l[g_head - 1:g_tail]
    q_head = find_line(quiz_l, "# F3 / P 截屏识图（面试官共享屏幕/测评题目截图")
    q_tail = find_line(quiz_l, '            hk["d1"] = k1')
    assert quiz_l[q_tail - 1].strip() == 'hk["d1"] = k1'
    quiz_div = quiz_l[q_head - 1:q_tail]
    g_pad = " " * ind_of(code_l[g_head - 1])
    g_body = g_pad + "    "          # 分叉体缩进 = 锚缩进 + 4
    edits.append((g_head, g_tail, [
        g_pad + 'if profile.key == "quiz":',
        g_body + "# ---- quiz 测评版原文：F3/P 裸键识图 + 裸 1 自动打字 ----",
    ] + reindent(quiz_div, len(g_body)) + [
        g_pad + "else:",
        g_body + "# ---- code 笔试版原文：Alt+P/Alt+1/Alt+2/Alt+3（裸键全释放防误触发）----",
    ] + reindent(code_div, len(g_body))))

    # (h) 就绪横幅 → ACTIVE 字段（AST 定位 call 起止行）
    fn = next(n for n in ast.parse("\n".join(code_l), CODE).body
              if isinstance(n, ast.FunctionDef) and n.name == "main")
    banner = {}
    for node in ast.walk(fn):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "print" and node.value.args
                and isinstance(node.value.args[0], ast.Constant)
                and isinstance(node.value.args[0].value, str)):
            t = node.value.args[0].value
            if "✅ 就绪（自动模式）" in t:
                banner["auto"] = (node.lineno, node.end_lineno)
            elif "✅ 就绪（手动模式）" in t:
                banner["manual"] = (node.lineno, node.end_lineno)
    assert len(banner) == 2, f"就绪横幅定位失败: {sorted(banner)}"
    for which, (s, e) in banner.items():
        b_pad = " " * ind_of(code_l[s - 1])
        edits.append((s, e, [
            b_pad + f"print(profiles.ACTIVE.banner_{which}, flush=True)"
                    "   # 就绪横幅原文在 profiles（两版差异收容）",
        ]))

    # ---------- 按原行号降序套用（编辑区间互不重叠，安全） ----------
    for start, end, new in sorted(edits, key=lambda t: -t[0]):
        code_l[start - 1:end] = new

    # ---------- 编辑后重新 AST 定位 main 边界，切主体 ----------
    fn2 = next(n for n in ast.parse("\n".join(code_l), CODE).body
               if isinstance(n, ast.FunctionDef) and n.name == "main")
    body = code_l[fn2.lineno - 1:fn2.end_lineno]
    assert body[0] == "def main():"
    tail = "\n".join(code_l[fn2.end_lineno:])
    assert 'if __name__ == "__main__"' in tail, "main 之后应只剩 __main__ 守卫"
    assert "recorder_mic.stop()" in "\n".join(body[-12:]), "main 尾部异常（锚 recorder_mic.stop 缺失）"

    # def 行换签名 + 注入 ACTIVE/typing 别名
    body[0:1] = [
        "def main(profile):",
        "    profiles.ACTIVE = profile        # flavor 激活：分叉段与 ACTIVE.* 字段取用（run_quiz/run_code 传入）",
        "    prompt_store = PromptStore()     # 技术场景独立于 quiz/code；任意时刻只激活一个",
        "    # 打字实现注入：quiz/code 保留各自的前台输入策略，按键与松键等待由 hotkeys 统一处理。",
        "    type_answer_into_foreground = (typing_quiz.type_answer_into_foreground",
        "                                   if profile.key == \"quiz\"",
        "                                   else typing_code.type_answer_into_foreground)",
    ]

    # (k) Prompt 场景编排。放在切片完成后的 body 上做锚点编辑，避免污染旧单体源。
    def body_find(key):
        hits = [i for i, line in enumerate(body) if key in line]
        if len(hits) != 1:
            raise SystemExit(f"body 锚 '{key[:60]}' 命中 {len(hits)} 处（期望 1）")
        return hits[0]

    p_api = body_find('print(f"🤖 问答 API: {answer_backend} / {answer_model}"') + 1
    body[p_api:p_api] = [
        "    active_scene = prompt_store.get_active_scene()",
        "    print(f\"🎯 Prompt 场景: {active_scene['name']}\", flush=True)",
        "    if prompt_store.load_error:",
        "        print(f\"⚠️ Prompt 场景配置读取失败，已回退默认：{prompt_store.load_error}\", flush=True)",
    ]
    p_log = body_find("log.start_session(answer_model, args.no_inject)") + 1
    body[p_log:p_log] = [
        '    log_event({"type": "prompt_scene", "scene_id": active_scene["id"],',
        '               "scene_name": active_scene["name"], "reason": "session_start"})',
    ]

    p_root = body_find("    root = None")
    p_agent_tail = body_find("system_prompt=build_system_prompt(), base_url=answer_url)")
    body[p_root:p_agent_tail + 1] = [
        "    agent = None",
        "",
        "    def on_prompt_apply(scene):",
        '        """设置窗口回调：两条链路一起切换，保留 UI 历史但重置模型私有上下文。"""',
        '        _vis_mem_reset("Prompt 场景切换")',
        "        if agent is not None:",
        "            agent.schedule_system_prompt(build_system_prompt(prompt_store),",
        "                                         reset_history=True)",
        '        log_event({"type": "prompt_scene", "scene_id": scene["id"],',
        '                   "scene_name": scene["name"], "reason": "user_apply"})',
        '        ui("status", f"🎯 场景已切换：{scene[\'name\']} · 下一题生效")',
        '        print(f"🎯 Prompt 场景已切换: {scene[\'name\']}（下一题生效）", flush=True)',
        "",
        "    root = None",
        "    if not args.no_window:",
        "        root = show_answer_window(ui_q, prompt_store=prompt_store,",
        "                                  on_prompt_apply=on_prompt_apply)",
        '        set_capture_excluded(root, stealth["on"])   # 防捕获常驻开启',
        "",
        "    if not args.no_inject:",
        "        agent = ChatAgent(api_key, model=answer_model,",
        "                          system_prompt=build_system_prompt(prompt_store), base_url=answer_url)",
    ]
    vision_calls = [i for i, line in enumerate(body)
                    if "threading.Thread(target=do_vision, args=(ui,)," in line]
    if len(vision_calls) != 2:
        raise SystemExit(f"do_vision 调用应为 2 处，实际 {len(vision_calls)}")
    for i in vision_calls:
        body[i] = body[i].replace("args=(ui,)", "args=(ui, prompt_store)")
    alt3_reset = body_find('dropped = _vis_mem_reset("Alt+3 手动清空")') + 1
    body[alt3_reset:alt3_reset] = [
        '                    ui("vision_reset")               # 清掉当前显示/旧答案待输入态，历史仍可回看',
    ]

    # (l) 统一可配置热键。旧单体里的硬编码轮询仍作为生成输入锚点，但不进入产物。
    hotkey_head = body_find("    # 热键轮询（GetAsyncKeyState")
    hotkey_tail = body_find("    threading.Thread(target=hotkey_loop, daemon=True).start()")
    body[hotkey_head:hotkey_tail + 1] = [
        "    # 所有快捷键统一从 .env 解析；默认值保持旧行为。",
        "    hotkey_runtime = HotkeyRuntime(",
        "        bindings=hotkeys, profile_key=profile.key, manual=args.manual,",
        "        root=root, ui=ui, set_status=set_status, state=state,",
        "        state_lock=st_lock, epoch=epoch, recorder_loop=recorder_loop,",
        "        recorder_mic=recorder_mic,",
        "        event_q=event_q if not args.manual else None,",
        "        attach_on=attach_on if not args.manual else None,",
        "        prompt_store=prompt_store, type_answer=type_answer_into_foreground,",
        "        paste_answer=paste_answer_into_foreground, do_vision=do_vision,",
        "        reset_vision=_vis_mem_reset, save_geometry=save_window_geometry,",
        "        manual_handler=_manual_go,",
        "    )",
        "    threading.Thread(target=hotkey_runtime.run, daemon=True).start()",
    ]

    acrylic_line = body_find('    CHAMELEON["on"] = args.chameleon') + 1
    body[acrylic_line:acrylic_line] = [
        "    hotkeys = HotkeyBindings(profile.key)",
        '    profiles.ACTIVE.vision_retry = hotkeys.label("vision")',
        "    for warning in hotkeys.warnings:",
        '        print(f"⚠️ 快捷键配置: {warning}", flush=True)',
    ]

    crash_comment = body_find("    # 崩溃兜底：hidden-start")
    body[crash_comment] = body[crash_comment].replace(
        "logs/cheat-crash.log", "logs/interview-crash.log")
    crash_path = body_find('"cheat-crash.log"')
    body[crash_path] = body[crash_path].replace(
        '"cheat-crash.log"', '"interview-crash.log"')

    state_line = body_find('    state = {"mode": "listen", "paused": False')
    body[state_line:state_line + 1] = [
        "    # 手动模式默认全听，让 F1/F2 同时收回环和麦克风；F10 仍可切到只听。",
        '    state = {"mode": "full" if args.manual else "listen",',
        '             "paused": False, "tp": False}   # listen=只听 / full=全听 / tp=提词器',
    ]

    force_mic_print = body_find('print("🎤 F9 按住：强制收录你的声音"')
    force_mic_pad = " " * ind_of(body[force_mic_print])
    body[force_mic_print:force_mic_print + 1] = [
        force_mic_pad + 'print(f"🎤 {hotkeys.label(\'force_mic\')} 按住：强制收录你的声音",',
        force_mic_pad + "      flush=True)",
    ]

    prompt_log_tail = body_find('               "scene_name": active_scene["name"], "reason": "session_start"})') + 1
    body[prompt_log_tail:prompt_log_tail] = [
        '    log_event({"type": "hotkeys", "profile": profile.key,',
        '               "bindings": hotkeys.labels(), "warnings": hotkeys.warnings})',
    ]

    ui_call = body_find("                                  on_prompt_apply=on_prompt_apply)")
    body[ui_call:ui_call + 1] = [
        "                                  on_prompt_apply=on_prompt_apply,",
        "                                  hotkey_labels=hotkeys.labels())",
    ]

    startup_comment = body_find("    # 启动：录音流常开")
    startup_tail = body_find('    print("=" * 50, flush=True)')
    body[startup_comment:startup_tail] = [
        "    # 启动：录音流常开（F1/F2 控制攒与不攒）；防捕获与手机推送常驻开",
        "    recorder_loop.start()",
        "    if recorder_mic:",
        "        recorder_mic.start()   # 自动模式常开；手动模式默认全听，F1/F2 同时收两轨",
        "    print(hotkeys.banner(args.manual), flush=True)",
        "    if not args.manual:",
        '        set_status("🕶️ 防捕获开 · 📱 推送开 · 自动模式")',
        "    else:",
        '        set_status("🕶️ 防捕获开 · 📱 推送开 · 手动全听模式 · "',
        '                   f"{hotkeys.label(\'toggle_mode\')}切换")',
    ]

    PRELUDE = '''# -*- coding: utf-8 -*-
"""engine.py — 统一编排主本（quiz 测评版 / code 笔试版共享一份 main）。

由 tools/gen_engine.py 从旧 code 版单体的 main() 生成（锚点校验后可重跑），
文本切片 + 登记点编辑见生成器 docstring；quiz/code 运行差异经 profiles.ACTIVE 取用，
用户技术场景由 PromptStore 注入。禁止手改（要改需同步 tools/gen_engine.py）。

模块级 import 三组：标准库 / 第三方（numpy、pyaudiowpatch——与原单体同款别名）/
interview_tool 内各模块。属主规则（R1/R2）与差异收容表见 profiles.py docstring。
"""
import argparse
import os
import queue
import sys
import threading
import time
import traceback
from collections import deque
from threading import Thread

import numpy as np
import pyaudiowpatch as pyaudio

from . import log, profiles, typing_code, typing_quiz
from .asr import clean_asr_text, transcribe
from .audio import Recorder, WavWriter, loop_tcp_thread, pick_loopback_device
from .chat import ChatAgent, DEEPSEEK_MODEL, DEEPSEEK_URL, build_system_prompt
from .config import (AUTO_ATTACH_ON, B_TRIGGER_SEC, BLOCK,
                     ESCAPE_COOLDOWN, GATE_ESCAPE_ABS, GATE_ESCAPE_RATIO,
                     GATE_RECYCLE_SEC, LOG_DIR, MIN_UTTERANCE_SEC,
                     MY_ANSWER_MAX_CHARS, MY_ANSWER_TEXT_MAX, MY_BATCH_SEC,
                     SAMPLE_RATE, _env_get)
from .dsp import GateState, SpeechDetector
from .hotkeys import HotkeyBindings, HotkeyRuntime
from .log import log_event
from .prompt_store import PromptStore
from .push import push_answer
from .state import (ACRYLIC, CHAMELEON, TYPING_STATE, VISION_STATE, push_on,
                    stealth)
from .typing_code import paste_answer_into_foreground
from .ui import hist, load_history_from_logs, save_window_geometry, \\
    show_answer_window
from .vision import _vis_mem_reset, _vision_providers, do_vision
from .winfx import set_capture_excluded
'''

    content = PRELUDE + "\n\n" + "\n".join(body) + "\n"
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"engine.py 已生成: {OUT} ({content.count(chr(10)) + 1} 行)")


if __name__ == "__main__":
    main()
