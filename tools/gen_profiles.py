#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_profiles.py — 从两个旧单体提取双场景差异 → 生成 interview_tool/profiles.py

profiles.py 收容"共享主本内无法收敛"的全部场景差异（quiz 测评版 / code 笔试版）：
  * 文本字段（程序化提取保证逐字，杜绝手抄错字）：
      system_prompt / vision_prompt / vision_max_tokens / vision_retry /
      banner_auto / banner_manual —— 从旧单体顶层常量节点与 main() 就绪横幅
      经字符串折叠（Constant 直取 / JoinedStr 全常量拼接 / + 链递归）取文本
  * 窗口差异方法（源码行切片嵌入，替换点登记在案）：
      place_window        —— 窗口初始几何（quiz 560x160 顶中 / code 恢复上次或 760x460）
      mount_window_extra  —— code 缩放手柄原文（2026-09-13 起 quiz 共用同一份）

安全：切片/提取前校验锚点文本，源文件漂移即中止 → 生成器可放心重跑。
用法：python tools/gen_profiles.py
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUIZ = os.path.join(ROOT, "interview-cheat-quiz.py")
CODE = os.path.join(ROOT, "interview-cheat-code.py")
OUT = os.path.join(ROOT, "interview_tool", "profiles.py")


def read_lines(path):
    with open(path, encoding="utf-8") as f:
        return f.read().splitlines()


def fold(node):
    """字符串常量折叠 → 文本。含插值/动态表达式即抛错（这类文本不该进 profile）"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = node.values
        if all(isinstance(p, ast.Constant) and isinstance(p.value, str) for p in parts):
            return "".join(p.value for p in parts)
        raise ValueError(f"JoinedStr 含插值: {ast.dump(node)[:100]}")
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return fold(node.left) + fold(node.right)
    raise ValueError(f"无法折叠: {type(node).__name__} {ast.dump(node)[:100]}")


def top_assign(path, name, want_int=False):
    """模块顶层 Assign target==name 的值折叠（取首个命中）"""
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    v = node.value
                    if want_int:
                        if isinstance(v, ast.Constant) and isinstance(v.value, int):
                            return v.value
                        raise SystemExit(f"{path}: {name} 不是整型常量")
                    return fold(v)
    raise SystemExit(f"{path}: 找不到顶层 {name}")


def banner_texts(path):
    """main() 里 '✅ 就绪（自动模式）/（手动模式）' 两条横幅 print 的文本"""
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "main")
    texts = {}
    for node in ast.walk(fn):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "print" and node.value.args):
            try:
                t = fold(node.value.args[0])
            except ValueError:
                continue
            if "✅ 就绪（自动模式）" in t:
                texts["auto"] = t
            elif "✅ 就绪（手动模式）" in t:
                texts["manual"] = t
    if not (texts.get("auto") and texts.get("manual")):
        raise SystemExit(f"{path}: 就绪横幅定位失败: {sorted(texts)}")
    return texts["auto"], texts["manual"]


def slice_restrip(lines, start, end):
    """1-based 闭区间行切片，剥公共缩进 → 行列表（空行留空）"""
    chunk = lines[start - 1:end]
    inds = [len(l) - len(l.lstrip()) for l in chunk if l.strip()]
    cut = min(inds)
    return [l[cut:] if l.strip() else "" for l in chunk]


def indent_block(lines, pad):
    return "\n".join((pad + l) if l.strip() else "" for l in lines)


# ---------- 锚点断言（行号漂移即中止；文本 = 该行的实际内容） ----------
quiz_l = read_lines(QUIZ)
code_l = read_lines(CODE)
ANCHORS = [
    (quiz_l, 986, "    # 初始化：水平居中、垂直顶边贴屏幕最上（用户要求）；之后可正常拖动"),
    (quiz_l, 987, "    _sw = root.winfo_screenwidth()"),
    (quiz_l, 988, '    root.geometry(f"560x160+{(_sw - 560) // 2}+0")'),
    (code_l, 992, "    # 初始化：水平居中、垂直顶边贴屏幕最上（用户要求）；之后可正常拖动"),
    (code_l, 998, '        root.geometry(f"760x460+{(_sw - 760) // 2}+0")   # 笔试版默认放大：思路+代码一屏起步'),
    (code_l, 1131, "    # 右下角缩放柄：无边框窗没有系统缩放手柄，长代码答案拉大窗口看（最小 300x160）。"),
    (code_l, 1149, '    grip.bind("<B1-Motion>", lambda e: (on_grip_move(e), "break")[1])'),
]
for lines, ln, want in ANCHORS:
    if lines[ln - 1] != want:
        raise SystemExit(f"锚点失配 L{ln}:\n  期望: {want}\n  实际: {lines[ln - 1]}")

# ---------- 文本字段提取 ----------
sys_p = top_assign(QUIZ, "SYSTEM_PROMPT"), top_assign(CODE, "SYSTEM_PROMPT")
vis_p = top_assign(QUIZ, "VISION_PROMPT"), top_assign(CODE, "VISION_PROMPT")
vis_m = top_assign(QUIZ, "VISION_MAX_TOKENS", True), top_assign(CODE, "VISION_MAX_TOKENS", True)
ba_q, bm_q = banner_texts(QUIZ)
ba_c, bm_c = banner_texts(CODE)

# 防捕获现为不可关闭的常驻能力；旧单体横幅仍写着 F7/F6 均可关闭，生成时收敛掉 F7。
_old_switch_note = "🕶️ 防捕获常驻开，📱 手机推送常驻开（F7/F6 可关）。"
_fixed_switch_note = "🕶️ 防捕获常驻开，📱 手机推送常驻开（F6 可关）。"

def fixed_capture_banner(name, value):
    if _old_switch_note not in value:
        raise SystemExit(f"{name}: 找不到旧 F7 横幅文本，源文件可能已漂移")
    return value.replace(_old_switch_note, _fixed_switch_note)

ba_q = fixed_capture_banner("ba_q", ba_q)
bm_q = fixed_capture_banner("bm_q", bm_q)
ba_c = fixed_capture_banner("ba_c", ba_c)
bm_c = fixed_capture_banner("bm_c", bm_c)

# ---------- 窗口差异方法体切片（替换点登记） ----------
# place_window body：quiz = 986-988 三行（注释+取屏宽+单行几何）；code = 992-998 七行
# （注释+取屏宽+恢复上次位置，无则 760x460 默认放大窗）。_sw 方法内自算，调用方只传 root。
body_place_q = slice_restrip(quiz_l, 986, 988)
body_place_c = slice_restrip(code_l, 992, 998)
# mount_window_extra body：code = 1131-1149 缩放手柄原文。登记替换：
#   局部 import tkinter（profiles 零包内 import，与原 show_answer_window 同款局部 import）
#   FAM→fam / BG_DARK→bg（方法参数承接，不再读模块常量）
body_mount_c = slice_restrip(code_l, 1131, 1149)
body_mount_c.insert(0, "import tkinter as tk   # 局部 import：与旧 show_answer_window 同款")
body_mount_c = [l.replace("FAM", "fam").replace("BG_DARK", "bg") for l in body_mount_c]

# ---------- 渲染 ----------
def r(s):
    return repr(s)


def cls_body(clsname, flavor_note, place_note, body_place, mount_note, body_mount):
    return f'''class {clsname}(Profile):
    """{flavor_note}"""

    def place_window(self, root, load_window_geometry):
        """{place_note}"""
{indent_block(body_place, " " * 8)}

    def mount_window_extra(self, root, fam, bg):
        """{mount_note}"""
{indent_block(body_mount, " " * 8)}
'''


doc = """# -*- coding: utf-8 -*-
\"\"\"profiles.py — 双场景差异数据与分支函数（quiz 测评版 / code 笔试版）。

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
\"\"\"
"""

texts = f'''
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


{cls_body("QuizProfile", "quiz 测评版：560x160 小窗顶中，右下角可拉大看长答案",
          "quiz 版：水平居中、垂直顶边贴屏幕最上（用户要求）；之后可正常拖动",
          body_place_q,
          "quiz 版：右下角缩放柄与 code 版共用（2026-09-13 起：长答案也要拉大窗口看）",
          body_mount_c)}
{cls_body("CodeProfile", "code 笔试版：恢复上次窗口或 760x460 大窗，带右下角缩放手柄",
          "code 版：重启回到上次的尺寸+位置（560x160 代码根本排不下）；无记录则 760x460 默认放大",
          body_place_c,
          "code 版：右下角缩放柄原文——长代码答案拉大窗口看（最小 300x160）",
          body_mount_c)}
QUIZ = QuizProfile(key="quiz", system_prompt={r(sys_p[0])},
                   vision_prompt={r(vis_p[0])}, vision_max_tokens={vis_m[0]},
                   vision_retry={r("P")}, banner_auto={r(ba_q)}, banner_manual={r(bm_q)})
CODE = CodeProfile(key="code", system_prompt={r(sys_p[1])},
                   vision_prompt={r(vis_p[1])}, vision_max_tokens={vis_m[1]},
                   vision_retry={r("Alt+P")}, banner_auto={r(ba_c)}, banner_manual={r(bm_c)})

ACTIVE = None       # engine.main(profile) 首行赋值；其余模块一律经 profiles.ACTIVE 取用
'''

content = doc + texts
with open(OUT, "w", encoding="utf-8") as f:
    f.write(content)
print(f"profiles.py 已生成: {OUT} ({len(content.splitlines())} 行)")
