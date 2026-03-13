#!/usr/bin/env python3
"""Markdown → PDF 完整流水线（中文+emoji友好）

用法: python3 md_to_pdf.py input.md output.pdf
"""
import sys
import subprocess
from pathlib import Path
import markdown

def md_to_pdf(md_path, pdf_path):
    with open(md_path, 'r') as f:
        md_text = f.read()
    
    html_body = markdown.markdown(md_text, extensions=['tables', 'fenced_code', 'toc'])
    
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>A股分析报告</title>
<style>
  body {{ font-family: "Noto Sans CJK SC", "PingFang SC", sans-serif;
         font-size: 11pt; line-height: 1.7; max-width: 800px; margin: 0 auto; padding: 40px; color: #222; }}
  p {{ text-indent: 2em; margin: 0.7em 0; text-align: justify; }}
  li p, blockquote p, td p, th p {{ text-indent: 0; }}
  h1 {{ font-size: 20pt; border-bottom: 2px solid #1a5276; padding-bottom: 8px; color: #1a5276; }}
  h2 {{ font-size: 15pt; color: #1a5276; margin-top: 28px; border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
  h3 {{ font-size: 12pt; color: #2e4057; }}
  table {{ border-collapse: collapse; width: 100%; margin: 14px 0; font-size: 10pt; }}
  th, td {{ border: 1px solid #ccc; padding: 7px 10px; text-align: left; }}
  th {{ background: #eaf2f8; font-weight: 600; }}
  tr:nth-child(even) {{ background: #fafbfc; }}
  blockquote {{ border-left: 3px solid #1a5276; padding-left: 14px; color: #555; margin: 14px 0; background: #f8f9fa; padding: 10px 14px; }}
  code {{ background: #f0f0f0; padding: 2px 5px; border-radius: 3px; font-size: 10pt; }}
  strong {{ color: #111; }}
  hr {{ border: none; border-top: 1px solid #ddd; margin: 24px 0; }}
  @media print {{ body {{ margin: 0; padding: 20px; }} @page {{ size: A4; margin: 1.5cm; }} }}
</style>
</head><body>{html_body}</body></html>"""
    
    html_path = pdf_path.replace('.pdf', '.html')
    with open(html_path, 'w') as f:
        f.write(html)
    
    # Puppeteer PDF
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
    result = subprocess.run(['node', '-e', js], capture_output=True, text=True, 
                          cwd=str(Path(__file__).resolve().parents[2]), timeout=30)
    if result.returncode != 0:
        print(f"ERROR: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    
    print(f"OK → {pdf_path}")

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: md_to_pdf.py input.md output.pdf", file=sys.stderr)
        sys.exit(1)
    md_to_pdf(sys.argv[1], sys.argv[2])
