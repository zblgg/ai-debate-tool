#!/usr/bin/env python3
"""
批量重命名旧报告文件，使用AI生成有意义的标题
"""

import os
import re
import asyncio
import aiohttp
import ssl
import certifi
from pathlib import Path

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

async def call_openrouter(session, model, prompt):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 100,
        "temperature": 0.3
    }

    async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        if resp.status == 200:
            result = await resp.json()
            return result["choices"][0]["message"]["content"]
        return None


async def generate_title_for_file(session, file_path: Path) -> str:
    """读取报告文件并生成标题"""
    content = file_path.read_text(encoding="utf-8")

    # 提取原始问题
    question_match = re.search(r'## 原始问题\n\n(.+?)(?=\n---|\n##)', content, re.DOTALL)
    question = question_match.group(1).strip()[:500] if question_match else content[:500]

    prompt = f"""请为以下分析报告生成一个简短的中文标题（10-20个字）。

原始问题/内容：{question}

要求：
1. 标题要概括分析的核心主题
2. 简洁有力，便于识别
3. 只输出标题本身，不要任何解释或标点符号"""

    title_model = "anthropic/claude-3-5-haiku-20241022"
    result = await call_openrouter(session, title_model, prompt)

    if result:
        title = result.strip().replace('"', '').replace("'", '')
        title = re.sub(r'[^\w\u4e00-\u9fff\-]', '', title)
        return title[:25] if len(title) > 25 else title
    return "AI分析报告"


async def main():
    if not OPENROUTER_API_KEY:
        print("❌ 请设置环境变量 OPENROUTER_API_KEY")
        return

    # 找到所有以 report_ 开头的旧格式文件
    script_dir = Path(__file__).parent
    old_reports = list(script_dir.glob("report_*.md"))

    if not old_reports:
        print("没有找到需要重命名的旧报告文件")
        return

    print(f"找到 {len(old_reports)} 个旧报告文件，开始重命名...\n")

    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(limit=5, ssl=ssl_context)

    async with aiohttp.ClientSession(connector=connector) as session:
        for old_path in old_reports:
            print(f"处理: {old_path.name}")

            # 提取原始时间戳
            timestamp_match = re.search(r'report_(\d{8}_\d{6})', old_path.name)
            if not timestamp_match:
                print(f"  ⚠️ 无法提取时间戳，跳过")
                continue

            timestamp = timestamp_match.group(1)

            # 生成标题
            title = await generate_title_for_file(session, old_path)
            print(f"  生成标题: {title}")

            # 新文件名
            new_name = f"{timestamp}_{title}.md"
            new_path = old_path.parent / new_name

            # 重命名
            old_path.rename(new_path)
            print(f"  ✅ 重命名为: {new_name}")

            # 同时重命名对应的JSON文件（如果存在）
            old_json = old_path.with_suffix('.json')
            if old_json.exists():
                new_json = new_path.with_suffix('.json')
                old_json.rename(new_json)
                print(f"  ✅ JSON也已重命名")

            print()

    print("🎉 所有报告文件已重命名完成！")


if __name__ == "__main__":
    asyncio.run(main())
