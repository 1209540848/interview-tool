# -*- coding: utf-8 -*-
"""单活动场景的持久化与 Prompt 组合。

模块不依赖 Tk、模型客户端或截图实现。调用方只拿不可变快照/最终文本，因此设置界面、
语音 Agent 和视觉链路可以独立演进。
"""
import copy
import json
import os
import tempfile
import threading
import uuid

from .config import BASE_DIR
from .prompt_presets import BUILTIN_SCENES, DEFAULT_SCENE_ID


CONFIG_VERSION = 1
SCENE_CONFIG_FILE = os.path.join(BASE_DIR, "prompt-scenes.json")
MAX_NAME_CHARS = 80
MAX_PROMPT_CHARS = 20000
MAX_LANGUAGE_CHARS = 120
_FIELDS = ("name", "default_language", "voice_prompt", "vision_prompt")


class PromptStoreError(ValueError):
    pass


def _clean_text(value, *, field, limit, required=False):
    if not isinstance(value, str):
        raise PromptStoreError(f"{field} 必须是文本")
    value = value.strip()
    if required and not value:
        raise PromptStoreError(f"{field} 不能为空")
    if len(value) > limit:
        raise PromptStoreError(f"{field} 不能超过 {limit} 字")
    return value


def _validate_scene(scene):
    if not isinstance(scene, dict):
        raise PromptStoreError("场景格式无效")
    return {
        "name": _clean_text(scene.get("name", ""), field="场景名称",
                            limit=MAX_NAME_CHARS, required=True),
        "default_language": _clean_text(scene.get("default_language", ""),
                                        field="默认语言", limit=MAX_LANGUAGE_CHARS),
        "voice_prompt": _clean_text(scene.get("voice_prompt", ""),
                                    field="语音 Prompt", limit=MAX_PROMPT_CHARS),
        "vision_prompt": _clean_text(scene.get("vision_prompt", ""),
                                     field="截图 Prompt", limit=MAX_PROMPT_CHARS),
    }


class PromptStore:
    """线程安全的场景仓库；任何时刻只有 active_scene_id 指向的一个场景生效。"""

    def __init__(self, path=SCENE_CONFIG_FILE):
        self.path = os.path.abspath(path)
        self._lock = threading.RLock()
        self._active_id = DEFAULT_SCENE_ID
        self._builtin_overrides = {}
        self._custom_scenes = {}
        self._revision = 0
        self.load_error = ""
        self._load()

    @property
    def revision(self):
        with self._lock:
            return self._revision

    def _scene_locked(self, scene_id):
        if scene_id in BUILTIN_SCENES:
            scene = copy.deepcopy(BUILTIN_SCENES[scene_id])
            scene.update(self._builtin_overrides.get(scene_id, {}))
            scene["builtin"] = True
        else:
            scene = copy.deepcopy(self._custom_scenes.get(scene_id))
            if scene is None:
                raise PromptStoreError(f"场景不存在：{scene_id}")
            scene["builtin"] = False
        scene["id"] = scene_id
        return scene

    def _all_ids_locked(self):
        return list(BUILTIN_SCENES) + sorted(
            self._custom_scenes,
            key=lambda sid: self._custom_scenes[sid]["name"].casefold(),
        )

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict) or raw.get("version") != CONFIG_VERSION:
                raise PromptStoreError("配置版本不受支持")

            overrides = raw.get("builtin_overrides", {})
            customs = raw.get("custom_scenes", {})
            if not isinstance(overrides, dict) or not isinstance(customs, dict):
                raise PromptStoreError("场景集合格式无效")

            checked_overrides = {}
            for scene_id, scene in overrides.items():
                if scene_id in BUILTIN_SCENES:
                    checked_overrides[scene_id] = _validate_scene(scene)

            checked_customs = {}
            for scene_id, scene in customs.items():
                if (isinstance(scene_id, str) and scene_id
                        and scene_id not in BUILTIN_SCENES):
                    checked_customs[scene_id] = _validate_scene(scene)

            active_id = raw.get("active_scene_id", DEFAULT_SCENE_ID)
            if (not isinstance(active_id, str)
                    or (active_id not in BUILTIN_SCENES and active_id not in checked_customs)):
                active_id = DEFAULT_SCENE_ID

            self._builtin_overrides = checked_overrides
            self._custom_scenes = checked_customs
            self._active_id = active_id
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError, PromptStoreError) as exc:
            self.load_error = str(exc)

    def _payload_locked(self):
        return {
            "version": CONFIG_VERSION,
            "active_scene_id": self._active_id,
            "builtin_overrides": copy.deepcopy(self._builtin_overrides),
            "custom_scenes": copy.deepcopy(self._custom_scenes),
        }

    def _restore_payload_locked(self, payload):
        self._active_id = payload["active_scene_id"]
        self._builtin_overrides = payload["builtin_overrides"]
        self._custom_scenes = payload["custom_scenes"]

    def _persist_or_rollback_locked(self, previous):
        try:
            self._persist_locked()
        except Exception:
            self._restore_payload_locked(previous)
            raise

    def _persist_locked(self):
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".prompt-scenes-", suffix=".tmp",
                                         dir=directory, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(self._payload_locked(), f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, self.path)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

    def list_scenes(self):
        with self._lock:
            return [self._scene_locked(scene_id) for scene_id in self._all_ids_locked()]

    def get_scene(self, scene_id):
        with self._lock:
            return self._scene_locked(scene_id)

    def get_active_scene(self):
        with self._lock:
            return self._scene_locked(self._active_id)

    def save_scene(self, scene_id, scene, *, activate=True):
        checked = _validate_scene(scene)
        with self._lock:
            previous = self._payload_locked()
            if scene_id in BUILTIN_SCENES:
                self._builtin_overrides[scene_id] = checked
            elif scene_id in self._custom_scenes:
                self._custom_scenes[scene_id] = checked
            else:
                raise PromptStoreError(f"场景不存在：{scene_id}")
            if activate:
                self._active_id = scene_id
            self._persist_or_rollback_locked(previous)
            self._revision += 1
            return self._scene_locked(scene_id)

    def select_scene(self, scene_id):
        with self._lock:
            self._scene_locked(scene_id)  # 校验存在
            if self._active_id != scene_id:
                previous = self._payload_locked()
                self._active_id = scene_id
                self._persist_or_rollback_locked(previous)
                self._revision += 1
            return self._scene_locked(scene_id)

    def duplicate_scene(self, source_id, name=None):
        with self._lock:
            previous = self._payload_locked()
            source = self._scene_locked(source_id)
            new_id = f"custom-{uuid.uuid4().hex[:10]}"
            scene = {field: source[field] for field in _FIELDS}
            scene["name"] = _clean_text(name or f"{source['name']} 副本",
                                        field="场景名称", limit=MAX_NAME_CHARS,
                                        required=True)
            self._custom_scenes[new_id] = _validate_scene(scene)
            self._active_id = new_id
            self._persist_or_rollback_locked(previous)
            self._revision += 1
            return self._scene_locked(new_id)

    def create_scene(self, scene, *, activate=True):
        checked = _validate_scene(scene)
        with self._lock:
            previous = self._payload_locked()
            new_id = f"custom-{uuid.uuid4().hex[:10]}"
            self._custom_scenes[new_id] = checked
            if activate:
                self._active_id = new_id
            self._persist_or_rollback_locked(previous)
            self._revision += 1
            return self._scene_locked(new_id)

    def reset_builtin(self, scene_id):
        with self._lock:
            if scene_id not in BUILTIN_SCENES:
                raise PromptStoreError("只有内置场景可以恢复默认")
            previous = self._payload_locked()
            self._builtin_overrides.pop(scene_id, None)
            self._active_id = scene_id
            self._persist_or_rollback_locked(previous)
            self._revision += 1
            return self._scene_locked(scene_id)

    def delete_scene(self, scene_id):
        with self._lock:
            if scene_id in BUILTIN_SCENES:
                raise PromptStoreError("内置场景不能删除")
            if scene_id not in self._custom_scenes:
                raise PromptStoreError(f"场景不存在：{scene_id}")
            previous = self._payload_locked()
            del self._custom_scenes[scene_id]
            if self._active_id == scene_id:
                self._active_id = DEFAULT_SCENE_ID
            self._persist_or_rollback_locked(previous)
            self._revision += 1
            return self._scene_locked(self._active_id)

    @staticmethod
    def _scene_block(scene, prompt_key):
        details = scene[prompt_key]
        language = scene["default_language"]
        if not details and not language:
            return ""
        lines = [f"【当前唯一技术场景：{scene['name']}】"]
        if details:
            lines.append(details)
        if language:
            lines.append(f"默认编程语言偏好：{language}；题目明确指定语言时以题目为准。")
        return "\n".join(lines)

    def compose_voice_prompt(self, base_prompt):
        return self.resolve_voice_prompt(base_prompt)[0]

    def compose_vision_prompt(self, base_prompt):
        return self.resolve_vision_prompt(base_prompt)[0]

    def _resolve_prompt(self, base_prompt, prompt_key):
        with self._lock:
            scene = self._scene_locked(self._active_id)
            revision = self._revision
        block = self._scene_block(scene, prompt_key)
        prompt = base_prompt if not block else f"{base_prompt}\n\n{block}"
        return prompt, scene, revision

    def resolve_voice_prompt(self, base_prompt):
        return self._resolve_prompt(base_prompt, "voice_prompt")

    def resolve_vision_prompt(self, base_prompt):
        return self._resolve_prompt(base_prompt, "vision_prompt")
