#!/usr/bin/env python3
"""Markdown → PDF 完整流水线（中文+emoji友好）

用法: python3 md_to_pdf.py input.md output.pdf

优先级: WeasyPrint（纯Python） > Puppeteer（需Chrome）
"""
import sys
import os
import subprocess
from pathlib import Path
import markdown

# 确保 linuxbrew 库路径可用（WeasyPrint 的 pango 依赖）
_BREW_LIB = "/home/linuxbrew/.linuxbrew/lib"
if os.path.isdir(_BREW_LIB):
    ld = os.environ.get("LD_LIBRARY_PATH", "")
    if _BREW_LIB not in ld:
        os.environ["LD_LIBRARY_PATH"] = f"{_BREW_LIB}:{ld}" if ld else _BREW_LIB

_CSS = """
body { font-family: "Noto Sans CJK SC", "PingFang SC", "Microsoft YaHei", sans-serif;
       font-size: 10.8pt; line-height: 1.78; max-width: 860px; margin: 0 auto; padding: 30px 34px; color: #1f2933; }
p { text-indent: 0; margin: 0.65em 0; text-align: justify; }
li p, blockquote p, td p, th p { text-indent: 0; }
ul, ol { margin: 0.5em 0 0.9em 1.2em; padding-left: 1.1em; }
li { margin: 0.28em 0; }
h1, h2, h3, h4 { page-break-after: avoid; }
h1 { font-size: 20pt; border-bottom: 2px solid #1a5276; padding-bottom: 8px; color: #1a5276; margin-bottom: 18px; }
h2 { font-size: 15pt; color: #1a5276; margin-top: 26px; border-bottom: 1px solid #d8e1e8; padding-bottom: 5px; }
h3 { font-size: 12.2pt; color: #2e4057; margin-top: 18px; margin-bottom: 8px; }
h4 { font-size: 11.2pt; color: #385170; margin-top: 12px; margin-bottom: 6px; }
table { border-collapse: collapse; width: 100%; margin: 12px 0 16px 0; font-size: 9.7pt; table-layout: fixed; }
th, td { border: 1px solid #d6dde5; padding: 7px 9px; text-align: left; vertical-align: top; word-break: break-word; }
th { background: #eef4f8; font-weight: 700; }
tr:nth-child(even) { background: #fafcfd; }
blockquote { border-left: 4px solid #1a5276; color: #4a5568; margin: 12px 0; background: #f7fafc; padding: 10px 14px; border-radius: 0 6px 6px 0; }
code { background: #f1f5f9; padding: 2px 5px; border-radius: 3px; font-size: 9.6pt; }
pre { background: #f8fafc; padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 9.2pt;
      white-space: pre-wrap; word-break: break-word; border: 1px solid #e2e8f0; }
strong { color: #111827; }
hr { border: none; border-top: 1px solid #d8e1e8; margin: 22px 0; }
section, table, blockquote { page-break-inside: avoid; }
@page { size: A4; margin: 1.6cm; @bottom-center { content: counter(page) " / " counter(pages); font-size: 9pt; color: #888; } }
"""


_EMOJI_MAP = {
    '✅': '[OK]', '❌': '[X]', '⚠️': '[!]', '🔥': '[HOT]', '❄️': '[COLD]',
    '🐂': '[BULL]', '🐻': '[BEAR]', '⚖️': '[BAL]', '⚡': '[ZAP]',
    '📊': '[CHART]', '📡': '[SAT]', '🌍': '[GLOBE]', '🏭': '[IND]',
    '👁': '[EYE]', '🟠': '[ORG]', '🟡': '[YEL]', '🟢': '[GRN]', '🔴': '[RED]',
    '📄': '[DOC]', '📧': '[MAIL]', '🏠': '[HOME]', '🔐': '[KEY]',
    '🧠': '[BRAIN]', '🎵': '[MUSIC]', '🔍': '[SEARCH]', '️': '',
    '💡': '[TIP]', '📈': '[UP]', '📉': '[DOWN]', '🚨': '[ALERT]',
    '1️⃣': '(1)', '2️⃣': '(2)', '3️⃣': '(3)', '4️⃣': '(4)', '5️⃣': '(5)',
}

def _strip_emoji(text: str) -> str:
    """将 emoji 替换为纯文本标记，避免 PDF 乱码"""
    for emoji, replacement in _EMOJI_MAP.items():
        text = text.replace(emoji, replacement)
    # 兜底：移除剩余的常见 emoji 范围
    import re
    text = re.sub(r'[\U0001F300-\U0001F9FF\u2600-\u26FF\u2700-\u27BF]', '', text)
    return text

def _build_html(md_text: str) -> str:
    md_text = _strip_emoji(md_text)
    html_body = markdown.markdown(md_text, extensions=['tables', 'fenced_code', 'toc'])
    return f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{_CSS}</style></head><body>{html_body}</body></html>'


def _try_weasyprint(html_str: str, pdf_path: str) -> bool:
    """尝试用 WeasyPrint 生成 PDF（纯 Python，不依赖 Chrome）"""
    try:
        from weasyprint import HTML
        HTML(string=html_str).write_pdf(pdf_path)
        return True
    except Exception as e:
        print(f"WeasyPrint failed: {e}", file=sys.stderr)
        return False


def _try_puppeteer(html_path: str, pdf_path: str) -> bool:
    """尝试用 Puppeteer + Chrome 生成 PDF"""
    js = f"""
const puppeteer = require('puppeteer');
(async () => {{
  const browser = await puppeteer.launch({{headless: true, args: ['--no-sandbox','--disable-gpu','--disable-dev-shm-usage']}});
  const page = await browser.newPage();
  await page.goto('file://{html_path}', {{waitUntil: 'networkidle0'}});
  await page.pdf({{path: '{pdf_path}', format: 'A4', margin: {{top: '1.5cm', bottom: '1.5cm', left: '1.5cm', right: '1.5cm'}}, printBackground: true}});
  await browser.close();
}})();
"""
    try:
        result = subprocess.run(
            ['node', '-e', js],
            capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parents[2]),
            timeout=30
        )
        return result.returncode == 0
    except Exception as e:
        print(f"Puppeteer failed: {e}", file=sys.stderr)
        return False


def md_to_pdf(md_path: str, pdf_path: str):
    with open(md_path, 'r', encoding='utf-8') as f:
        md_text = f.read()

    html_str = _build_html(md_text)

    # 保存 HTML 副本（方便调试和浏览器打印）
    html_path = pdf_path.replace('.pdf', '.html')
    with open(html_path, 'w') as f:
        f.write(html_str)

    # 优先 WeasyPrint
    if _try_weasyprint(html_str, pdf_path):
        print(f"OK (weasyprint) → {pdf_path}")
        return

    # 降级 Puppeteer
    abs_html = str(Path(html_path).resolve())
    if _try_puppeteer(abs_html, pdf_path):
        print(f"OK (puppeteer) → {pdf_path}")
        return

    print("ERROR: Both WeasyPrint and Puppeteer failed. HTML saved at:", html_path, file=sys.stderr)
    sys.exit(1)


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: md_to_pdf.py input.md output.pdf", file=sys.stderr)
        sys.exit(1)
    md_to_pdf(sys.argv[1], sys.argv[2])
