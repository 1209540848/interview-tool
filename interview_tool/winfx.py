# -*- coding: utf-8 -*-
"""winfx.py — win32 窗口特效/捕获排除（防截屏、磨砂玻璃、不抢焦点）。"""
import ctypes

def sample_screen_rect(x, y, w, h):
    """GDI BitBlt 采样屏幕矩形平均色（返回 (r, g, b)），不依赖 PIL。
    坑：不能采自己窗口的像素（会采到自己的旧颜色 → 反馈循环锁死），
    调用方必须给窗口外侧的矩形。"""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.GetDC.argtypes = [ctypes.c_void_p]
    user32.GetDC.restype = ctypes.c_void_p
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.BitBlt.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 4 + \
                            [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    gdi32.BitBlt.restype = ctypes.c_int
    gdi32.GetBitmapBits.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]
    gdi32.GetBitmapBits.restype = ctypes.c_long
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]

    hdc = user32.GetDC(None)              # NULL = 整个（虚拟）屏幕
    if not hdc:
        return None
    try:
        mem = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        old = gdi32.SelectObject(mem, bmp)
        gdi32.BitBlt(mem, 0, 0, w, h, hdc, x, y, 0x00CC0020)   # SRCCOPY
        bits = (ctypes.c_ubyte * (w * h * 4))()
        gdi32.GetBitmapBits(bmp, len(bits), ctypes.byref(bits))
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
    finally:
        user32.ReleaseDC(None, hdc)
    n = w * h
    rs = gs = bs = 0
    for i in range(n):        # BGRA 行序：平均色不关心顺序
        rs += bits[4 * i + 2]
        gs += bits[4 * i + 1]
        bs += bits[4 * i]
    return rs // n, gs // n, bs // n

def set_capture_excluded(root, exclude):
    """把答案窗从屏幕捕获中排除：自己照常看，截屏/录屏/共享画面里该区域空白。
    Windows 10 2004+ 官方接口（DRM 播放器同款机制），本机 19045 支持。返回是否成功。"""
    try:
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetAncestor.restype = ctypes.c_void_p
        user32.SetWindowDisplayAffinity.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        top = user32.GetAncestor(root.winfo_id(), 2) or root.winfo_id()   # GA_ROOT 拿顶层窗口
        WDA_EXCLUDEFROMCAPTURE, WDA_NONE = 0x11, 0x0
        user32.SetWindowDisplayAffinity(top, WDA_EXCLUDEFROMCAPTURE if exclude else WDA_NONE)
        return True
    except Exception as e:
        print(f"⚠️ 防捕获设置失败: {e}", flush=True)
        return False

def set_no_activate(root):
    """给答案窗挂 WS_EX_NOACTIVATE：窗口永不激活、点它也不抢焦点。
    测评/笔试页面监听 blur 记「离开页面」——答案窗每次被激活(点击/lift 瞬间)
    浏览器就失焦一次。tk 的 lift() 在 Windows 上是 SetWindowPos 且不带
    SWP_NOACTIVATE，_keep_ontop 每 300ms 抬一次就可能激活窗口 = 失焦根因。
    挂上此样式后 lift/点击都不再影响浏览器焦点。"""
    try:
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetAncestor.restype = ctypes.c_void_p
        user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_void_p   # 64 位指针宽返回值
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        top = user32.GetAncestor(root.winfo_id(), 2) or root.winfo_id()   # GA_ROOT 拿顶层窗口
        GWL_EXSTYLE, WS_EX_NOACTIVATE = -20, 0x08000000
        style = user32.GetWindowLongPtrW(top, GWL_EXSTYLE)
        user32.SetWindowLongPtrW(top, GWL_EXSTYLE, style | WS_EX_NOACTIVATE)
        return True
    except Exception as e:
        print(f"⚠️ 设置 WS_EX_NOACTIVATE 失败: {e}", flush=True)
        return False

# ---------- DWM Acrylic 磨砂玻璃背景（--acrylic 启动参数） ----------
class ACCENTPOLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_uint), ("AccentFlags", ctypes.c_uint),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_uint)]

class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p),
                ("SizeOfData", ctypes.c_size_t)]

def set_acrylic(root):
    """给答案窗挂 DWM Acrylic 磨砂玻璃（Win10 1803+ 官方 SetWindowCompositionAttribute）。
    先试 ACCENT_ENABLE_ACRYLICBLURBEHIND(4) 带中灰 tint；失败回退 ACCENT_ENABLE_BLURBEHIND(3)
    纯模糊无 tint（更稳）。返回是否成功。"""
    try:
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetAncestor.restype = ctypes.c_void_p
        user32.SetWindowCompositionAttribute.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.SetWindowCompositionAttribute.restype = ctypes.c_int
        top = user32.GetAncestor(root.winfo_id(), 2) or root.winfo_id()   # GA_ROOT 拿顶层窗口
        # AccentState=4（acrylic blur）+ AccentFlags=2（画全部边框）
        # GradientColor=0xCC8C8C8C：DWORD 是 AABBGGRR 小端，alpha=0xCC、RGB=0x8C8C8C 中灰 tint
        accent = ACCENTPOLICY(4, 2, 0xCC8C8C8C, 0)
        data = WINDOWCOMPOSITIONATTRIBDATA()
        data.Attribute = 19                  # WCA_ACCENT_POLICY
        data.Data = ctypes.cast(ctypes.byref(accent), ctypes.c_void_p)
        data.SizeOfData = ctypes.sizeof(accent)
        if user32.SetWindowCompositionAttribute(top, ctypes.byref(data)):
            return True
        accent.AccentState = 3               # 回退：纯模糊无 tint，兼容性更稳
        accent.GradientColor = 0
        if user32.SetWindowCompositionAttribute(top, ctypes.byref(data)):
            return True
        print("⚠️ Acrylic 设置失败（acrylic/blurbehind 两种 accent 都不支持）", flush=True)
        return False
    except Exception as e:
        print(f"⚠️ Acrylic 设置失败: {e}", flush=True)
        return False

# ---------- 答案小窗（tkinter 置顶，可拖动；UI 线程安全：事件队列投递） ----------
# 窗口视觉：轻微冷调的深蓝灰底板。透明度只留少量环境融合，避免文字和代码块
# 被桌面杂色冲淡；全透明/自动取色分别由独立模式负责。
BG_DARK = "#0D111A"
WIN_ALPHA = 0.92
