#!/usr/bin/env python3
"""
多AI辩论（带模型fallback）
"""

import os
import asyncio
import aiohttp
import ssl
import certifi
from datetime import datetime
from pathlib import Path

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
BASE_URL = "https://openrouter.ai/api/v1"

# 模型fallback列表
CLAUDE_MODELS = [
    "anthropic/claude-3.5-sonnet",
]

GPT_MODELS = [
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "openai/gpt-4-turbo",
]

GEMINI_MODELS = [
    "google/gemini-2.0-flash-001",
    "google/gemini-pro",
]


async def call_with_fallback(session, models: list, prompt: str, name: str) -> str:
    """尝试多个模型直到成功"""
    for model in models:
        try:
            print(f"   尝试 {name}: {model}...")
            result = await call_openrouter(session, model, prompt)
            if not result.startswith("[") and len(result) > 100:
                print(f"   ✅ {name} 成功")
                return result
            else:
                print(f"   ⚠️ {model} 返回异常，尝试下一个...")
        except Exception as e:
            print(f"   ❌ {model} 失败: {e}")
    return f"[{name}所有模型都失败了]"


async def call_openrouter(session, model: str, prompt: str) -> str:
    """调用OpenRouter API"""
    url = f"{BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
        "temperature": 0.7
    }

    async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=120)) as resp:
        if resp.status == 200:
            result = await resp.json()
            if "choices" in result and len(result["choices"]) > 0:
                return result["choices"][0]["message"]["content"]
        error = await resp.text()
        return f"[API错误 {resp.status}] {error[:200]}"


ORIGINAL_PROMPT = """请认真分析以下问题，给出你的深度思考：

{question}

要求：
1. 结构清晰，逻辑严密
2. 结合实际案例
3. 给出具体可执行的建议
4. 指出潜在的风险和矛盾点
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

请定向批评另外两个回答中的薄弱点，同时承认他们比你更好的地方（如果有）。

要求：
1. 指出具体的逻辑漏洞或盲点
2. 承认对方的优势（如果有）
3. 基于批评修正自己的观点
4. 总字数不超过500字
"""

JUDGE_PROMPT = """【原始问题】
{question}

===== 三个AI的回答 =====

【Claude的回答】
{claude_answer}

【GPT的回答】
{gpt_answer}

【Gemini的回答】
{gemini_answer}

===== 三方互批 =====

【Claude的批评】
{claude_critique}

【GPT的批评】
{gpt_critique}

【Gemini的批评】
{gemini_critique}

===== 你的任务 =====

作为最终裁判，请：
1. 评估三方观点的优劣
2. 指出各自的盲点
3. 综合给出最终结论
4. 给出具体可执行的行动建议

要求：不要和稀泥，要有明确的判断。
"""


async def run_debate(question: str):
    """执行三AI辩论"""

    print("=" * 70)
    print("🔥 三AI辩论分析")
    print("=" * 70)
    print(f"\n问题: {question[:100]}...\n")

    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_context)

    async with aiohttp.ClientSession(connector=connector) as session:

        # === 阶段一：原始回答 ===
        print("\n📝 阶段一：收集原始回答")
        print("-" * 50)

        original_prompt = ORIGINAL_PROMPT.format(question=question)

        claude_task = call_with_fallback(session, CLAUDE_MODELS, original_prompt, "Claude")
        gpt_task = call_with_fallback(session, GPT_MODELS, original_prompt, "GPT")
        gemini_task = call_with_fallback(session, GEMINI_MODELS, original_prompt, "Gemini")

        claude_answer, gpt_answer, gemini_answer = await asyncio.gather(
            claude_task, gpt_task, gemini_task
        )

        # === 阶段二：互批 ===
        print("\n🔥 阶段二：互相批评")
        print("-" * 50)

        claude_critique_prompt = CRITIQUE_PROMPT.format(
            current_ai="Claude", question=question, my_answer=claude_answer,
            ai_b="GPT", answer_b=gpt_answer,
            ai_c="Gemini", answer_c=gemini_answer
        )
        gpt_critique_prompt = CRITIQUE_PROMPT.format(
            current_ai="GPT", question=question, my_answer=gpt_answer,
            ai_b="Claude", answer_b=claude_answer,
            ai_c="Gemini", answer_c=gemini_answer
        )
        gemini_critique_prompt = CRITIQUE_PROMPT.format(
            current_ai="Gemini", question=question, my_answer=gemini_answer,
            ai_b="Claude", answer_b=claude_answer,
            ai_c="GPT", answer_c=gpt_answer
        )

        claude_critique, gpt_critique, gemini_critique = await asyncio.gather(
            call_with_fallback(session, CLAUDE_MODELS, claude_critique_prompt, "Claude"),
            call_with_fallback(session, GPT_MODELS, gpt_critique_prompt, "GPT"),
            call_with_fallback(session, GEMINI_MODELS, gemini_critique_prompt, "Gemini")
        )

        # === 阶段三：最终裁判 ===
        print("\n⚖️ 阶段三：最终裁判")
        print("-" * 50)

        judge_prompt = JUDGE_PROMPT.format(
            question=question,
            claude_answer=claude_answer, gpt_answer=gpt_answer, gemini_answer=gemini_answer,
            claude_critique=claude_critique, gpt_critique=gpt_critique, gemini_critique=gemini_critique
        )

        final_judgment = await call_with_fallback(session, CLAUDE_MODELS, judge_prompt, "裁判")

    # 生成报告
    report = f"""# 三AI辩论分析报告

**问题**: {question}

**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}

---

## 第一轮：原始回答

### Claude
{claude_answer}

---

### GPT
{gpt_answer}

---

### Gemini
{gemini_answer}

---

## 第二轮：互相批评

### Claude的批评
{claude_critique}

---

### GPT的批评
{gpt_critique}

---

### Gemini的批评
{gemini_critique}

---

## 最终裁判结论

{final_judgment}
"""

    # 保存
    report_dir = Path(__file__).parent
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"{timestamp}_招聘策略分析.md"
    report_path.write_text(report, encoding="utf-8")

    print(f"\n✅ 报告已保存: {report_path}")
    print("\n" + "=" * 70)
    print("最终裁判结论")
    print("=" * 70)
    print(final_judgment)

    return report


async def main():
    question = """
我是一家火锅连锁店的老板，正在推进"合伙人裂变计划"——把优秀店长培养成合伙人，让他们去开新店。

现在遇到一个招聘困境，请帮我深度分析：

【核心矛盾】
1. 我想招"没有其他选择、会全力以赴"的人
   - 比如二本学历、没有大厂光环
   - 这条路对他们来说是最好的机会，所以会拼命
   - 但这样的人往往视野有限，可能只能做执行

2. 但我需要他们未来能"复制自己"
   - 不只是单店店长，而是能培养出下一个店长
   - 需要有全局思维、能带团队、能传授方法论
   - 这似乎需要更高的认知能力

【我的困惑】
- 这两个要求是否矛盾？
- 如何在招聘阶段就识别出"有潜力成长为能复制他人的人"？
- 有没有具体的面试问题或测试方法？
- 如何设计培养路径，让"全力以赴型"的人也能成长为"能带团队型"？

【背景信息】
- 餐饮行业，火锅连锁
- 目标人群：25-35岁，有一定餐饮经验
- 提供的是"店长→合伙人"的成长路径
- 愿意花6-12个月培养

请给出系统性的分析和可执行的建议。
"""

    await run_debate(question)


if __name__ == "__main__":
    asyncio.run(main())
