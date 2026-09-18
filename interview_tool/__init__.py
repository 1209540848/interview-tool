# -*- coding: utf-8 -*-
"""interview_tool — 面试/测评实时辅助工具的功能域包（重构自旧 quiz/code 两个单体）。

布局（依赖单向无环）：config → log/dsp/audio/asr/push/winfx/profiles/prompt_presets；
prompt_store → config,prompt_presets；chat/vision → prompt_store（由 engine 注入实例）；
ui → config,state,winfx,prompt_settings；
engine → 全部；repo 根部薄入口 run_quiz.py / run_code.py → engine,profiles。

行为等价是硬底线：本包每个函数/常量都是从旧单体按符号逐字节切片搬来（tools/assemble.py），
收敛编辑全部登记在 tools/allow-*.json；判定依据 tools/parity.py。
"""
