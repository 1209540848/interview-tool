# -*- coding: utf-8 -*-
"""vision.py — 截屏识图（火山豆包视觉 / 百炼 qwen-vl 兼容两家接口）。

共享主本取 code 版（code ⊃ quiz）：_ask_vision_multi 多图核心 + VIS_MEM 多轮记忆助手
为 code 独有能力，quiz 永不引用（可选能力在场）。VISION_MODEL/BASE_URL/FALLBACK_URL
两版逐字相同故留在本模块。VISION_PROMPT/VISION_MAX_TOKENS/答崩提示按键名两版文本
不同 → 已收进 profiles.py 的 Profile 字段（vision_prompt/vision_max_tokens/vision_retry）；
技术方向由 engine 注入的 PromptStore 在每次截图时追加，和运行模式保持解耦。
do_vision 统一主本（code 版；quiz 问图段更简，同构差异收敛到 profiles）。"""
from .config import _env_get
from .log import log_event
from .state import VIS_MEM, VIS_MEM_LOCK, VISION_STATE
from . import profiles          # quiz/code 问图协议取 ACTIVE；技术场景由 PromptStore 参数注入

# ---------- Alt+P 截屏识图（手撕代码场景：面试官共享屏幕出题 / 笔试 OJ 截图直接出答案） ----------
# 兼容两家 OpenAI 风格接口，用 .env 三件套切换，不用改代码：
#   ARK_API_KEY        = 识图 API key
#   ARK_VISION_MODEL   = 模型名（火山 doubao-1.5-vision-lite-250315 / 百炼 qwen-vl-plus）
#   VISION_BASE_URL    = 接口地址（默认火山 chat/completions；百炼填
#                        https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions）
# 冗余模型选择（2026-09-09）：主模型失败自动切备用——备用三件套 BACKUP_VISION_API_KEY /
# BACKUP_VISION_MODEL / BACKUP_VISION_BASE_URL，key/url 缺省回退主的值（见 _vision_providers）
VISION_MODEL = "doubao-1.5-vision-lite-250315"     # 默认火山豆包轻量视觉（便宜快）
VISION_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
VISION_FALLBACK_URL = "https://ark.cn-beijing.volces.com/api/v3/responses"
# VISION_PROMPT / VISION_MAX_TOKENS / 答崩提示按键名已收敛 → profiles.ACTIVE.vision_*
# （两版文本不同，删 const 防双抄漂移；code 文本现值见 profiles.CODE.vision_prompt）


def _vis_mem_note(img_pil, ans, expected_generation=None):
    """识图答成后记一笔：当前截图压 q80/≤1440 存 b64，答案截前 4000 字；
    截图留最近 6 张、解答留最近 4 条（丢最旧——题目图若被挤掉，续截时补一张即可）。
    答崩（❌）不记——失败历史喂回去只会带偏下一轮"""
    try:
        import io as _io
        import base64 as _b64
        from PIL import Image as _PImage
        w, h = img_pil.size
        sc = min(1.0, 1440.0 / max(w, h))
        if sc < 1.0:
            img_pil = img_pil.resize((max(1, int(w * sc)), max(1, int(h * sc))),
                                     resample=_PImage.LANCZOS)
        buf = _io.BytesIO()
        img_pil.convert("RGB").save(buf, "JPEG", quality=80)
        a = (ans or "").strip()
        encoded = _b64.b64encode(buf.getvalue()).decode()
        with VIS_MEM_LOCK:
            if (expected_generation is not None
                    and VIS_MEM["generation"] != expected_generation):
                return False                     # Alt+3 已换题：旧请求禁止重新污染记忆
            VIS_MEM["imgs"].append(encoded)
            if len(VIS_MEM["imgs"]) > 6:
                del VIS_MEM["imgs"][0]
            if a:
                VIS_MEM["ans"].append(a[:4000])
                if len(VIS_MEM["ans"]) > 4:
                    del VIS_MEM["ans"][0]
        return True
    except Exception:
        return False                            # 记忆失败不影响主链路

def _vis_mem_reset(why=""):
    """换新题时清空多轮记忆（Alt+3 触发），防旧题截图/旧解答污染新题。返回是否真丢了内容"""
    with VIS_MEM_LOCK:
        n_img, n_ans = len(VIS_MEM["imgs"]), len(VIS_MEM["ans"])
        VIS_MEM["imgs"] = []
        VIS_MEM["ans"] = []
        VIS_MEM["generation"] += 1              # 让 Alt+3 前已发出的请求结果自动作废
    try:
        log_event({"type": "vis_mem_reset", "why": why,
                   "dropped_imgs": n_img, "dropped_ans": n_ans})
    except Exception:
        pass
    return (n_img + n_ans) > 0

def _vis_mem_parts():
    """历史截图 → API parts 列表（旧→新；主流程把最新截图追加在后）"""
    with VIS_MEM_LOCK:
        return [{"b64": b} for b in VIS_MEM["imgs"]]

def _vis_mem_prompt_suffix():
    """此前成功解答 → prompt 后缀：告诉模型这是上一轮自己给的答案，报错修复/继续优化对照用"""
    with VIS_MEM_LOCK:
        answers = list(VIS_MEM["ans"])
    if not answers:
        return ""
    s = "\n\n【此前解答】（我上一轮给出的回答——报错修复或继续优化时以此为基础改，供对照）：\n"
    for i, a in enumerate(answers, 1):
        s += f"——第 {i} 轮解答——\n{a}\n"
    return s


def _vis_mem_snapshot():
    """为一次识图请求冻结同一代的历史，避免 Alt+3 与重试阶段交叉混用。"""
    with VIS_MEM_LOCK:
        generation = VIS_MEM["generation"]
        parts = [{"b64": b} for b in VIS_MEM["imgs"]]
        answers = list(VIS_MEM["ans"])
    suffix = ""
    if answers:
        suffix = "\n\n【此前解答】（我上一轮给出的回答——报错修复或继续优化时以此为基础改，供对照）：\n"
        for i, answer in enumerate(answers, 1):
            suffix += f"——第 {i} 轮解答——\n{answer}\n"
    return generation, parts, suffix, len(parts)


def _vision_parse(j):
    """解析 chat/completions / responses 两种响应格式，返回文本"""
    try:                                            # chat/completions 格式（主流）
        c = j["choices"][0]["message"]
        if c.get("content"):
            return c["content"]
        rc = c.get("reasoning_content") or ""       # content 空：只认「非英文思考流」的兜底
        if rc and not rc.lstrip().startswith(("The user", "Let me", "We need", "I need", "Okay")):
            return rc
        return None                                 # 纯思考流（deepseek-v4 实测）→ 当没答案，交给上层报清晰错误
    except Exception:
        try:                                        # responses 格式（火山新接口）
            texts = []
            for o in j.get("output", []):
                if not isinstance(o, dict) or o.get("type") == "reasoning":
                    continue        # seed-2.1 推理块排在 output[0]：思考过程不是答案，跳过
                for c in o.get("content", []):
                    if isinstance(c, dict):
                        texts.append(c.get("text", ""))
            return "".join(texts)
        except Exception:
            return None

def _crop_center_zoom(img, fx=1.8):
    """裁屏幕中央 60% 宽 × 75% 高（题目主体一般在中间偏上）再放大 fx 倍——
    小图形/小数字整图里看不清，裁出来放大后模型才认得（穷替版 VisualCoT）"""
    from PIL import Image
    w, h = img.size
    cw, ch = int(w * 0.6), int(h * 0.75)
    x0, y0 = (w - cw) // 2, int(h * 0.08)           # 中央略偏上：题目区一般在中上部
    crop = img.crop((x0, y0, x0 + cw, y0 + ch))
    return crop.resize((max(1, int(cw * fx)), max(1, int(ch * fx))), resample=Image.LANCZOS)

def _ask_vision_multi(key, model, url, parts, prompt, max_tokens):
    """多图识图核心（2026-09-06 多轮记忆的基础，单请求多图已实测支持）：
    parts 每项是 PIL.Image 或 {"b64": 已编码字符串}，按旧→新排列，最后一张最新；
    一次性全喂——同题续截（拼图/报错）模型自己对照，省一次往返。
    返回 (答案文本 or None, HTTP 状态码)；chat/completions 格式被拒(400)时自动回退 responses 格式"""
    import io as _io
    import base64 as _b64
    import requests
    b64s = []
    for p in parts:
        if isinstance(p, dict) and p.get("b64"):
            b64s.append(p["b64"])
        else:
            buf = _io.BytesIO()
            p.convert("RGB").save(buf, "JPEG", quality=92)  # q92：小图形细节不再压糊
            b64s.append(_b64.b64encode(buf.getvalue()).decode())
    hdr = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    imgs = [{"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{b}"}} for b in b64s]
    body = {"model": model, "messages": [{"role": "user",
            "content": imgs + [{"type": "text", "text": prompt}]}], "max_tokens": max_tokens}
    thinking = (_env_get("VISION_THINKING") or "").lower()
    if thinking in ("enabled", "disabled", "auto"):
        body["thinking"] = {"type": thinking}
    # 超时 (15, 120)：多轮记忆大请求 + 新模型首 token 慢，60s 单值实测被
    # HTTPConnectionPool 超时打爆（2026-09-12 用户高频复现）——对齐 chat.py 写法
    r = requests.post(url, headers=hdr, json=body, timeout=(15, 120))
    if r.status_code == 200:
        return _vision_parse(r.json()), 200
    if r.status_code in (400, 404):                 # chat 格式/模型入口被拒 → 回退 responses 格式
        body2 = {"model": model, "input": [{"role": "user", "content":
            [{"type": "input_image",
              "image_url": f"data:image/jpeg;base64,{b}"} for b in b64s] +
            [{"type": "input_text", "text": prompt}]}]}
        if thinking in ("enabled", "disabled", "auto"):
            body2["thinking"] = {"type": thinking}
        r2 = requests.post(VISION_FALLBACK_URL, headers=hdr, json=body2, timeout=(15, 120))
        if r2.status_code == 200:
            return _vision_parse(r2.json()), 200
        return None, r2.status_code
    return None, r.status_code

def _ask_vision_once(key, model, url, img, prompt, max_tokens):
    """单张 PIL 图入口（保留旧签名兼容）→ 走多图核心"""
    return _ask_vision_multi(key, model, url, [img], prompt, max_tokens)

def _ask_vision_retry(key, model, url, parts, prompt, max_tokens):
    """识图带一次自动重试：HTTPConnectionPool 超时（大图+多轮记忆请求重、模型端
    慢/不稳）是「识图异常」最常见根因，重试一次多半能成；重试仍超时再抛给上层"""
    import requests
    for attempt in (1, 2):
        try:
            return _ask_vision_multi(key, model, url, parts, prompt, max_tokens)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt == 2:
                raise
            log_event({"type": "vision_retry", "why": type(e).__name__,
                       "err": str(e)[:120]})

def _vision_providers():
    """[2026-09-09 冗余模型选择] 主+备用视觉模型拨号组：主失败自动切备用。
    备用三件套缺省回退链——key/url 没填就复用主的：同服务商多模型场景（如
    doubao-seed-evolving 主 + doubao-vision-pro 备）只需一行 BACKUP_VISION_MODEL 即启用；
    跨服务商（备百炼 qwen-vl）则 BACKUP_VISION_API_KEY/MODEL/BASE_URL 全填。
    返回 [(tag, key, model, url), ...]；BACKUP_VISION_MODEL 没配 → 列表只有主（行为同旧版）"""
    key0 = _env_get("ARK_API_KEY")
    model0 = _env_get("ARK_VISION_MODEL") or VISION_MODEL   # 可在 .env 覆盖模型名
    url0 = _env_get("VISION_BASE_URL") or VISION_BASE_URL   # 可在 .env 换服务商
    provs = [("main", key0, model0, url0)]
    b_model = _env_get("BACKUP_VISION_MODEL")
    if b_model:
        b_key = _env_get("BACKUP_VISION_API_KEY") or key0   # 备用 key 缺省复用主 key（同服务商）
        b_url = _env_get("BACKUP_VISION_BASE_URL") or url0  # 备用 url 缺省复用主 url（同上）
        if b_key:                               # 备用有模型名但 key 拿不到 → 只有主（主会报缺 key 提示）
            provs.append(("backup", b_key, b_model, b_url))
    return provs

def do_vision(ui, prompt_store=None):
    """Alt+P 识图主流程（后台线程）：全屏截图 →（自动带上同题历史截图+上次解答）→ 识图 API →
    答案窗显示。答成记入多轮记忆；识别失败走冗余链路（2026-09-09）：主模型整图失败 →
    放大重试 → 仍失败自动切备用模型（同一份截图与上下文）→ 备用也放大 → 全失败才报错，
    任何时候都不打断主链路"""
    try:
        key = _env_get("ARK_API_KEY")
        if not key:
            ui("status", "❌ 缺 ARK_API_KEY：.env 里填识图 API key")
            return
        providers = _vision_providers()         # 主+备用拨号组（备用未配则只有主，行为同旧版）
        from PIL import Image, ImageGrab
        import time as _time
        t0 = _time.time()
        ui("ov_ctl", "hide")                        # 置顶窗先离场：它会被截进图，模型看见「Alt+P截」等字样拒答
        try:
            _time.sleep(0.35)                       # 等 Tk 主线程 withdraw + 合成一帧
            img = ImageGrab.grab()                  # 全屏
        finally:
            ui("ov_ctl", "restore")                 # 截完立刻回来（不等 API，闪感最小）
        w, h = img.size
        scale = min(1.0, 1920.0 / max(w, h))        # 最长边压到 1920（看清小图形细节）
        if scale < 1.0:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                             resample=Image.LANCZOS)
        # 多轮记忆与代号一起冻结：Alt+3 后旧请求即使晚回来，也不能重新显示/写回。
        mem_generation, mem_parts, mem_suffix, mem_n = _vis_mem_snapshot()
        parts = mem_parts + [img]
        scene_revision = None
        scene_id = profiles.ACTIVE.key
        if prompt_store is not None:
            prompt, scene, scene_revision = prompt_store.resolve_vision_prompt(
                profiles.ACTIVE.vision_prompt)
            scene_id = scene["id"]
        else:
            prompt = profiles.ACTIVE.vision_prompt
        prompt += mem_suffix
        ans = None
        used_tag = "main"                       # 实际答出答案的模型 tag（日志复盘哪个模型救场）
        for tag, pkey, pmodel, purl in providers:
            if tag == "backup":                 # [2026-09-09] 主链路两枪都空才轮询到备用：提示切换
                ui("status", "🔁 主视觉模型没答出，自动切换备用模型重看中…")
            ans, st = _ask_vision_retry(pkey, pmodel, purl, parts, prompt,
                                        profiles.ACTIVE.vision_max_tokens)
            if ans:
                if tag == "backup":             # 备用整图直接答出：答案带前缀，用户可感知救场
                    ans = f"（备用视觉模型作答）\n{ans}"
                used_tag = tag
                break
            ui("status", "🔍 整图没认出，放大题目重看中…")
            zoom = _crop_center_zoom(img)
            ans2, st2 = _ask_vision_retry(pkey, pmodel, purl, mem_parts + [zoom],
                                          prompt, profiles.ACTIVE.vision_max_tokens)
            if ans2:
                ans = f"（整图没答出，放大重看）\n{ans2}"
                used_tag = tag
                break
        if not ans:                                 # 所有模型整图+放大全空 → 报错收尾
            ans = (f"❌ 视觉模型都没答出来（整图 HTTP {st} / 放大 HTTP {st2}），"
                   f"重按 {profiles.ACTIVE.vision_retry} 截一次或语音问我")
        with VIS_MEM_LOCK:
            memory_unchanged = VIS_MEM["generation"] == mem_generation
        scene_unchanged = (prompt_store is None
                           or prompt_store.revision == scene_revision)
        request_current = memory_unchanged and scene_unchanged
        if ans and not ans.startswith("❌") and request_current:
            _vis_mem_note(img, ans, mem_generation)  # 答成才记；代号变化则静默拒绝旧回写
        # 显示事件也在同一把锁里入队：若 Alt+3 先拿到锁，旧结果不入队；若旧结果
        # 先入队，Alt+3 的 vision_reset 必然排在它后面，最终画面仍保持清空。
        with VIS_MEM_LOCK:
            memory_unchanged = VIS_MEM["generation"] == mem_generation
            current_mem_ans = len(VIS_MEM["ans"])
            request_current = memory_unchanged and scene_unchanged
            if request_current:
                ui("vision", ans)
        log_event({"type": "vision", "ok": bool(ans and not ans.startswith("❌")),
                   "provider": used_tag,            # [2026-09-09] 实际答出的是主还是备用
                   "err": None if (ans and not ans.startswith("❌")) else (ans or "")[:200],
                    "answer": ans[:2000], "ans_len": len(ans or ""),
                    "truncated": bool(ans) and len(ans) > 2000,
                    "prompt_scene": scene_id,
                    "scene_unchanged": scene_unchanged,
                    "memory_generation": mem_generation,
                    "displayed": request_current,
                    "mem_imgs": mem_n, "mem_ans": current_mem_ans,
                    "api_sec": round(_time.time() - t0, 2)})
    except Exception as e:
        # 2026-09-12 起落盘：原只弹状态栏，静默启动下「识图失败」在日志里零痕迹
        log_event({"type": "vision_error", "err": f"{type(e).__name__}: {e}"[:200]})
        ui("status", f"❌ 识图异常: {e}")
    finally:
        VISION_STATE["busy"] = False
