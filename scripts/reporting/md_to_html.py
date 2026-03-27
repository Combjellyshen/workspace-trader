#!/usr/bin/env python3
"""Markdown → HTML 渲染器（供浏览器打PDF）"""
import sys
import markdown

def md_to_html(md_path, html_path):
    import re
    with open(md_path, 'r') as f:
        md_text = f.read()

    # Step 1: 确保非表格行后紧跟表格时补一个空行（Markdown要求表格前有空行）
    md_text = re.sub(r'([^|\n][^\n]*)\n(\|)', r'\1\n\n\2', md_text)

    # Step 2: 去掉表格行之间的空行（AI常在每行后加空行，导致tables扩展无法解析）
    # 必须在Step1之后执行，否则两步会互相抵消
    prev = None
    while prev != md_text:
        prev = md_text
        md_text = re.sub(r'(\|[^\n]*)\n\n(\|)', r'\1\n\2', md_text)

    html_body = markdown.markdown(md_text, extensions=['tables', 'fenced_code', 'toc'])
    
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>A股分析报告</title>
<style>
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
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
    
    with open(html_path, 'w') as f:
        f.write(html)
    print(f"OK → {html_path}")

if __name__ == '__main__':
    md_to_html(sys.argv[1], sys.argv[2])
