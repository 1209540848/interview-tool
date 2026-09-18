# -*- coding: utf-8 -*-
"""内置技术场景。

这里只保存可被用户场景覆盖的领域补充，不保存 quiz/code 的运行协议。后者仍由
profiles.py 负责，避免技术方向与热键、窗口、截图输出格式耦合。
"""

DEFAULT_SCENE_ID = "general"


BUILTIN_SCENES = {
    "general": {
        "name": "通用",
        "default_language": "",
        "voice_prompt": "",
        "vision_prompt": "",
    },
    "ai-infra": {
        "name": "AI Infra",
        "default_language": "Python / C++ / CUDA",
        "voice_prompt": (
            "当前面试方向是 AI Infra。回答时优先从模型量化、推理框架、CUDA/Triton 算子、"
            "显存与 KV Cache、吞吐与延迟、多卡并行和 NCCL 通信等角度分析。涉及方案选择时，"
            "明确精度、性能、显存、部署复杂度之间的权衡；缺少硬件、batch size、序列长度等"
            "条件时，可以先说明合理假设。熟悉 vLLM、SGLang、TensorRT-LLM、AWQ、GPTQ、"
            "FP8/INT8/INT4、TP/PP/DP 等常见技术。"
        ),
        "vision_prompt": (
            "当前题目属于 AI Infra 场景。识别到系统设计、性能数据、模型结构或代码时，重点检查"
            "量化精度、显存占用、算子瓶颈、KV Cache、批处理策略、多卡拓扑与通信开销。代码题"
            "默认优先使用 Python、C++ 或 CUDA，但必须服从题目明确指定的语言和已有框架。"
        ),
    },
    "frontend": {
        "name": "前端",
        "default_language": "TypeScript / JavaScript",
        "voice_prompt": (
            "当前面试方向是前端。回答时优先结合 HTML、CSS、JavaScript、TypeScript、Vue 3、"
            "Composition API、Pinia、Vue Router 和 Vite；同时考虑浏览器渲染、事件循环、网络、"
            "性能优化、兼容性、可访问性与前端安全。涉及工程方案时说明可维护性、用户体验和"
            "性能之间的权衡。"
        ),
        "vision_prompt": (
            "当前题目属于前端场景。优先识别 HTML、CSS、JavaScript、TypeScript 和 Vue 代码。"
            "代码题默认使用 TypeScript；如果截图已有函数签名、组件骨架或指定语言，只补充题目"
            "要求的部分并保持现有接口与缩进。"
        ),
    },
}
