# -*- coding: utf-8 -*-
"""Code 版只读笔记仓库。

笔记统一放在项目根目录的 ``notes/`` 下。这里只负责安全地枚举和读取 Markdown，
不写会话日志，也不参与问答/识图 prompt。
"""
import os

from .config import BASE_DIR


NOTES_DIR = os.path.join(BASE_DIR, "notes")
NOTE_MAX_CHARS = 200_000
_MARKDOWN_EXTENSIONS = {".md", ".markdown"}


def list_markdown_notes(notes_dir=NOTES_DIR):
    """递归列出笔记目录里的 Markdown，按相对路径稳定排序。"""
    root = os.path.abspath(notes_dir)
    if not os.path.isdir(root):
        return []

    notes = []
    for dirpath, dirnames, filenames in os.walk(root):
        # 隐藏目录通常是编辑器元数据，不应出现在笔记列表里。
        dirnames[:] = [name for name in dirnames if not name.startswith(".")]
        for filename in filenames:
            if filename.startswith("."):
                continue
            if os.path.splitext(filename)[1].lower() not in _MARKDOWN_EXTENSIONS:
                continue
            path = os.path.abspath(os.path.join(dirpath, filename))
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            notes.append({"label": relative, "path": path})

    notes.sort(key=lambda item: item["label"].casefold())
    return notes


def read_markdown_note(path, notes_dir=NOTES_DIR, max_chars=NOTE_MAX_CHARS):
    """读取一篇笔记；拒绝读取笔记目录之外的路径并限制超大文件。"""
    root = os.path.realpath(os.path.abspath(notes_dir))
    target = os.path.realpath(os.path.abspath(path))
    try:
        if os.path.commonpath([root, target]) != root:
            raise ValueError("笔记路径不在 notes 目录内")
    except ValueError as exc:
        raise ValueError("无效的笔记路径") from exc

    if os.path.splitext(target)[1].lower() not in _MARKDOWN_EXTENSIONS:
        raise ValueError("只支持 Markdown 笔记")

    with open(target, "r", encoding="utf-8", errors="replace") as handle:
        content = handle.read(max_chars + 1)
    if len(content) > max_chars:
        content = content[:max_chars] + "\n\n（笔记过长，后续内容未显示）"
    return content
