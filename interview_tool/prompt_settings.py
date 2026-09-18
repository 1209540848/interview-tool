# -*- coding: utf-8 -*-
"""Prompt 场景设置窗口。

本模块只负责编辑 PromptStore，并通过 on_apply 回调通知编排层；不直接访问模型、截图记忆
或全局 Profile。
"""

from .winfx import set_capture_excluded


def open_prompt_settings(parent, prompt_store, on_apply=None):
    """打开唯一设置窗口；保存后始终只有被保存的一个场景处于活动状态。"""
    import tkinter as tk
    from tkinter import messagebox, simpledialog

    existing = getattr(parent, "_prompt_settings_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.deiconify()
                existing.lift()
                existing.focus_force()
                return existing
        except Exception:
            pass

    win = tk.Toplevel(parent)
    parent._prompt_settings_window = win
    parent._settings_open = True
    win.title("Prompt 场景设置")
    win.geometry("920x720")
    win.minsize(760, 600)
    win.attributes("-topmost", True)
    win.configure(bg="#101722")

    colors = {
        "bg": "#101722", "panel": "#151E2B", "input": "#0D141F",
        "text": "#E8ECF2", "muted": "#8D98AA", "accent": "#82AFFF",
        "danger": "#FF7B7B", "border": "#263244",
    }
    font = ("Microsoft YaHei UI", -12)
    small = ("Microsoft YaHei UI", -10)
    state = {"scene_id": None, "ids": []}

    header = tk.Frame(win, bg=colors["bg"])
    header.pack(fill="x", padx=18, pady=(16, 8))
    tk.Label(header, text="Prompt 场景", bg=colors["bg"], fg=colors["text"],
             font=(font[0], -18, "bold")).pack(side="left")
    active_label = tk.Label(header, text="", bg=colors["bg"], fg=colors["accent"],
                            font=small)
    active_label.pack(side="right")

    tk.Label(win, text="任意时刻只启用一个场景；保存后语音和截图会一起切换。",
             bg=colors["bg"], fg=colors["muted"], font=small,
             anchor="w").pack(fill="x", padx=18, pady=(0, 10))

    body = tk.Frame(win, bg=colors["bg"])
    body.pack(fill="both", expand=True, padx=18)
    left = tk.Frame(body, bg=colors["panel"], width=190,
                    highlightthickness=1, highlightbackground=colors["border"])
    left.pack(side="left", fill="y", padx=(0, 12))
    left.pack_propagate(False)
    scene_list = tk.Listbox(left, bg=colors["panel"], fg=colors["text"],
                            selectbackground="#263B5F", selectforeground=colors["accent"],
                            activestyle="none", borderwidth=0, highlightthickness=0,
                            exportselection=False, font=font)
    scene_list.pack(fill="both", expand=True, padx=8, pady=8)

    form = tk.Frame(body, bg=colors["bg"])
    form.pack(side="left", fill="both", expand=True)

    def label(text):
        tk.Label(form, text=text, bg=colors["bg"], fg=colors["muted"],
                 font=small, anchor="w").pack(fill="x", pady=(6, 3))

    label("场景名称")
    name_var = tk.StringVar()
    name_entry = tk.Entry(form, textvariable=name_var, bg=colors["input"],
                          fg=colors["text"], insertbackground=colors["text"],
                          relief="flat", font=font)
    name_entry.pack(fill="x", ipady=6)

    label("默认编程语言（题目明确指定时仍以题目为准）")
    language_var = tk.StringVar()
    language_entry = tk.Entry(form, textvariable=language_var, bg=colors["input"],
                              fg=colors["text"], insertbackground=colors["text"],
                              relief="flat", font=font)
    language_entry.pack(fill="x", ipady=6)

    label("语音回答场景 Prompt（追加到内置语音规则后）")
    voice_text = tk.Text(form, height=8, wrap="word", bg=colors["input"],
                         fg=colors["text"], insertbackground=colors["text"],
                         selectbackground="#263B5F", relief="flat", font=font,
                         padx=8, pady=8, undo=True)
    voice_text.pack(fill="both", expand=True)

    label("截图回答场景 Prompt（追加到 quiz/code 截图规则后）")
    vision_text = tk.Text(form, height=8, wrap="word", bg=colors["input"],
                          fg=colors["text"], insertbackground=colors["text"],
                          selectbackground="#263B5F", relief="flat", font=font,
                          padx=8, pady=8, undo=True)
    vision_text.pack(fill="both", expand=True)

    footer = tk.Frame(win, bg=colors["bg"])
    footer.pack(fill="x", padx=18, pady=(10, 16))
    status = tk.Label(footer, text="", bg=colors["bg"], fg=colors["muted"],
                      font=small, anchor="w")
    status.pack(side="left", fill="x", expand=True)

    def button(text, command, *, danger=False):
        widget = tk.Button(footer, text=text, command=command, relief="flat",
                           bg="#253247" if not danger else "#4A252B",
                           fg=colors["danger"] if danger else colors["text"],
                           activebackground="#31435F", activeforeground=colors["text"],
                           font=small, padx=11, pady=5, cursor="hand2")
        widget.pack(side="right", padx=(6, 0))
        return widget

    def form_value():
        return {
            "name": name_var.get(),
            "default_language": language_var.get(),
            "voice_prompt": voice_text.get("1.0", "end-1c"),
            "vision_prompt": vision_text.get("1.0", "end-1c"),
        }

    def load_scene(scene_id):
        scene = prompt_store.get_scene(scene_id)
        state["scene_id"] = scene_id
        name_var.set(scene["name"])
        language_var.set(scene["default_language"])
        voice_text.delete("1.0", "end")
        voice_text.insert("1.0", scene["voice_prompt"])
        vision_text.delete("1.0", "end")
        vision_text.insert("1.0", scene["vision_prompt"])
        reset_btn.config(state="normal" if scene["builtin"] else "disabled")
        delete_btn.config(state="disabled" if scene["builtin"] else "normal")
        status.config(text="内置场景可修改；“恢复默认”会撤销本地改动。"
                           if scene["builtin"] else "自定义场景",
                      fg=colors["muted"])

    def refresh(select_id=None):
        scenes = prompt_store.list_scenes()
        active = prompt_store.get_active_scene()
        active_label.config(text=f"当前生效：{active['name']}")
        state["ids"] = [scene["id"] for scene in scenes]
        scene_list.delete(0, "end")
        for scene in scenes:
            mark = "● " if scene["id"] == active["id"] else "  "
            scene_list.insert("end", mark + scene["name"])
        target = select_id or active["id"]
        try:
            index = state["ids"].index(target)
        except ValueError:
            index = 0
        scene_list.selection_clear(0, "end")
        scene_list.selection_set(index)
        scene_list.see(index)
        load_scene(state["ids"][index])

    def notify_applied(scene):
        active_label.config(text=f"当前生效：{scene['name']}")
        if on_apply is not None:
            on_apply(scene)

    def save_and_apply():
        try:
            scene = prompt_store.save_scene(state["scene_id"], form_value(), activate=True)
            refresh(scene["id"])
            status.config(text=f"已保存并应用：{scene['name']}", fg=colors["accent"])
            notify_applied(scene)
        except Exception as exc:
            status.config(text=f"保存失败：{exc}", fg=colors["danger"])

    def save_as_new():
        name = simpledialog.askstring("新建场景", "新场景名称：", parent=win,
                                      initialvalue=(name_var.get().strip() + " 副本"))
        if name is None:
            return
        try:
            value = form_value()
            value["name"] = name
            scene = prompt_store.create_scene(value, activate=True)
            refresh(scene["id"])
            status.config(text=f"已新建并应用：{scene['name']}", fg=colors["accent"])
            notify_applied(scene)
        except Exception as exc:
            status.config(text=f"新建失败：{exc}", fg=colors["danger"])

    def reset_builtin():
        try:
            scene = prompt_store.reset_builtin(state["scene_id"])
            refresh(scene["id"])
            status.config(text=f"已恢复并应用内置默认：{scene['name']}", fg=colors["accent"])
            notify_applied(scene)
        except Exception as exc:
            status.config(text=f"恢复失败：{exc}", fg=colors["danger"])

    def delete_custom():
        scene = prompt_store.get_scene(state["scene_id"])
        if not messagebox.askyesno("删除场景", f"确定删除“{scene['name']}”吗？", parent=win):
            return
        try:
            active = prompt_store.delete_scene(scene["id"])
            refresh(active["id"])
            status.config(text=f"已删除；当前场景：{active['name']}", fg=colors["accent"])
            notify_applied(active)
        except Exception as exc:
            status.config(text=f"删除失败：{exc}", fg=colors["danger"])

    def on_select(_event=None):
        selected = scene_list.curselection()
        if selected:
            load_scene(state["ids"][selected[0]])

    def close():
        parent._settings_open = False
        parent._prompt_settings_window = None
        win.destroy()

    button("关闭", close)
    delete_btn = button("删除", delete_custom, danger=True)
    reset_btn = button("恢复默认", reset_builtin)
    button("另存为新场景", save_as_new)
    button("保存并应用", save_and_apply)

    scene_list.bind("<<ListboxSelect>>", on_select)
    win.protocol("WM_DELETE_WINDOW", close)
    refresh()
    win.update_idletasks()
    set_capture_excluded(win, True)
    win.bind("<Map>", lambda _e: set_capture_excluded(win, True))
    win.after(50, lambda: (win.lift(), win.focus_force()))
    return win
