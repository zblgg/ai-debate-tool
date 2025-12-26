#!/usr/bin/env python3
"""
多AI互批工作流自动化脚本 (OpenRouter版)
用法：python multi_ai_debate_openrouter.py "你的问题"

⚠️ 重要：请将API Key设置为环境变量，不要硬编码在代码里
   export OPENROUTER_API_KEY="your-key-here"
"""

import os
import re
import asyncio
import aiohttp
import json
import ssl
import certifi
from datetime import datetime
from pathlib import Path

# ==================== API配置 ====================
# 从环境变量读取，更安全
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

if not OPENROUTER_API_KEY:
    print("❌ 错误：请设置环境变量 OPENROUTER_API_KEY")
    print("   方法：export OPENROUTER_API_KEY='your-key-here'")
    exit(1)

BASE_URL = "https://openrouter.ai/api/v1"

# ==================== 模型配置 (2025年12月验证) ====================
# 根据OpenRouter Rankings页面的实际可用模型ID

# 方案A：顶配版（贵但强）- 当前使用
CLAUDE_MODEL = "anthropic/claude-opus-4.5"
OPENAI_MODEL = "openai/gpt-5.2-pro"
GEMINI_MODEL = "google/gemini-3-pro-preview"

# 方案B：性价比版（推荐）
# CLAUDE_MODEL = "anthropic/claude-4.5-sonnet-20250929"  # ~$3/M input, $15/M output
# OPENAI_MODEL = "openai/gpt-4o"                          # ~$2.5/M input, $10/M output
# GEMINI_MODEL = "google/gemini-2.5-flash"                # ~$0.075/M input, $0.30/M output

# 方案C：省钱版（适合高频使用）
# CLAUDE_MODEL = "anthropic/claude-4.5-haiku-20251001"  # ~$0.80/M input, $4/M output
# OPENAI_MODEL = "openai/gpt-4o-mini"                    # ~$0.15/M input, $0.60/M output
# GEMINI_MODEL = "google/gemini-2.0-flash-001"           # 免费或极低价

# ==================== 提示词模板 ====================

ORIGINAL_PROMPT = """请认真回答以下问题，给出你的分析和建议：

{question}

要求：
1. 结构清晰，逻辑严密
2. 给出具体可执行的建议
3. 指出潜在风险和注意事项
"""

CRITIQUE_PROMPT = """你是{current_ai}。你刚才对以下问题给出了回答：

【原始问题】
{question}

【你的回答】
{my_answer}

现在，另外两个AI也给出了他们的回答：

【{ai_b}的回答】
{answer_b}

【{ai_c}的回答】
{answer_c}

---

你的任务是定向批评，不是泛泛评价。

任务A：攻击
分别找出另外两个回答中最薄弱的一环。每个批评必须：
- 指向具体句子或论点（引用原文）
- 说明是逻辑漏洞、事实错误、还是隐含假设不成立
- 一针见血，每条不超过50字

格式：
* 对{ai_b}的攻击：[引用原文] → [问题类型]：[批评内容]
* 对{ai_c}的攻击：[引用原文] → [问题类型]：[批评内容]

任务B：承认优势
如果对方有任何一个点比你答得更好、更深、更准，必须承认。
格式：
* {ai_b}/{ai_c}在___这一点上比我更好，因为___

如果没有，写"无"。不要硬凑。

任务C：自我修正
基于对方的回答，你是否需要修正自己的观点？
- 如果需要，说明修正什么、为什么
- 如果不需要，说明为什么你的原始立场依然成立

---

硬性规则：
× 禁止说"各有侧重"、"互为补充"、"都有道理"
× 禁止礼貌性认同
× 总字数不超过400字
"""

JUDGE_PROMPT = """【原始问题】 
{question}

===== 第一轮：原始回答 =====

【Claude的回答】 
{claude_answer}

【Gemini的回答】 
{gemini_answer}

【ChatGPT的回答】
{chatgpt_answer}

===== 第二轮：定向互批 =====

【Claude对其他AI的批评】
{claude_critique}

【Gemini对其他AI的批评】
{gemini_critique}

【ChatGPT对其他AI的批评】
{chatgpt_critique}

===== 你的角色 =====

极度严苛的主编与逻辑学家。你厌恶正确的废话，拒绝和稀泥。
你现在拥有两轮信息：原始回答 + 互相批评。你的判断必须基于这两轮的全部信息。

===== 硬性规则 =====

* 所有判断必须锚定到原文短句。若为推断，标注【推断】并说明依据。
* 不得使用"各有千秋"、"互补"、"侧重点不同"等模糊表述。
* 若某项任务的答案是"没有"或"全是废话"，直接说，不要硬凑。
* 互批中的攻击如果有效，必须采纳；如果无效，必须说明为什么无效。

===== 任务清单 =====

【任务1】质量预审
基于原始回答和互批表现，判断：
- 哪个AI的回答值得认真对待？（一句话理由）
- 哪个AI的互批最有杀伤力？（一句话理由）
- 哪个AI在被批评后表现出真正的思考深度？（一句话理由）

【任务2】互批有效性裁决
逐一审查三方互批中的每个攻击点：
格式：
* [攻击方]攻击[被攻击方]："[攻击内容摘要]" 
  → 有效/无效。理由：___

只列出有实质意义的攻击（无效的废话攻击可以跳过）。

【任务3】去伪存真
综合两轮信息，剔除正确的废话和三者共有的常识。
只保留有信息增量的独特洞见（至少1条，不设上限）。
格式：
* 洞见内容 + 来源（Claude/Gemini/ChatGPT）+ 证据锚点
* 【互批增量】如果某个洞见是在互批阶段才涌现的，特别标注

【任务4】隐含假设审查
三个回答各自基于什么前提在立论？
格式：
* Claude的隐含假设：___。
  - 成立/不成立，因为___
  - 互批中是否被有效攻击：是/否
* Gemini的隐含假设：___。
  - 成立/不成立，因为___
  - 互批中是否被有效攻击：是/否
* ChatGPT的隐含假设：___。
  - 成立/不成立，因为___
  - 互批中是否被有效攻击：是/否

【任务5】核心分歧裁决
找出根本分歧（至少1个），用对立命题呈现：
格式：
* 分歧点：___
  - Claude立场：___
  - Gemini立场：___
  - ChatGPT立场：___
  - 互批中的交锋：___（如果有）
  - 【裁决】：___方更有力。理由：___（必须给出明确判断，不得回避）

【任务6】盲点扫描
三者+互批阶段共同遗漏了什么？
* 事实层盲点：漏掉了什么关键事实/变量？
* 方法层盲点：思考路径上缺了什么？
* 价值层盲点：回避了什么价值判断？
* 【互批盲区】：三方在互批时共同回避了什么？（往往是最敏感的问题）

【任务7】最终结论
综合以上所有分析，对原始问题给出你的最终答案：
* 核心结论（一句话）
* 关键支撑点（2-3条）
* 置信度：高/中/低，理由：___

【任务8】下一步行动
二选一回答：
A. 如果需要继续深挖：最值得追问的一个方向是什么？为什么？
B. 如果可以行动：现在应该采取的具体行动是什么？

===== 输出格式要求 =====

按任务编号依次输出，每个任务之间用分隔线隔开。
宁可写"本任务无有效输出"，也不要硬凑内容。
"""

SYNTHESIS_REPORT_PROMPT = """你是一位资深的战略顾问。你刚刚见证了三个顶级AI针对同一个问题的深度讨论和激烈互批，并且有一位严苛的裁判对所有讨论进行了深度分析。

现在，你需要基于裁判分析的精华，为用户生成一份**完整、可执行的综合报告**。

【原始问题】
{question}

===== 裁判深度分析报告（已整合三方观点与互批精华）=====

{judgment}

===== 你的任务：生成综合报告 =====

请按以下结构输出完整报告：

---

## 一、问题的完整回答

综合三方讨论，给出对原始问题的**完整、深入**的回答。要求：
- 不是简单总结，而是站在巨人肩膀上的整合升华
- 吸收各方被证明有效的观点，剔除被批倒的论点
- 篇幅充分，把问题讲透（500-1000字）

---

## 二、核心结论

用一句话概括最重要的结论。这句话要：
- 足够有力，可以直接用于决策
- 明确表态，不含糊

---

## 三、关键论据

列出支撑上述结论的3-5条关键论据。格式：
1. **论据一**：[内容] —— 来源于[Claude/Gemini/ChatGPT]的观点，在互批中[被验证/被修正]
2. **论据二**：...

---

## 四、情境化建议

针对不同情况给出差异化建议：

**情境A：如果[某种条件]**
→ 建议：...

**情境B：如果[另一种条件]**
→ 建议：...

**情境C：如果[特殊情况]**
→ 建议：...

（至少给出2-3个情境）

---

## 五、风险与注意事项

明确指出需要警惕的风险：

⚠️ **风险1**：[描述] —— 应对策略：...
⚠️ **风险2**：[描述] —— 应对策略：...
⚠️ **风险3**：[描述] —— 应对策略：...

---

## 六、行动方案

给出具体可执行的行动步骤，按优先级排序：

**立即行动（本周内）：**
1. ...
2. ...

**短期行动（1个月内）：**
1. ...
2. ...

**长期规划（3个月以上）：**
1. ...

---

## 七、争议与不确定性

诚实说明哪些方面仍存在争议或不确定性：

- **争议点**：三方在___问题上存在分歧，目前无法完全定论
- **不确定性**：___取决于___，需要观察___再做调整
- **信息缺口**：如果能获得___信息，可以做出更精准的判断

---

## 八、一句话行动指南

如果用户只能记住一句话，那就是：

> [一句话，直接告诉用户现在最该做什么]

---

===== 输出原则 =====

* 立场鲜明，不和稀泥
* 建议具体，不说正确的废话
* 风险真实，不是为了凑数
* 如果某个部分确实无法产出有价值内容，简要说明原因即可
"""

INTERNALIZATION_PROMPT = """你是一位苏格拉底式的思维教练。你的目标不是给用户答案，而是帮助用户把刚才的分析内化成自己的判断力。

【原始问题】
{question}

【裁判分析报告】
{judgment}

===== 你的任务：内化辅导 =====

你要完成以下6个层次的引导，每个层次都要有具体输出：

---

【层次1】核心洞见萃取

从刚才的分析中，提炼出最值得用户记住的1-2个核心洞见。

要求：
- 不是总结，是萃取——去掉水分，只留精华
- 用一句话表达，像格言一样可以记住
- 解释为什么这个洞见对这个问题特别重要

格式：
💎 洞见1：[一句话]
   为什么重要：___
   
💎 洞见2：[一句话]（如果有）
   为什么重要：___

---

【层次2】思维盲区诊断

基于用户提出的问题方式，推断用户可能存在的思维盲区。

分析维度：
- 用户的问题隐含了什么假设？这个假设成立吗？
- 用户可能习惯从什么角度思考？容易忽略什么角度？
- 三个AI中，哪个视角是用户最可能忽略的？为什么？

格式：
🔍 你可能的盲区：___
   证据：从你的问题方式推断，___
   建议：下次思考类似问题时，先问自己___

---

【层次3】经验连接器

帮用户把这次分析和他的实际工作场景建立连接。

要求：
- 基于用户是餐厅管理者的背景
- 给出2-3个具体的应用场景
- 每个场景说明：什么情况下会遇到类似问题？可以怎么用今天的思路？

格式：
🔗 应用场景1：当你遇到___的时候
   可以这样用：___

🔗 应用场景2：当你遇到___的时候
   可以这样用：___

---

【层次4】可复用思维框架

从这次分析中抽象出一个可以反复使用的思维框架或检查清单。

要求：
- 给框架起一个容易记住的名字
- 框架要足够简洁，3-5个步骤以内
- 说明这个框架适用于什么类型的问题

格式：
🧰 框架名称：「___」法
   
   适用场景：当你需要___的时候
   
   步骤：
   1. ___
   2. ___
   3. ___
   
   使用示例：___

---

【层次5】刻意练习设计

给用户设计一个小练习，帮助巩固今天的思维收获。

要求：
- 练习要具体、可执行
- 难度适中，10分钟内可完成
- 练习完成后能明显感知到思维的提升

格式：
📝 今日练习：

   任务：___
   
   预期时间：___分钟
   
   完成标准：当你能够___，说明你已经掌握了这个思维方式
   
   可选加餐：如果想进一步提升，可以___

---

【层次6】元认知反思引导

引导用户反思这次思考过程本身。

提出2-3个反思问题（不需要用户立即回答，是留给用户自己思考的）：

格式：
🪞 反思问题：

   1. ___？
   2. ___？
   3. ___？

这些问题的目的是帮助用户觉察自己的思维习惯，没有标准答案。

---

===== 输出原则 =====

* 不要重复裁判报告已经说过的内容
* 每个层次都要有实质性输出，不是走形式
* 语言要直接、有力，像教练在指导学员
* 所有建议必须和用户的实际场景（餐厅管理）相关
* 如果某个层次确实无法产出有价值内容，写"本层次暂无特别输出"并说明原因
"""


# ==================== API调用函数 ====================

# 不同模型的超时时间配置（秒）
MODEL_TIMEOUTS = {
    "openai/gpt-5.2-pro": 600,      # GPT-5.2 Pro 响应慢，给 10 分钟
    "openai/gpt-4o": 180,            # GPT-4o 相对快
    "anthropic/claude-opus-4.5": 300, # Opus 也可能较慢
    "default": 180                   # 默认 3 分钟
}

def get_timeout_for_model(model: str) -> int:
    """根据模型获取超时时间"""
    return MODEL_TIMEOUTS.get(model, MODEL_TIMEOUTS["default"])

async def call_openrouter(session: aiohttp.ClientSession, model: str, prompt: str, role: str = "user") -> str:
    """通过OpenRouter调用任意模型"""
    url = f"{BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://github.com/multi-ai-debate",
        "X-Title": "Multi-AI Debate Workflow",
        "Content-Type": "application/json"
    }
    data = {
        "model": model,
        "messages": [{"role": role, "content": prompt}],
        "max_tokens": 4096,
        "temperature": 0.7
    }

    timeout_seconds = get_timeout_for_model(model)

    try:
        async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=timeout_seconds)) as resp:
            if resp.status == 200:
                result = await resp.json()
                if "choices" in result and len(result["choices"]) > 0:
                    return result["choices"][0]["message"]["content"]
                else:
                    return f"[API返回空内容] {json.dumps(result, ensure_ascii=False)}"
            else:
                error = await resp.text()
                return f"[API错误 {resp.status}] {error}"
    except asyncio.TimeoutError:
        return f"[超时] 模型 {model} 响应超过 {timeout_seconds} 秒"
    except Exception as e:
        return f"[调用失败] {str(e)}"


async def call_claude(session, prompt):
    return await call_openrouter(session, CLAUDE_MODEL, prompt)

async def call_openai(session, prompt):
    return await call_openrouter(session, OPENAI_MODEL, prompt)

async def call_gemini(session, prompt):
    return await call_openrouter(session, GEMINI_MODEL, prompt)


# ==================== 工作流主逻辑 ====================

async def run_multi_ai_debate(question: str, mode: str = "full") -> dict:
    """执行完整的多AI互批工作流

    Args:
        question: 用户问题
        mode: 输出模式
            - "quick": 简单行动指南（跳过综合报告和内化辅导）
            - "full": 完整报告（包含综合报告，跳过内化辅导）
            - "all": 全部内容（包含综合报告和内化辅导）
    """

    results = {
        "question": question,
        "timestamp": datetime.now().isoformat(),
        "mode": mode,
        "models_used": {
            "claude": CLAUDE_MODEL,
            "openai": OPENAI_MODEL,
            "gemini": GEMINI_MODEL
        },
        "phase1_answers": {},
        "phase2_critiques": {},
        "phase3_judgment": "",
        "phase4_synthesis": "",  # 新增：综合报告
        "phase5_internalization": ""  # 原来的内化辅导移到这里
    }
    
    # 创建SSL上下文，使用certifi提供的证书
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(limit=10, ssl=ssl_context)
    async with aiohttp.ClientSession(connector=connector) as session:
        
        # ========== 阶段一：收集原始回答 ==========
        print(f"🚀 阶段一：并行调用三个AI获取原始回答...")
        print(f"   Claude: {CLAUDE_MODEL}")
        print(f"   OpenAI: {OPENAI_MODEL}")
        print(f"   Gemini: {GEMINI_MODEL}")
        
        original_prompt = ORIGINAL_PROMPT.format(question=question)
        
        claude_answer, chatgpt_answer, gemini_answer = await asyncio.gather(
            call_claude(session, original_prompt),
            call_openai(session, original_prompt),
            call_gemini(session, original_prompt)
        )
        
        results["phase1_answers"] = {
            "claude": claude_answer,
            "chatgpt": chatgpt_answer,
            "gemini": gemini_answer
        }
        
        print("✅ 阶段一完成：收到三个AI的原始回答")
        
        # ========== 阶段二：定向互批 ==========
        print("🔥 阶段二：让三个AI互相批评...")
        
        claude_critique_prompt = CRITIQUE_PROMPT.format(
            current_ai="Claude", question=question, my_answer=claude_answer,
            ai_b="Gemini", answer_b=gemini_answer,
            ai_c="ChatGPT", answer_c=chatgpt_answer
        )
        
        gemini_critique_prompt = CRITIQUE_PROMPT.format(
            current_ai="Gemini", question=question, my_answer=gemini_answer,
            ai_b="Claude", answer_b=claude_answer,
            ai_c="ChatGPT", answer_c=chatgpt_answer
        )
        
        chatgpt_critique_prompt = CRITIQUE_PROMPT.format(
            current_ai="ChatGPT", question=question, my_answer=chatgpt_answer,
            ai_b="Claude", answer_b=claude_answer,
            ai_c="Gemini", answer_c=gemini_answer
        )
        
        claude_critique, gemini_critique, chatgpt_critique = await asyncio.gather(
            call_claude(session, claude_critique_prompt),
            call_gemini(session, gemini_critique_prompt),
            call_openai(session, chatgpt_critique_prompt)
        )
        
        results["phase2_critiques"] = {
            "claude": claude_critique,
            "gemini": gemini_critique,
            "chatgpt": chatgpt_critique
        }
        
        print("✅ 阶段二完成：收到三方互批结果")
        
        # ========== 阶段三：最终裁判 ==========
        print("⚖️ 阶段三：Claude进行最终裁判整合...")
        
        judge_prompt = JUDGE_PROMPT.format(
            question=question,
            claude_answer=claude_answer, gemini_answer=gemini_answer, chatgpt_answer=chatgpt_answer,
            claude_critique=claude_critique, gemini_critique=gemini_critique, chatgpt_critique=chatgpt_critique
        )
        
        final_judgment = await call_claude(session, judge_prompt)
        results["phase3_judgment"] = final_judgment
        
        print("✅ 阶段三完成：最终裁判报告生成")

        # ========== 阶段四：综合报告（full/all模式）==========
        if mode in ["full", "all"]:
            print("📊 阶段四：生成综合报告...")

            # 使用精简版prompt，只传入裁判分析结果（已包含精华）
            synthesis_prompt = SYNTHESIS_REPORT_PROMPT.format(
                question=question,
                judgment=final_judgment
            )

            synthesis_report = await call_claude(session, synthesis_prompt)
            results["phase4_synthesis"] = synthesis_report

            print("✅ 阶段四完成：综合报告生成")
        else:
            print("⏭️  跳过阶段四（简单模式）")

        # ========== 阶段五：内化辅导（仅all模式）==========
        if mode == "all":
            print("🎓 阶段五：生成内化辅导内容...")

            internalization_prompt = INTERNALIZATION_PROMPT.format(
                question=question,
                judgment=final_judgment
            )

            internalization_guide = await call_claude(session, internalization_prompt)
            results["phase5_internalization"] = internalization_guide

            print("✅ 阶段五完成：内化辅导内容生成")
        else:
            print("⏭️  跳过阶段五（非全量模式）")

    return results


def generate_report(results: dict) -> str:
    """生成Markdown格式的完整报告"""

    mode = results.get("mode", "full")
    mode_label = {"quick": "简单模式", "full": "完整模式", "all": "全量模式"}.get(mode, mode)

    report = f"""# 多AI互批分析报告

**生成时间**: {results['timestamp']}
**输出模式**: {mode_label}

**使用模型**:
- Claude: `{results['models_used']['claude']}`
- OpenAI: `{results['models_used']['openai']}`
- Gemini: `{results['models_used']['gemini']}`

---

## 原始问题

{results['question']}

---

## 第一轮：原始回答

### Claude 的回答

{results['phase1_answers']['claude']}

---

### Gemini 的回答

{results['phase1_answers']['gemini']}

---

### ChatGPT 的回答

{results['phase1_answers']['chatgpt']}

---

## 第二轮：定向互批

### Claude 对其他AI的批评

{results['phase2_critiques']['claude']}

---

### Gemini 对其他AI的批评

{results['phase2_critiques']['gemini']}

---

### ChatGPT 对其他AI的批评

{results['phase2_critiques']['chatgpt']}

---

## 第三轮：裁判分析

{results['phase3_judgment']}

---
"""

    # 综合报告（full/all模式）
    if results.get("phase4_synthesis"):
        report += f"""
## 第四轮：综合报告

{results['phase4_synthesis']}

---
"""

    # 内化辅导（仅all模式）
    if results.get("phase5_internalization"):
        report += f"""
## 第五轮：内化辅导

{results['phase5_internalization']}

---
"""

    report += """
*报告由多AI互批工作流自动生成 via OpenRouter*
"""
    return report


async def generate_title(question: str, judgment: str) -> str:
    """使用AI生成简短的报告标题"""
    # 使用Gemini Flash生成标题（免费且稳定）
    title_model = "google/gemini-2.0-flash-001"

    prompt = f"""请为以下分析报告生成一个简短的中文标题（10-20个字以内）。

原始问题：{question[:200]}

要求：
1. 标题要概括分析的核心主题
2. 简洁有力，便于识别
3. 只输出标题本身，不要任何解释或标点符号
4. 不要使用引号

示例格式：学习与实践的关系探讨"""

    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(limit=5, ssl=ssl_context)

    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            result = await call_openrouter(session, title_model, prompt)
            # 清理结果，只保留核心文字
            title = result.strip().replace('"', '').replace("'", '').replace('《', '').replace('》', '')
            # 如果返回结果太长或包含错误信息，使用默认标题
            if len(title) > 30 or title.startswith('['):
                return "AI分析报告"
            return title
    except Exception as e:
        print(f"   ⚠️ 标题生成失败，使用默认标题: {e}")
        return "AI分析报告"


def ask_mode() -> str:
    """询问用户选择输出模式"""
    print()
    print("=" * 60)
    print("请选择输出模式：")
    print("=" * 60)
    print()
    print("  [1] 简单模式 (quick)")
    print("      → 只输出裁判分析 + 下一步行动")
    print("      → 速度最快，成本最低")
    print()
    print("  [2] 完整模式 (full) 【推荐】")
    print("      → 包含完整综合报告（问题回答、结论、行动方案等）")
    print("      → 适合需要完整决策依据的场景")
    print()
    print("  [3] 全量模式 (all)")
    print("      → 完整报告 + 内化辅导")
    print("      → 适合深度学习和思维训练")
    print()
    print("=" * 60)

    while True:
        choice = input("请输入选项 [1/2/3]，直接回车默认选2: ").strip()
        if choice == "" or choice == "2":
            return "full"
        elif choice == "1":
            return "quick"
        elif choice == "3":
            return "all"
        else:
            print("❌ 无效选项，请输入 1、2 或 3")


async def main():
    """主函数"""
    import sys

    if len(sys.argv) < 2:
        print("=" * 60)
        print("多AI互批工作流 (OpenRouter版)")
        print("=" * 60)
        print()
        print("用法: python multi_ai_debate_openrouter.py \"你的问题\"")
        print()
        print("示例:")
        print('  python multi_ai_debate_openrouter.py "丹秋寿是否适合给40%股权？"')
        print('  python multi_ai_debate_openrouter.py "是否应该在淡季投入5万做抖音营销？"')
        print()
        print("可选参数:")
        print("  --quick   简单模式（只输出裁判分析）")
        print("  --full    完整模式（包含综合报告）【默认】")
        print("  --all     全量模式（包含内化辅导）")
        print()
        print("配置:")
        print(f"  Claude: {CLAUDE_MODEL}")
        print(f"  OpenAI: {OPENAI_MODEL}")
        print(f"  Gemini: {GEMINI_MODEL}")
        print()
        sys.exit(0)

    question = sys.argv[1]

    # 检查命令行参数是否指定了模式
    mode = None
    if "--quick" in sys.argv:
        mode = "quick"
    elif "--full" in sys.argv:
        mode = "full"
    elif "--all" in sys.argv:
        mode = "all"

    # 如果命令行没指定模式，交互式询问
    if mode is None:
        mode = ask_mode()

    mode_label = {"quick": "简单模式", "full": "完整模式", "all": "全量模式"}.get(mode, mode)

    print()
    print("=" * 60)
    print("多AI互批工作流启动")
    print("=" * 60)
    print(f"问题: {question}")
    print(f"模式: {mode_label}")
    print("=" * 60)
    print()

    # 执行工作流
    results = await run_multi_ai_debate(question, mode=mode)
    
    # 生成报告
    report = generate_report(results)
    
    # 保存报告 - 用AI生成标题
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 调用AI生成简短标题
    print("📝 正在生成报告标题...")
    title = await generate_title(question, results.get("phase3_judgment", ""))

    # 清理标题中的特殊字符
    title = re.sub(r'[^\w\u4e00-\u9fff\-]', '', title)
    title = title[:25] if len(title) > 25 else title

    report_path = Path(f"{timestamp}_{title}.md")
    report_path.write_text(report, encoding="utf-8")

    # 同时保存JSON格式（方便程序读取）
    json_path = Path(f"{timestamp}_{title}.json")
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    
    print()
    print("=" * 60)
    print(f"✅ 报告已保存:")
    print(f"   Markdown: {report_path}")
    print(f"   JSON: {json_path}")
    print("=" * 60)
    print()
    
    # 输出到控制台
    print(report)


if __name__ == "__main__":
    asyncio.run(main())
