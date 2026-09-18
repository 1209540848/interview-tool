# -*- coding: utf-8 -*-
"""state.py — 进程级共享状态容器（原地可变 dict，无函数、无锁）。

R1 规则：这些容器只被原地写（VIEW['stage']=、hist.append 之类），任何模块 from state
import 后拿到的都是同一对象，不存在重绑定陷阱。

TYPING_STATE 取 code 版 6 键超集（含 pos/start_ts）；quiz 版 4 键是它的子集，
多出的键 quiz 侧无人读写=无害。VIS_MEM（识图多轮记忆）为 code 独有能力容器，
quiz 永不引用——可选能力在场而非缺席。
"""
import os
import threading

# ---------- 防屏幕捕获：SetWindowDisplayAffinity（WDA_EXCLUDEFROMCAPTURE） ----------
stealth = {"on": True}   # 防捕获固定常驻开启；不提供运行时关闭开关
ACRYLIC = {"on": False}  # --acrylic 启动参数：磨砂玻璃背景（DWM Acrylic）替代灰色实底。模块级同上
CHAMELEON = {"on": False}  # --chameleon 启动参数：吸窗口下方屏幕颜色做底板，文字自动深浅（变色龙）


VISION_STATE = {"busy": False}                     # 一次只截一张，防连按风暴
push_on = {"on": True}      # F6 手机推送开关（兜底渠道，常驻开）——定义在模块级：poll() 也读
                             # （原放在 main() 局部，poll 里引用抛 NameError，推送从未生效）
shot_hide = {"on": False}   # 截图离场标记：置顶窗会截进截图，模型看见「秒答/Alt+P截」字样直接拒答（2026-09-04 实测）
TYPING_STATE = {"armed": False, "busy": False, "stop": False, "paused": False,
                "text": "", "pos": 0, "start_ts": 0.0,          # 「Alt+1 自动输入」：识图答案就位→armed；busy=正在打；stop=Alt+1 信号（打字中=暂停、暂停中=继续/停止）；paused=打字挂起中（2026-09-08 新增暂停/继续）
                "auto_indent": os.environ.get("NO_AUTO_INDENT") != "1"}   # [2026-09-08 自动缩进适配] 默认开：回车后跳过答案行首空白、层级交给编辑器自动缩进；--no-auto-indent 启动参数关（记事本等无自动缩进的编辑器）
                # pos:已打字符数(断点)。新答案/完整打完→0;暂停时=已打数（续打走打字线程局部 i，pos 供状态/日志查看）
                # start_ts：打字启动时刻——1 秒内再按 Alt+1 忽略（防连按双击把刚启动的打字自杀）

# ---- 识图多轮记忆（2026-09-06）：同题续截（题目拼图/报错/测试用例）自动带上下文 ----
# 存最近 6 张历史截图(b64, q80/最长边≤1440) + 最近 4 条成功解答；Alt+3 手动清空
# 容量权衡：6 张≈「题目+样例+连续3轮报错修复」完整链条，够长题的来回改错；
# 再多每轮请求体/图 token 翻倍增长，且长尾信息模型已消化过——该按 Alt+3 换新题了
VIS_MEM = {"imgs": [], "ans": [], "generation": 0}
VIS_MEM_LOCK = threading.Lock()   # Alt+3 可与在途识图线程并发，快照/清空/回写必须原子
