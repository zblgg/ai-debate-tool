#!/usr/bin/env python3
"""
多AI互批工作流自动化脚本
用法：python multi_ai_debate.py "你的问题"
"""

import os
import asyncio
import aiohttp
import json
import ssl
import certifi
from datetime import datetime
from pathlib import Path

# ==================== API配置 ====================
# 请填入你的API密钥
CLAUDE_API_KEY = os.getenv("ANTHROPIC_API_KEY", "your-claude-api-key")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "your-openai-api-key")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "your-gemini-api-key")

# 模型配置
CLAUDE_MODEL = "claude-sonnet-4-20250514"
OPENAI_MODEL = "gpt-4o"
GEMINI_MODEL = "gemini-2.0-flash-exp"

# ==================== 提示词模板 ====================

# 阶段一：原始回答提示词
ORIGINAL_PROMPT = """请认真回答以下问题，给出你的分析和建议：

{question}

要求：
1. 结构清晰，逻辑严密
2. 给出具体可执行的建议
3. 指出潜在风险和注意事项
"""

# 阶段二：定向互批提示词
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

# 阶段三：最终裁判提示词
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
* {{攻击方}}攻击{{被攻击方}}：\"{{攻击内容摘要}}\" 
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

# 阶段四：内化辅导提示词（核心增值环节）
INTERNALIZATION_PROMPT = """你是一位苏格拉底式的思维教练。你的目标不是给用户答案，而是帮助用户把刚才的分析内化成自己的判断力。

【原始问题】
{question}

【裁判分析报告】
{judgment}

===== 你的任务：内化辅导 =====

你要完成以下5个层次的引导，每个层次都要有具体输出：

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

async def call_claude(session: aiohttp.ClientSession, prompt: str) -> str:
    """调用Claude API"""
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": CLAUDE_API_KEY,
        "content-type": "application/json",
        "anthropic-version": "2023-06-01"
    }
    data = {
        "model": CLAUDE_MODEL,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}]
    }
    
    try:
        async with session.post(url, headers=headers, json=data) as resp:
            if resp.status == 200:
                result = await resp.json()
                return result["content"][0]["text"]
            else:
                error = await resp.text()
                return f"[Claude API错误: {resp.status}] {error}"
    except Exception as e:
        return f"[Claude调用失败] {str(e)}"


async def call_openai(session: aiohttp.ClientSession, prompt: str) -> str:
    """调用ChatGPT API"""
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": OPENAI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096
    }
    
    try:
        async with session.post(url, headers=headers, json=data) as resp:
            if resp.status == 200:
                result = await resp.json()
                return result["choices"][0]["message"]["content"]
            else:
                error = await resp.text()
                return f"[ChatGPT API错误: {resp.status}] {error}"
    except Exception as e:
        return f"[ChatGPT调用失败] {str(e)}"


async def call_gemini(session: aiohttp.ClientSession, prompt: str) -> str:
    """调用Gemini API"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    data = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 4096}
    }
    
    try:
        async with session.post(url, headers=headers, json=data) as resp:
            if resp.status == 200:
                result = await resp.json()
                return result["candidates"][0]["content"]["parts"][0]["text"]
            else:
                error = await resp.text()
                return f"[Gemini API错误: {resp.status}] {error}"
    except Exception as e:
        return f"[Gemini调用失败] {str(e)}"


# ==================== 工作流主逻辑 ====================

async def run_multi_ai_debate(question: str) -> dict:
    """执行完整的多AI互批工作流"""

    results = {
        "question": question,
        "timestamp": datetime.now().isoformat(),
        "phase1_answers": {},
        "phase2_critiques": {},
        "phase3_judgment": "",
        "phase4_internalization": ""
    }

    # 创建SSL上下文，使用certifi提供的证书
    ssl_context = ssl.create_default_context(cafile=certifi.where())

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
        
        # ========== 阶段一：收集原始回答 ==========
        print("🚀 阶段一：并行调用三个AI获取原始回答...")
        
        original_prompt = ORIGINAL_PROMPT.format(question=question)
        
        claude_task = call_claude(session, original_prompt)
        chatgpt_task = call_openai(session, original_prompt)
        gemini_task = call_gemini(session, original_prompt)
        
        claude_answer, chatgpt_answer, gemini_answer = await asyncio.gather(
            claude_task, chatgpt_task, gemini_task
        )
        
        results["phase1_answers"] = {
            "claude": claude_answer,
            "chatgpt": chatgpt_answer,
            "gemini": gemini_answer
        }
        
        print("✅ 阶段一完成：收到三个AI的原始回答")
        
        # ========== 阶段二：定向互批 ==========
        print("🔥 阶段二：让三个AI互相批评...")
        
        # Claude批评其他两个
        claude_critique_prompt = CRITIQUE_PROMPT.format(
            current_ai="Claude",
            question=question,
            my_answer=claude_answer,
            ai_b="Gemini", answer_b=gemini_answer,
            ai_c="ChatGPT", answer_c=chatgpt_answer
        )
        
        # Gemini批评其他两个
        gemini_critique_prompt = CRITIQUE_PROMPT.format(
            current_ai="Gemini",
            question=question,
            my_answer=gemini_answer,
            ai_b="Claude", answer_b=claude_answer,
            ai_c="ChatGPT", answer_c=chatgpt_answer
        )
        
        # ChatGPT批评其他两个
        chatgpt_critique_prompt = CRITIQUE_PROMPT.format(
            current_ai="ChatGPT",
            question=question,
            my_answer=chatgpt_answer,
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
            claude_answer=claude_answer,
            gemini_answer=gemini_answer,
            chatgpt_answer=chatgpt_answer,
            claude_critique=claude_critique,
            gemini_critique=gemini_critique,
            chatgpt_critique=chatgpt_critique
        )
        
        final_judgment = await call_claude(session, judge_prompt)
        results["phase3_judgment"] = final_judgment
        
        print("✅ 阶段三完成：最终裁判报告生成")
        
        # ========== 阶段四：内化辅导 ==========
        print("🎓 阶段四：生成内化辅导内容...")
        
        internalization_prompt = INTERNALIZATION_PROMPT.format(
            question=question,
            judgment=final_judgment
        )
        
        internalization_guide = await call_claude(session, internalization_prompt)
        results["phase4_internalization"] = internalization_guide
        
        print("✅ 阶段四完成：内化辅导内容生成")
    
    return results


def generate_report(results: dict) -> str:
    """生成Markdown格式的完整报告"""
    
    report = f"""# 多AI互批分析报告

**生成时间**: {results['timestamp']}

---

## 原始问题

{results['question']}

---

## 第一轮：原始回答

### Claude的回答

{results['phase1_answers']['claude']}

---

### Gemini的回答

{results['phase1_answers']['gemini']}

---

### ChatGPT的回答

{results['phase1_answers']['chatgpt']}

---

## 第二轮：定向互批

### Claude对其他AI的批评

{results['phase2_critiques']['claude']}

---

### Gemini对其他AI的批评

{results['phase2_critiques']['gemini']}

---

### ChatGPT对其他AI的批评

{results['phase2_critiques']['chatgpt']}

---

## 第三轮：最终裁判整合

{results['phase3_judgment']}

---

## 第四轮：内化辅导（核心增值）

{results['phase4_internalization']}

---

*报告由多AI互批工作流自动生成*
"""
    return report


async def main():
    """主函数"""
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python multi_ai_debate.py \"你的问题\"")
        print("示例: python multi_ai_debate.py \"丹秋寿是否适合给40%股权？\"")
        sys.exit(1)
    
    question = sys.argv[1]
    
    print(f"\n{'='*60}")
    print(f"多AI互批工作流启动")
    print(f"问题: {question}")
    print(f"{'='*60}\n")
    
    # 执行工作流
    results = await run_multi_ai_debate(question)
    
    # 生成报告
    report = generate_report(results)
    
    # 保存报告
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = Path(f"report_{timestamp}.md")
    report_path.write_text(report, encoding="utf-8")
    
    print(f"\n{'='*60}")
    print(f"✅ 报告已保存: {report_path}")
    print(f"{'='*60}\n")
    
    # 也输出到控制台
    print(report)


if __name__ == "__main__":
    asyncio.run(main())
