#!/usr/bin/env python3
"""批量重命名报告文件 v2 - 更可靠的版本"""

import os
import re
import asyncio
import aiohttp
import ssl
import certifi
from pathlib import Path

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

async def generate_title(session, question_text):
    """生成标题"""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": "https://github.com/multi-ai-debate",
        "Content-Type": "application/json"
    }

    prompt = f"""请为以下内容生成一个简短的中文标题（8-15个字）。

内容：{question_text[:300]}

要求：只输出标题，不要解释，不要标点符号，不要引号。"""

    data = {
        "model": "google/gemini-2.0-flash-001",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 50,
        "temperature": 0.3
    }

    try:
        async with session.post(url, headers=headers, json=data) as resp:
            result = await resp.json()
            if resp.status == 200 and "choices" in result:
                title = result["choices"][0]["message"]["content"].strip()
                # 清理
                title = re.sub(r'[^\w\u4e00-\u9fff]', '', title)
                return title[:20] if title else None
            else:
                print(f"    API响应: {result}")
                return None
    except Exception as e:
        print(f"    错误: {e}")
        return None


async def main():
    if not OPENROUTER_API_KEY:
        print("❌ 请设置 OPENROUTER_API_KEY")
        return

    script_dir = Path(__file__).parent
    # 找以时间戳开头且包含"AI分析报告"的文件
    reports = list(script_dir.glob("*_AI分析报告.md"))

    if not reports:
        print("没有需要重命名的文件")
        return

    print(f"找到 {len(reports)} 个文件需要生成标题\n")

    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_context)

    async with aiohttp.ClientSession(connector=connector) as session:
        for report_path in reports:
            print(f"📄 {report_path.name}")

            # 读取内容
            content = report_path.read_text(encoding="utf-8")

            # 提取问题
            match = re.search(r'## 原始问题\n\n(.+?)(?=\n---)', content, re.DOTALL)
            if match:
                question = match.group(1).strip()
            else:
                question = content[200:500]

            print(f"   问题: {question[:50]}...")

            # 生成标题
            title = await generate_title(session, question)

            if title and len(title) >= 4:
                # 提取时间戳
                ts_match = re.match(r'(\d{8}_\d{6})', report_path.name)
                if ts_match:
                    timestamp = ts_match.group(1)
                    new_name = f"{timestamp}_{title}.md"
                    new_path = report_path.parent / new_name

                    # 重命名
                    report_path.rename(new_path)
                    print(f"   ✅ 新标题: {title}")

                    # JSON
                    old_json = report_path.with_suffix('.json')
                    if old_json.exists():
                        old_json.rename(new_path.with_suffix('.json'))
            else:
                print(f"   ⚠️ 标题生成失败，保持原名")

            # 避免请求太快
            await asyncio.sleep(1)
            print()

    print("🎉 完成！")

if __name__ == "__main__":
    asyncio.run(main())
