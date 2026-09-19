# -*- coding: utf-8 -*-
"""config.py — interview_tool 包：路径基准 + 共享常量 + .env 读取（零依赖，仅 os）。

路径基准收敛编辑（重构 Step 1，2026-09-07）：旧单体 BASE_DIR = dirname(abspath(__file__))
恰好是仓库根（单体文件就住在根）；包化后 __file__ 指向 interview_tool/ 子目录，故推导
改为包父目录——logs/.env/resume.md/window-pos.txt 落点与旧版完全一致。
旧行已登记 parity --allow：旧 code 单体 ENV_FILE(65) / BASE_DIR(67) 行。

死代码核销（Step 1，census 三份源均确认）：READ_POLL_SECONDS / PENDING_MAX_SEC
在 quiz/code/api 里都只有定义、零引用（API 版注释自称「兼容保留」），不再搬入。
"""
import os

# 收敛编辑：包父目录 = 仓库根（旧版单层 dirname 会指到 interview_tool/ 自身）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(BASE_DIR, ".env")

# ---------- 配置 ----------
SAMPLE_RATE = 16000          # ASR 标准采样率（ISI/DashScope 都是 16k）
BLOCK = 1024                 # 录音块大小（帧）


# ---------- 自动模式（默认）：门控 / 断句 / 触发参数 ----------
GATE_RMS_THR = 0.003         # 门控快速 RMS 阈值。低于 VAD 阈值 0.008：轻声/回声也要门住
ESCAPE_COOLDOWN = 0.6        # 逃生口放行后回环 VAD 跳过时长（放行=麦克风在收人声→回环不是面试官）
GATE_SILENCE_BLOCKS = 20     # 回环连续低 RMS 块数 → 判定静音（衰减释放，~0.43s@48k）
GATE_ESCAPE_RATIO = 3.0      # 逃生口：mic RMS 超过回环回声水平 N 倍 → 用户开口放行
GATE_ESCAPE_ABS = 0.02       # 逃生口绝对阈值：mic RMS 至少要达到（防噪声放大）
GATE_RECYCLE_SEC = 0.4       # 被门控丢弃的麦克风块保留最近 N 秒（抢答开头回收）
GATE_FAST_ONSET_SEC = 0.4    # 回环快速 onset 持续 N 秒 → epoch++（打断 0.4s 内作废）
GATE_FAST_ONSET_MIN_GAP = 1.0  # 两次快速 onset 最小间隔（防持续说话反复触发）
MIN_UTTERANCE_SEC = 0.8      # utterance 低于此秒数丢弃（backchannel"对对对"）


B_TRIGGER_SEC = 2.5          # 兜底触发：pending 最后一次追加后静置 N 秒 → 发送
MY_ANSWER_MAX_CHARS = 4000   # 附注进 prompt 的截断长度（长回答取最近 4000 字）
MY_ANSWER_TEXT_MAX = 8000    # 你的回答累积文本上限（超出丢最旧，防无限增长）
MY_BATCH_SEC = 15            # 你的回答增量转写批量：攒够 ~15s 音频提交一次转写
RESUME_MAX_CHARS = 1500      # resume.md 注入 system prompt 的截断长度
RESUME_FILE = os.path.join(BASE_DIR, "resume.md")
AUTO_ATTACH_ON = True        # 自动模式默认附注你的回答（F10 切换）
# VAD 攒句参数（自最早原版单体移植）
VAD_RMS_THR = 0.008          # 回环"开口"阈值：静音基线极低(~0.001)，语音明显更高
MIN_SPEECH = 1.2             # 语音持续 ≥1.2s 才开始攒（滤咳嗽/短插话）
END_SILENCE = 0.9            # 停顿 ≥0.9s 视为句子完成
MAX_UTTERANCE = 90           # 单句上限（秒）

# ---------- 面试日志（每场一个 JSONL：问题/答案/作废/模式全记录，复盘用） ----------
LOG_DIR = os.path.join(BASE_DIR, "logs")


def _clean_env_value(raw):
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] in ('"', "'"):
        quote = raw[0]
        end = raw.find(quote, 1)
        if end != -1:
            return raw[1:end].strip()
        return raw.strip(quote).strip()
    return raw.split("#", 1)[0].strip()


def _env_get(var):
    val = os.environ.get(var, "").strip()
    if val:
        return val
    found = ""
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(var + "="):
                    value = _clean_env_value(line.split("=", 1)[1])
                    if value:
                        found = value
    except OSError:
        pass
    return found


def _env_bool(var, default=False):
    """读取常见布尔配置；空值使用默认值，非法值给出提示后回退。"""
    raw = _env_get(var).strip().lower()
    if not raw:
        return bool(default)
    if raw in ("1", "true", "yes", "on", "enabled"):
        return True
    if raw in ("0", "false", "no", "off", "disabled"):
        return False
    print(f"⚠️ {var} 仅支持 true/false，已使用默认值 {bool(default)}", flush=True)
    return bool(default)
