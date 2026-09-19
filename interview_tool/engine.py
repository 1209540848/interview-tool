# -*- coding: utf-8 -*-
"""engine.py — 统一编排主本（quiz 测评版 / code 笔试版共享一份 main）。

由 tools/gen_engine.py 从旧 code 版单体的 main() 生成（锚点校验后可重跑），
文本切片 + 登记点编辑见生成器 docstring；quiz/code 运行差异经 profiles.ACTIVE 取用，
用户技术场景由 PromptStore 注入。禁止手改（要改需同步 tools/gen_engine.py）。

模块级 import 三组：标准库 / 第三方（numpy、pyaudiowpatch——与原单体同款别名）/
interview_tool 内各模块。属主规则（R1/R2）与差异收容表见 profiles.py docstring。
"""
import argparse
import ctypes
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
from .log import log_event
from .prompt_store import PromptStore
from .push import push_answer
from .state import (ACRYLIC, CHAMELEON, TYPING_STATE, VISION_STATE, push_on,
                    stealth)
from .typing_code import _after_alt_release, paste_answer_into_foreground
from .ui import hist, load_history_from_logs, save_window_geometry, \
    show_answer_window
from .vision import _vis_mem_reset, _vision_providers, do_vision
from .winfx import set_capture_excluded


def main(profile):
    profiles.ACTIVE = profile        # flavor 激活：分叉段与 ACTIVE.* 字段取用（run_quiz/run_code 传入）
    prompt_store = PromptStore()     # 技术场景独立于 quiz/code；任意时刻只激活一个
    # 打字实现注入：quiz 裸 1 走简化打字（原文语义）；code Alt+1 走 _after_alt_release 包 code 版
    type_answer_into_foreground = (typing_quiz.type_answer_into_foreground
                                   if profile.key == "quiz"
                                   else typing_code.type_answer_into_foreground)
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-key", default=None, help="DeepSeek API key（默认读 .env DEEPSEEK_API_KEY）")
    ap.add_argument("--model", default=None,
                    help="问答模型名（默认随所选后端：DeepSeek 或主视觉模型）")
    ap.add_argument("--no-inject", action="store_true", help="只转写不调 API（测链路）")
    ap.add_argument("--no-window", action="store_true", help="不显示答案窗（纯转写测试）")
    ap.add_argument("--acrylic", action="store_true", help="磨砂玻璃背景（DWM Acrylic）替代灰色实底")
    ap.add_argument("--chameleon", action="store_true", help="变色龙背景：吸窗口下方屏幕颜色，文字自动深浅")
    ap.add_argument("--manual", action="store_true",
                    help="手动模式（F1 开始/F2 结束定界，旧版行为；默认自动模式双轨全程录音）")
    ap.add_argument("--mic-device", type=int, default=None,
                    help="麦克风设备索引（默认取系统默认输入；装虚拟声卡测试时指定）")
    ap.add_argument("--loop-device", type=int, default=None,
                    help="回环设备索引（默认按默认输出设备匹配；默认输出被虚拟声卡占用时指定）")
    ap.add_argument("--loop-tcp", type=int, default=None,
                    help="面试官音频走 TCP（本机 PORT 监听）：不采声卡回环——VB-Audio 虚拟声卡"
                    "采集端在本机不可用（全损），播放器进程直发面试官音频绕过声卡；"
                    "真麦克风轨照常从设备采集")
    args = ap.parse_args()
    ACRYLIC["on"] = args.acrylic   # 模块级标志：答案窗按此决定磨砂玻璃 / 灰色实底
    CHAMELEON["on"] = args.chameleon

    # 问答后端优先级：显式 --api-key / DEEPSEEK_API_KEY → DeepSeek；否则复用
    # 主识图链路的 key、模型和 URL。视觉模型是多模态模型，也可接收纯文本问题。
    deepseek_key = args.api_key or _env_get("DEEPSEEK_API_KEY")
    _vision_tag, vision_key, vision_model, vision_url = _vision_providers()[0]
    answer_thinking = None
    if deepseek_key:
        api_key = deepseek_key
        answer_model = args.model or DEEPSEEK_MODEL
        answer_url = DEEPSEEK_URL
        answer_backend = "DeepSeek"
    else:
        api_key = vision_key
        answer_model = args.model or vision_model
        answer_url = vision_url
        answer_backend = "识图链路多模态模型"
        answer_thinking = _env_get("ANSWER_THINKING")
    if not args.no_inject and not api_key:
        sys.exit("❌ 缺少问答模型凭证：请填写 DEEPSEEK_API_KEY，或配置识图链路的 ARK_API_KEY")
    print(f"🤖 问答 API: {answer_backend} / {answer_model}", flush=True)
    active_scene = prompt_store.get_active_scene()
    print(f"🎯 Prompt 场景: {active_scene['name']}", flush=True)
    if prompt_store.load_error:
        print(f"⚠️ Prompt 场景配置读取失败，已回退默认：{prompt_store.load_error}", flush=True)

    # 本场日志：logs/session-时间戳.jsonl（重启提词器 = 新一场）
    log.start_session(answer_model, args.no_inject)   # R3 注入：原 4 行块（global 声明+建目录+命名+session_start）封装
    log_event({"type": "prompt_scene", "scene_id": active_scene["id"],
               "scene_name": active_scene["name"], "reason": "session_start"})

    # 崩溃兜底：hidden-start 启动无控制台，任何线程异常都落到 logs/interview-crash.log
    def _crash_hook(etype, val, tb):
        try:
            with open(os.path.join(LOG_DIR, "interview-crash.log"), "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {etype.__name__}: {val}\n")
                import traceback as _tb
                _tb.print_exception(etype, val, tb, file=f)
        except Exception:
            pass
    def _thread_crash_hook(args):
        # threading.excepthook 回调签名是单参 ExceptionHookArgs（Py3.8+），
        # 与 sys.excepthook 的三参不同——不能共用一个函数，否则钩子先炸、原始异常丢失
        _crash_hook(args.exc_type, args.exc_value, args.exc_traceback)
    threading.excepthook = _thread_crash_hook
    sys.excepthook = _crash_hook

    # 全局状态 + UI 事件队列
    # 手动模式默认全听，让 F1/F2 同时收回环和麦克风；F10 仍可切到只听。
    state = {"mode": "full" if args.manual else "listen",
             "paused": False, "tp": False}   # listen=只听 / full=全听 / tp=提词器
    epoch = {"n": 0}            # 每检测到新语音 +1；答案回来时序号不符 → 作废
    st_lock = threading.Lock()
    ui_q = queue.Queue()

    def ui(kind, payload=None):
        # payload 可省略（F1 热键传单参 "mini"，poll 里按当前状态取反）
        ui_q.put((kind, payload))

    def set_status(text):
        ui("status", text)

    # 对话历史：重启从日志重建（↑↓ 回滚查看不丢）
    hist.clear()
    hist.extend(load_history_from_logs())
    if hist:
        print(f"📜 历史恢复: {len(hist)} 个对话（↑↓ 翻看）", flush=True)

    agent = None

    def on_prompt_apply(scene):
        """设置窗口回调：两条链路一起切换，保留 UI 历史但重置模型私有上下文。"""
        _vis_mem_reset("Prompt 场景切换")
        if agent is not None:
            agent.schedule_system_prompt(build_system_prompt(prompt_store),
                                         reset_history=True)
        log_event({"type": "prompt_scene", "scene_id": scene["id"],
                   "scene_name": scene["name"], "reason": "user_apply"})
        ui("status", f"🎯 场景已切换：{scene['name']} · 下一题生效")
        print(f"🎯 Prompt 场景已切换: {scene['name']}（下一题生效）", flush=True)

    root = None
    if not args.no_window:
        root = show_answer_window(ui_q, prompt_store=prompt_store,
                                  on_prompt_apply=on_prompt_apply)
        set_capture_excluded(root, stealth["on"])   # 防捕获常驻开启

    if not args.no_inject:
        agent = ChatAgent(api_key, model=answer_model,
                          system_prompt=build_system_prompt(prompt_store), base_url=answer_url,
                          thinking=answer_thinking)

    # 问答工作线程（串行调 API；答案作废判定靠 epoch 序号）
    # 警告：严禁并行化！void_last 替换 messages 最后一条 assistant 依赖串行顺序，
    # 并行会在 last 是 user 时静默 no-op，历史留下错误配对的 Q/A。
    answer_q = queue.Queue()

    def answer_worker():
        while True:
            text, seq, trigger = answer_q.get()
            t0 = time.time()      # API 耗时（日志复盘用）
            last_ui = {"t": 0.0}
            # 附注你的回答（自动模式）：取尚未附注过的 last_answer，拼进问题末尾
            q_text = text
            if not args.manual:
                with ans_lock:
                    if attach_on["on"] and last_answer["text"] and not last_answer["attached"]:
                        q_text = text + (f"\n【附：你此前的回答】"
                                         f"{last_answer['text'][:MY_ANSWER_MAX_CHARS]}"
                                         f"（以上是你实际口头回答，仅供参考，不是新问题）")
                        # 已附注部分砍掉，保证每段内容至多附注一次（增量累积下不重复）
                        last_answer["text"] = last_answer["text"][MY_ANSWER_MAX_CHARS:]
                        last_answer["attached"] = True
                if q_text != text:
                    print(f"📎 附注生效：问题带了你此前的回答（+{len(q_text) - len(text)}字）",
                          flush=True)

            def on_chunk(partial):
                # 节流：0.12s 内最多刷一次窗口（流式 delta 频率高，别挤爆 UI 队列）
                now = time.time()
                if now - last_ui["t"] < 0.12:
                    return
                last_ui["t"] = now
                ui("a", partial + "…")

            def should_stop():
                with st_lock:
                    return seq != epoch["n"]

            try:
                answer = agent.ask_stream(q_text, on_chunk=on_chunk,
                                          should_stop=should_stop)
            except Exception as e:
                print(f"❌ API 调用失败: {e}", flush=True)
                ui("a", f"（API 失败：{e}）")
                continue
            with st_lock:
                stale = seq != epoch["n"]
            if stale:
                agent.void_last()   # 不删历史：占位保留，追问才有上下文
                log_event({"type": "void", "question": text,
                           "partial": (answer or "").strip()[:200],
                           "api_sec": round(time.time() - t0, 2)})
                print("🗑️ 答案作废（新语音已到，历史保留）", flush=True)
                continue
            print(f"💡 答案 ({len(answer)}字)", flush=True)
            log_event({"type": "answer", "question": text, "answer": answer,
                       "api_sec": round(time.time() - t0, 2)})
            ui("a", answer)
            if push_on["on"]:
                push_answer(text, answer)

    if agent is not None:
        threading.Thread(target=answer_worker, daemon=True).start()

    # 手动录音链路（F1 开始攒、F2 结束转写 → 提问上屏 → 生成回答）
    def _manual_go(bufs):
        """F2 结束录音后：拼接音频 → 转写 → 提问上屏 → 送 answer_q"""
        try:
            audio = np.concatenate([np.frombuffer(b, dtype=np.float32) for b in bufs])
        except Exception as e:
            print(f"❌ 音频拼接失败: {e}", flush=True)
            ui("idle")
            return
        try:
            text = transcribe(audio)
        except Exception as e:
            print(f"❌ 转写出错: {e}", flush=True)
            ui("status", f"❌ 转写出错: {e}")
            ui("idle")
            return
        text = clean_asr_text(text or "")
        if not text:
            print("🔇 没识别到有效语音（环境声已丢弃）", flush=True)
            ui("status", "没识别到有效语音，可重录")
            ui("idle")
            return
        print(f"✍️ 转写: {text}", flush=True)
        with st_lock:
            seq = epoch["n"]
        log_event({"type": "question", "source": "手动", "text": text, "seq": seq})
        ui("q", text)                       # 提问先上屏（用户先确认录到了什么）
        if args.no_inject:
            print(f"🔇 [no-inject] {text}", flush=True)
            return
        answer_q.put((text, seq, "manual"))

    # ---------- 自动模式链路（默认）：双轨常开 + 门控 + 编排线程 ----------
    # 两个 PortAudio callback 只发事件（event_q），门控仲裁/VAD/双触发/作废全在编排线程串行。
    if not args.manual:
        event_q = queue.Queue()
        gate = GateState()
        recycle = deque()                # 被门控丢弃的 mic 块（回收：抢答开头不丢）
        pending = []                     # 面试官句子（16k float32 列表）
        pending_sec = 0.0                # pending 总音频秒数（上限强制发送）
        pending_since = 0.0              # 最后一次 append 的 perf_counter（B 触发计时）
        my_bufs = []                     # 你的回答 utterances（归属最近一次发送的问题）
        tq = queue.Queue()               # 转写队列：("q", audio, trigger) / ("my", audio)
        ans_lock = threading.Lock()
        last_answer = {"text": "", "attached": True}   # ans_lock 保护（转写 worker 写 / answer_worker 读）
        attach_on = {"on": AUTO_ATTACH_ON}             # F10 切换
        f9_override = {"on": False}                    # F9 按住强制解除门控
        escape_at = 0.0                # 最近一次逃生口放行时刻（期间回环 VAD 跳过）

        def ui_pending():
            ui("pending", f" · 📥 攒句 {len(pending)} 段" if pending else "")

        def on_loop_utterance(buf):
            """面试官句子 → pending（backchannel 短句丢弃）"""
            if len(buf) / SAMPLE_RATE < MIN_UTTERANCE_SEC:
                return
            nonlocal pending_sec, pending_since
            pending.append(buf)
            pending_sec += len(buf) / SAMPLE_RATE
            pending_since = time.perf_counter()
            ui_pending()

        def on_mic_utterance(buf):
            """你的回答句子 → my_bufs。攒够 MY_BATCH_SEC 音频就提交一批转写：
            长回答边讲边进上下文（不攒 10-20 分钟大音频一次转——会超时 + 卡住问题转写）。"""
            if len(buf) / SAMPLE_RATE < MIN_UTTERANCE_SEC:
                return
            my_bufs.append(buf)
            n_sec = sum(len(b) for b in my_bufs) / SAMPLE_RATE
            if n_sec >= MY_BATCH_SEC:
                tq.put(("my", np.concatenate(my_bufs)))
                my_bufs.clear()

        detector_loop = SpeechDetector(
            lambda: None,              # 快速作废走 gate.fast_onset_hit（0.4s），1.2s 确认版不用
            lambda b: event_q.put(("loop_utterance", b)))
        detector_mic = SpeechDetector(
            lambda: event_q.put(("mic_onset", None)),
            lambda b: event_q.put(("mic_utterance", b)))

        def send_question(trigger):
            """合并 pending → 转写队列。先提交 my_bufs 转写（FIFO 在前 → 附注及时），再提交问题。
            只被编排线程调用（串行），pending/my_bufs 无锁竞争。"""
            nonlocal pending, pending_sec, pending_since
            if not pending:
                return
            audio = np.concatenate(pending)
            pending, pending_sec, pending_since = [], 0.0, 0.0
            ui_pending()
            if my_bufs:
                tq.put(("my", np.concatenate(my_bufs)))
                my_bufs.clear()
            tq.put(("q", audio, trigger))

        def orchestrator():
            """单一串行处理线程：门控仲裁、VAD 喂块、双触发、打断作废。callback 只发事件。"""
            nonlocal pending_sec, pending_since, escape_at   # reset/逃生口分支要赋值（不声明会变局部）
            while True:
                try:
                    kind, obj = event_q.get(timeout=0.5)
                except queue.Empty:
                    kind = None
                try:
                    if kind == "loop_audio":
                        block, rms = obj
                        gate.feed_loop_rms(rms)
                        if gate.fast_onset_hit():
                            # 快速作废：回环连续有声 ≥0.4s（与 1.2s 句子确认解耦，打断响应快）
                            with st_lock:
                                epoch["n"] += 1
                            log_event({"type": "void_onset", "epoch": epoch["n"]})
                            print("🧠 面试官新语音…在途答案作废", flush=True)
                            set_status("🧠 面试官新语音…在途答案作废")
                        if time.perf_counter() - escape_at < ESCAPE_COOLDOWN:
                            pass   # 麦克风在收真实人声（逃生口刚放行）→ 回环此刻不是面试官，跳过 VAD
                        else:
                            detector_loop.feed(block, SAMPLE_RATE)
                    elif kind == "mic_audio":
                        block, rms = obj
                        gated = False
                        if not f9_override["on"] and gate.loop_recent():
                            # 回环在响（扬声器有回声）→ 丢弃；逃生口：mic 能量远超回声 → 放行
                            if rms <= max(gate.loop_rms * GATE_ESCAPE_RATIO, GATE_ESCAPE_ABS):
                                gated = True
                        if gated:
                            recycle.append(block)
                            maxr = int(GATE_RECYCLE_SEC * SAMPLE_RATE / BLOCK)
                            while len(recycle) > maxr:
                                recycle.popleft()
                            # 喂零块推进静音计数：面试官说话期间你的 utterance 自然断开
                            detector_mic.feed(np.zeros_like(block), SAMPLE_RATE)
                        else:
                            if recycle:          # 门控刚释放：回收缓冲先喂（抢答开头不丢）
                                for rb in recycle:
                                    detector_mic.feed(rb, SAMPLE_RATE)
                                recycle.clear()
                            detector_mic.feed(block, SAMPLE_RATE)
                            escape_at = time.perf_counter()   # 放行=麦克风在收人声→回环短暂跳过 VAD
                    elif kind == "loop_utterance":
                        on_loop_utterance(obj)
                    elif kind == "mic_onset":
                        # A 触发：你开口 = 问题边界。先 flush 回环半句并入 pending，再发送
                        flush = detector_loop.flush_now()
                        if flush is not None:
                            on_loop_utterance(flush)
                        send_question("mic_onset")
                    elif kind == "mic_utterance":
                        on_mic_utterance(obj)
                    elif kind == "reset":
                        # Ctrl+Esc 恢复：清全部状态，防陈年 pending 被 B 触发
                        detector_loop.reset()
                        detector_mic.reset()
                        pending.clear()
                        pending_sec, pending_since = 0.0, 0.0
                        my_bufs.clear()
                        recycle.clear()
                        escape_at = 0.0
                        gate.reset()
                        ui_pending()
                    elif kind == "f9_on":
                        f9_override["on"] = True
                        print("🎤 F9 按住：强制收录你的声音", flush=True)
                    elif kind == "f9_off":
                        f9_override["on"] = False
                        print("🎤 强制收录结束", flush=True)
                    # B 兜底：pending 静置 ≥B_TRIGGER_SEC（面试官陈述完你没开口）→ 发送
                    if pending and pending_since and \
                            time.perf_counter() - pending_since > B_TRIGGER_SEC:
                        send_question("silence")
                except Exception as e:
                    log_event({"type": "orchestrator_error", "err": str(e)[:200]})
                    traceback.print_exc()   # 定位用（正常无错不打印）

        threading.Thread(target=orchestrator, daemon=True).start()

        def transcribe_worker():
            """串行转写：FIFO（my 先入先转 → 附注及时）。失败不阻塞后续任务。"""
            while True:
                job = tq.get()
                kind = job[0]
                try:
                    if kind == "q":
                        text = transcribe(job[1])
                    else:
                        text = transcribe(job[1])   # 增量小批（≤MY_BATCH_SEC 音频），无需短超时
                except Exception as e:
                    log_event({"type": "transcribe_fail", "kind": kind, "err": str(e)[:200]})
                    if kind == "q":
                        print(f"❌ 问题转写出错: {e}", flush=True)
                        ui("status", f"❌ 转写出错: {e}")
                    continue
                text = clean_asr_text(text or "")
                if not text:
                    if kind == "q":
                        print("🔇 没识别到有效语音", flush=True)
                        ui("status", "没识别到有效语音")
                    continue
                if kind == "q":
                    with st_lock:
                        seq = epoch["n"]   # 采样点：转写完成、入队前（触发时取会误作废新问题）
                    log_event({"type": "question_auto", "trigger": job[2],
                               "text": text[:300], "seq": seq,
                               "audio_sec": round(len(job[1]) / SAMPLE_RATE, 1)})
                    print(f"✍️ 问题（{job[2]}）: {text}", flush=True)
                    ui("q", text)
                    answer_q.put((text, seq, job[2]))
                else:
                    with ans_lock:
                        # 增量累积：边讲边进上下文；保留最近 MY_ANSWER_TEXT_MAX 字（超出丢最旧）
                        last_answer["text"] = (last_answer["text"] + " " + text).strip()
                        if len(last_answer["text"]) > MY_ANSWER_TEXT_MAX:
                            last_answer["text"] = last_answer["text"][-MY_ANSWER_TEXT_MAX:]
                        last_answer["attached"] = False
                    log_event({"type": "my_answer", "text": text[:300]})
                    print(f"🗣 你的回答: {text[:60]}{'…' if len(text) > 60 else ''}", flush=True)
                    ui("my_answer", text)

        threading.Thread(target=transcribe_worker, daemon=True).start()

    # 音频设备（同一 PortAudio 实例：loopback + 麦克风）
    p = pyaudio.PyAudio()
    loop_idx, loop_dev = None, None
    if args.loop_tcp:
        # 面试官音频直连模式：不走声卡（VB-Audio 采集端不可用），假设备信息仅用于 WAV 落盘
        loop_dev = {"name": "面试官(TCP)", "defaultSampleRate": 44100, "maxInputChannels": 2}
    elif args.loop_device is not None:
        try:
            loop_idx = args.loop_device
            loop_dev = p.get_device_info_by_index(loop_idx)
        except OSError:
            print(f"⚠️ --loop-device {args.loop_device} 无效", flush=True)
    if not loop_dev:
        loop_idx, loop_dev = pick_loopback_device(p)
    if not loop_dev:
        sys.exit("❌ 找不到回环设备（检查默认输出设备）")
    print(f"🎧 回环: {loop_dev['name']}"
          + (f" (TCP :{args.loop_tcp})" if args.loop_tcp else f" ({int(loop_dev['defaultSampleRate'])}Hz)"),
          flush=True)

    mic_idx = mic_dev = None
    if args.mic_device is not None:
        try:
            mic_idx = args.mic_device
            mic_dev = p.get_device_info_by_index(mic_idx)
        except OSError:
            print(f"⚠️ --mic-device {args.mic_device} 无效", flush=True)
    if mic_dev is None:
        try:
            mic_idx = p.get_default_input_device_info()["index"]
            mic_dev = p.get_device_info_by_index(mic_idx)
        except OSError:
            print("⚠️ 无默认输入设备（全听模式不可用）", flush=True)
    if mic_dev:
        print(f"🎙 麦克风: {mic_dev['name']}", flush=True)

    # 自动模式：回环轨 read 线程（VB-Audio 驱动 callback 全损 0/NaN，read() 才正常）、
    # 或 TCP 直连（面试官音频不走声卡）、麦克风轨 callback（真麦克风正常）发事件给编排线程
    recorder_loop = Recorder(p, loop_idx, loop_dev, mode="tcp" if args.loop_tcp else "read",
                             on_block=(lambda b, r: event_q.put(("loop_audio", (b, r))))
                             if not args.manual else None)
    recorder_mic = Recorder(p, mic_idx, mic_dev,
                            on_block=(lambda b, r: event_q.put(("mic_audio", (b, r))))
                            if not args.manual else None)
    if args.loop_tcp:
        threading.Thread(target=loop_tcp_thread, args=(args.loop_tcp, recorder_loop),
                         daemon=True).start()

    # 全程录音落盘（复盘）：双轨原始 WAV，独立线程每 5s 刷盘 + 修补 header
    wav_writer = None
    if not args.manual:
        wav_dir = os.path.join(LOG_DIR, log.LOG_FILENAME[:-6] if log.LOG_FILENAME.endswith(".jsonl") else "rec")
        os.makedirs(wav_dir, exist_ok=True)
        wav_writer = WavWriter(
            {"interviewer": os.path.join(wav_dir, "interviewer.wav"),
             "me": os.path.join(wav_dir, "me.wav")},
            {"interviewer": recorder_loop, "me": recorder_mic})
        threading.Thread(target=wav_writer.run, daemon=True).start()
        print(f"🎙 全程录音落盘: {wav_dir}", flush=True)

    # 热键轮询（GetAsyncKeyState：F10 切模式 / F9 临时麦克风 /
    # F4 隐藏窗口 / F8 提词器模式 / F6 手机推送 / Ctrl+Esc 暂停 /
    # Alt+P 识图 / Alt+1 打字 / Alt+2 粘贴 / Alt+3 清识图记忆——笔试写码安全的组合键）
    VK_F1, VK_F2 = 0x70, 0x71
    VK_F4 = 0x73    # VK_F3 已废弃：识图收敛到 Alt+P，裸 F3 在浏览器/IDE 有默认行为不再占用
    VK_F3 = 0x72    # quiz 测评版主键：F3/P 裸键识图（code 场景废弃——f3/p 槽无人读写）
    VK_F6, VK_F8, VK_F9, VK_F10 = 0x75, 0x77, 0x78, 0x79
    VK_ESC, VK_CTRL, VK_Q = 0x1B, 0x11, 0x51
    VK_ALT = 0x12
    VK_UP, VK_DOWN = 0x26, 0x28
    hk = {"f10": False, "esc": False, "f4": False, "f8": False,
          "f6": False, "f1": False, "f2": False,
          "f9": False, "up": False, "down": False, "esc_alone": False,
          "altp": False, "alt1": False, "alt2": False, "alt3": False,
          "f3": False, "p": False, "d1": False}   # quiz 测评版槽位（code 场景无人读写）
    hidden_state = {"v": False}      # F4 窗口隐藏状态
    esc_exit_prev = 0.0              # ESC 双按退出计时（写码时 IDE 单按 ESC 极常见，见下）
    f8_last = 0.0                    # F8 防抖：连按/键盘重复不把提词器状态抖乱

    def key_down(vk):
        try:
            return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
        except Exception:
            return False

    def set_mode(m):
        with st_lock:
            state["mode"] = m
        log_event({"type": "mode", "value": m})
        print(f"🔀 模式: {'只听（只转写面试官）' if m == 'listen' else '全听（你的话也注入）'}", flush=True)
        set_status("只听模式 · F10切换" if m == "listen" else "全听模式 · F10切换")

    def hotkey_loop():
        nonlocal esc_exit_prev, f8_last   # 双按/F8 防抖计时在 main 作用域，赋值需声明
        while True:
            time.sleep(0.08)
            f10 = key_down(VK_F10)
            if f10 and not hk["f10"]:
                if args.manual:
                    with st_lock:
                        m = state["mode"]
                    set_mode("full" if m == "listen" else "listen")
                    if recorder_mic:
                        recorder_mic.start() if m == "listen" else recorder_mic.stop()
                else:
                    # 自动模式：F10 = 你的回答是否附注给模型（默认开）
                    attach_on["on"] = not attach_on["on"]
                    print(f"🔗 附注你的回答: {'开' if attach_on['on'] else '关'}", flush=True)
                    set_status(("🔗 附注开" if attach_on["on"] else "🔗 附注关") + " · F10切换")
            hk["f10"] = f10
            esc = key_down(VK_ESC)
            if esc and not hk["esc"] and key_down(VK_CTRL):
                with st_lock:
                    state["paused"] = not state["paused"]
                    paused = state["paused"]
                if paused:
                    recorder_loop.stop()
                    if recorder_mic:
                        recorder_mic.stop()
                    if not args.manual:
                        event_q.put(("reset", None))   # 暂停也清状态（防 B 触发陈年 pending）
                    print("⏸️ 已暂停（录音+生成全停）", flush=True)
                    set_status("⏸️ 已暂停 · Ctrl+Esc恢复")
                else:
                    recorder_loop.start()
                    with st_lock:
                        mic_should_run = not args.manual or state["mode"] == "full"
                    if recorder_mic and mic_should_run:
                        recorder_mic.start()
                    if not args.manual:
                        event_q.put(("reset", None))   # 清 pending/detector/门控（防陈年触发）
                    print("▶️ 已恢复", flush=True)
                    set_status("只听模式 · F10切换" if state["mode"] == "listen"
                               else "全听模式 · F10切换")
            hk["esc"] = esc
            # ESC 单独按：退出进程。quiz 版：单按即退（下方第一支，原文整段）；⚠️ code 笔试版：
            # 写码时 IDE/编辑器单按 ESC 极常见（关补全、取消弹窗）——单按退出是事故
            # （2026-09-06 实测被连杀两次），需 0.8s 内连按两次才退（第二支原文）。
            # Ctrl+Esc 仍是暂停（上面分支），Ctrl+Q 仍一键退
            esc_alone = esc and not key_down(VK_CTRL)
            if esc_alone and not hk["esc_alone"]:
                if profile.key == "quiz":   # quiz 版：ESC 单按即退（原文整段）
                    log_event({"type": "session_end", "reason": "ESC"})
                    if root is not None:
                        save_window_geometry(root.geometry())      # 记住位置，下次回到这
                    print("👋 ESC 退出", flush=True)
                    os._exit(0)
                if time.time() - esc_exit_prev < 0.8:
                    log_event({"type": "session_end", "reason": "ESC双按"})
                    if root is not None:
                        save_window_geometry(root.geometry())      # 记住位置，下次回到这
                    print("👋 ESC 双按退出", flush=True)
                    os._exit(0)
                esc_exit_prev = time.time()     # 第一下：只记时刻，0.8s 内再按才退
            hk["esc_alone"] = esc_alone
            # F1 开始录音（手动定界：录多久自己定，杜绝 VAD 误判半截问题）
            f1 = key_down(VK_F1)
            if f1 and not hk["f1"]:
                with st_lock:
                    epoch["n"] += 1          # 新一轮：在途旧答案作废
                # 手动录音两轨都攒（不区分 listen/full）：面试官问题 + 你的话都能录
                if recorder_loop:
                    recorder_loop.start_rec()
                if recorder_mic:
                    recorder_mic.start_rec()
                ui("rec_on")
                print("🎙️ 开始录音（F2 结束）", flush=True)
            hk["f1"] = f1
            # F2 结束录音：攒的音频 → 转写 → 提问上屏 → 生成回答
            f2 = key_down(VK_F2)
            if f2 and not hk["f2"]:
                bufs = []
                if recorder_loop:
                    bufs += recorder_loop.stop_rec()
                if recorder_mic:
                    bufs += recorder_mic.stop_rec()
                if not bufs:
                    ui("rec_off")
                    ui("status", "没录到内容（先按 F1 开始录音）")
                    ui("idle")
                    print("⚠️ F2 无录音内容", flush=True)
                else:
                    ui("rec_off")            # 转写中标志
                    threading.Thread(target=_manual_go, args=(bufs,), daemon=True).start()
            hk["f2"] = f2
            if profile.key == "quiz":
                # ---- quiz 测评版原文：F3/P 裸键识图 + 裸 1 自动打字 ----
                # F3 / P 截屏识图（面试官共享屏幕/测评题目截图，按一下直接出答案）
                # P 是测评场景主键：F 键在浏览器有默认行为（F3=查找栏）且部分测评页
                # 拦截 F 键；P 是普通字母键，答题不聚焦输入框时页面收不到任何副作用
                f3 = key_down(VK_F3)
                pkey = key_down(0x50)                       # VK_P
                if not VISION_STATE["busy"] and ((f3 and not hk["f3"]) or (pkey and not hk["p"])):
                    VISION_STATE["busy"] = True
                    set_status("📝 测评识图中…")
                    threading.Thread(target=do_vision, args=(ui, prompt_store), daemon=True).start()
                hk["f3"] = f3
                hk["p"] = pkey
                # 数字 1：把最新识图答案模拟真人打字打进当前焦点输入框（再按 1 = 停止）。
                # 只在有答案待打时监听——平时 1 键不劫持，答题框里正常输 1
                k1 = key_down(0x31)                     # VK_1
                if k1 and not hk["d1"]:
                    if TYPING_STATE["busy"]:
                        TYPING_STATE["stop"] = True     # 正在打：再按 1 = 停止（已打不撤销）
                    elif TYPING_STATE["armed"] and TYPING_STATE["text"]:
                        TYPING_STATE["armed"] = False
                        print("⌨️ 自动输入开始…（点好答题框光标后按 1；再按 1 停止）", flush=True)
                        threading.Thread(target=type_answer_into_foreground,
                                         args=(TYPING_STATE["text"],), daemon=True).start()
                hk["d1"] = k1
            else:
                # ---- code 笔试版原文：Alt+P/Alt+1/Alt+2/Alt+3（裸键全释放防误触发）----
                # ---- 识图/注入组合键全部收敛成 Alt+（2026-09-06 改）：裸键全释放 ----
                # 旧版裸 P 截屏、armed 时裸 1/裸 2 注入：正常敲代码时 P/数字键太常见，
                # 误触发会突然截图/突然打字/突然整段粘贴——笔试写码场景不可接受。
                # Alt+P 识图 / Alt+1 打字 / Alt+2 粘贴 / Alt+3 清记忆：正常打字永不误发。
                # 判定用「同时按下」：Alt 按住期间目标键按下即触发（Alt 松开后动作才执行）。
                pkey = key_down(0x50)                       # VK_P
                k1 = key_down(0x31)                         # VK_1
                k2 = key_down(0x32)                         # VK_2
                alt = key_down(VK_ALT)
                altp = alt and pkey
                if altp and not hk["altp"]:
                    if VISION_STATE["busy"]:
                        # 上一张还在识别中：连按 Alt+P 曾静默吞掉（无日志无提示，像「识图失败」）
                        log_event({"type": "altp_ignored", "why": "vision_busy"})
                        set_status("⏳ 上一张还在识别中，稍候…")
                    else:
                        VISION_STATE["busy"] = True
                        set_status("💻 笔试识图中…（同题续截自动带上下文）")
                        threading.Thread(target=do_vision, args=(ui, prompt_store), daemon=True).start()
                hk["altp"] = altp
                # Alt+1：把最新识图答案模拟真人打字打进当前焦点输入框（再按 Alt+1 = 停止）。
                # 只在有答案待打时生效——平时 1 键不劫持，答题框里正常输 1
                alt1 = alt and k1
                if alt1 and not hk["alt1"]:
                    log_event({"type": "alt1_press", "armed": TYPING_STATE["armed"],
                               "busy": TYPING_STATE["busy"],
                               "text_len": len(TYPING_STATE["text"] or "")})
                    if TYPING_STATE["busy"]:
                        if time.time() - TYPING_STATE["start_ts"] > 1.0:
                            TYPING_STATE["stop"] = True  # 正在打：隔 1 秒以上再按才停（1 秒内防连按误停）
                        else:
                            log_event({"type": "alt1_ignored",
                                       "why": "typing刚启动1秒内，忽略第二次按"})
                    elif TYPING_STATE["armed"] and TYPING_STATE["text"]:
                        TYPING_STATE["armed"] = False
                        print("⌨️ 自动输入开始…（松开 Alt 后自动打；框架已有行自动跳过，光标放函数体内；再按 Alt+1 停止）", flush=True)
                        threading.Thread(target=_after_alt_release,
                                         args=(type_answer_into_foreground,
                                               TYPING_STATE["text"]), daemon=True).start()
                hk["alt1"] = alt1
                # Alt+2：剪贴板粘贴整段答案（clippy 同款 paste 兜底通道）。
                # 打字链路在编辑器里吞字符/不出字时，Ctrl+V 对浏览器零抵抗力，一键换路
                alt2 = alt and k2
                if alt2 and not hk["alt2"]:
                    log_event({"type": "alt2_press", "armed": TYPING_STATE["armed"],
                               "busy": TYPING_STATE["busy"],
                               "text_len": len(TYPING_STATE["text"] or "")})
                    if TYPING_STATE["busy"]:
                        log_event({"type": "alt2_ignored", "why": "busy"})
                    elif TYPING_STATE["armed"] and TYPING_STATE["text"]:
                        TYPING_STATE["armed"] = False
                        print("📋 粘贴模式…（松开 Alt 后整段粘贴到焦点框）", flush=True)
                        threading.Thread(target=_after_alt_release,
                                         args=(paste_answer_into_foreground,),
                                         daemon=True).start()
                hk["alt2"] = alt2
                # Alt+3：清空识图多轮记忆（换新题/切题目场景时按，防旧截图旧解答污染新题）
                alt3 = alt and key_down(0x33)               # VK_3
                if alt3 and not hk["alt3"]:
                    dropped = _vis_mem_reset("Alt+3 手动清空")
                    ui("vision_reset")               # 清掉当前显示/旧答案待输入态，历史仍可回看
                    if dropped:
                        print("🧹 识图多轮记忆已清空（截图+旧解答全丢）", flush=True)
                        ui("status", "🧹 识图记忆已清空（Alt+3）")
                    else:
                        ui("status", "🧹 本就无识图记忆")
                hk["alt3"] = alt3
            # F4 隐藏/显示窗口（面试官靠近/共享屏幕时一键藏）
            f4 = key_down(VK_F4)
            if f4 and not hk["f4"] and root is not None:
                hidden_state["v"] = not bool(
                    getattr(root, "_user_hidden", hidden_state["v"]))
                root._user_hidden = hidden_state["v"]
                if hidden_state["v"]:
                    root.withdraw()
                    print("🙈 窗口已隐藏（再按 F4 显示）", flush=True)
                else:
                    root.deiconify()
                    print("👁️ 窗口已显示", flush=True)
            hk["f4"] = f4
            # ↑/↓ 当前页导航：回答页翻历史，笔记页切上一篇/下一篇。
            up = key_down(VK_UP)
            if up and not hk["up"]:
                ui("nav", -1)
            hk["up"] = up
            down = key_down(VK_DOWN)
            if down and not hk["down"]:
                ui("nav", 1)
            hk["down"] = down
            # F8 提词器模式开关（贴镜头小窗+大字滚动；切回普通窗）
            f8 = key_down(VK_F8)
            if hk["f8"] and not f8:
                now = time.time()
                if now - f8_last > 0.6:
                    f8_last = now
                    ui("tp_toggle")
            hk["f8"] = f8
            # F9 按住：自动模式强制解除门控（面试官说话期间你补充/纠正，麦克风全收）
            f9 = key_down(VK_F9)
            if f9 and not hk["f9"] and not args.manual:
                with st_lock:
                    paused = state["paused"]
                if not paused:
                    event_q.put(("f9_on", None))
            elif not f9 and hk["f9"] and not args.manual:
                event_q.put(("f9_off", None))
            hk["f9"] = f9
            # F6 手机推送开关（兜底渠道，启动常驻开）
            f6 = key_down(VK_F6)
            if f6 and not hk["f6"]:
                push_on["on"] = not push_on["on"]
                print(f"📱 手机推送: {'开' if push_on['on'] else '关'}", flush=True)
                set_status(("📱 推送开" if push_on["on"] else "推送关") + " · F6切换")
            hk["f6"] = f6
            # Ctrl+Q 一键退出（进程+窗口一起没）
            if key_down(VK_CTRL) and key_down(VK_Q):
                log_event({"type": "session_end", "reason": "Ctrl+Q"})
                if root is not None:
                    save_window_geometry(root.geometry())      # 记住位置，下次回到这
                print("👋 Ctrl+Q 退出", flush=True)
                os._exit(0)

    threading.Thread(target=hotkey_loop, daemon=True).start()

    # 启动：录音流常开（F1/F2 控制攒与不攒）；防捕获与手机推送常驻开
    recorder_loop.start()
    if recorder_mic:
        recorder_mic.start()   # 自动模式常开；手动模式默认全听，F1/F2 同时收两轨
    if not args.manual:
        print(profiles.ACTIVE.banner_auto, flush=True)   # 就绪横幅原文在 profiles（两版差异收容）
        set_status("🕶️ 防捕获开 · 📱 推送开 · 自动模式")
    else:
        print(profiles.ACTIVE.banner_manual, flush=True)   # 就绪横幅原文在 profiles（两版差异收容）
        set_status("🕶️ 防捕获开 · 📱 推送开 · 手动全听模式 · F10切换")
    print("=" * 50, flush=True)

    if root is not None:
        root.mainloop()
    else:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    log_event({"type": "session_end", "reason": "正常退出"})
    recorder_loop.stop()
    if recorder_mic:
        recorder_mic.stop()
