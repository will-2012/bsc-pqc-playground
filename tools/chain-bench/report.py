#!/usr/bin/env python3
"""Render the chain-bench Markdown report to a standalone, offline HTML page."""
import html
import json
from pathlib import Path
import re
import statistics

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / 'docs/chain-bench'


def inline(text):
    # Only the inline Markdown constructs used by the report are supported.
    text = html.escape(text)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    return re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)


def render(lines):
    blocks, toc = [], []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith('```'):
            code = []
            i += 1
            while i < len(lines) and not lines[i].startswith('```'):
                code.append(lines[i])
                i += 1
            blocks.append('<pre><code>' + html.escape('\n'.join(code)) + '</code></pre>')
            i += 1
        elif line.startswith('# '):
            i += 1  # Title is rendered in the page header.
        elif line.startswith('## '):
            title = line[3:]
            anchor = f'section-{len(toc)+1}'
            toc.append((anchor, title))
            blocks.append(f'<h2 id="{anchor}">{inline(title)}</h2>')
            i += 1
        elif line.startswith('|'):
            table = []
            while i < len(lines) and lines[i].startswith('|'):
                table.append([c.strip() for c in lines[i].strip('|').split('|')])
                i += 1
            assert len(table) >= 2 and all(re.fullmatch(r':?-+:?', c) for c in table[1])
            align = [' class="number"' if c.endswith(':') else '' for c in table[1]]
            rows = ['<thead><tr>' + ''.join(f'<th{a}>{inline(c)}</th>' for a, c in zip(align, table[0])) + '</tr></thead><tbody>']
            for row in table[2:]:
                assert len(row) == len(align)
                rows.append('<tr>' + ''.join(f'<td{a}>{inline(c)}</td>' for a, c in zip(align, row)) + '</tr>')
            blocks.append('<div class="table-wrap"><table>' + ''.join(rows) + '</tbody></table></div>')
        elif line.startswith('- '):
            items = []
            while i < len(lines) and lines[i].startswith('- '):
                items.append('<li>' + inline(lines[i][2:]) + '</li>')
                i += 1
            blocks.append('<ul>' + ''.join(items) + '</ul>')
        else:
            paragraph = []
            while i < len(lines) and lines[i].strip() and not lines[i].startswith(('#', '|', '- ', '```')):
                paragraph.append(lines[i])
                i += 1
            if not paragraph:
                raise ValueError(f'Unsupported Markdown block: {lines[i]}')
            blocks.append('<p>' + inline(' '.join(paragraph)) + '</p>')
    return '\n'.join(blocks), toc


def main():
    source = (REPORT / 'README.md').read_text()
    body, toc = render(source.splitlines())
    reference = json.loads((REPORT / 'v3/slh-reference.json').read_text())
    current = json.loads((REPORT / 'v3/results.json').read_text())
    average = lambda data: statistics.mean(int(row['transferred']['outerTransactionGas']) for row in data['rows'])
    transfer = average(current)
    reduction = (1-transfer/average(reference))*100
    price = json.loads((REPORT / 'v3/precompile.json').read_text())['pricing']['Falcon']['selectedGas']
    navigation = ''.join(f'<a href="#{anchor}">{html.escape(title)}</a>' for anchor, title in toc)
    css = '''
:root{color-scheme:light;--ink:#182b43;--muted:#607188;--line:#dfe7ef;--blue:#155ac4;--bg:#f3f6fa}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:24px}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.85 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}a{color:var(--blue);text-underline-offset:3px}header{max-width:1220px;margin:auto;padding:48px 36px 28px}.eyebrow{font-size:12px;letter-spacing:2px;color:var(--blue);font-weight:750}h1{font-size:36px;line-height:1.35;margin:10px 0}header p{color:var(--muted);margin:8px 0}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:24px}.card{background:white;border:1px solid var(--line);border-radius:12px;padding:18px 22px}.card span{color:var(--muted);font-size:14px}.card strong{display:block;font-size:28px;line-height:1.65;font-variant-numeric:tabular-nums}.card small{font-size:13px;color:var(--muted)}.layout{max-width:1220px;margin:0 auto 60px;display:grid;grid-template-columns:210px minmax(0,1fr);gap:28px;padding:0 36px}nav{align-self:start;position:sticky;top:24px;display:grid;gap:9px;padding:20px 0;font-size:14px}nav a{text-decoration:none;color:var(--muted)}nav a:hover{color:var(--blue)}article{min-width:0;background:white;border:1px solid var(--line);border-radius:14px;padding:24px 32px 36px}h2{font-size:23px;margin:32px 0 16px;border-top:1px solid var(--line);padding-top:26px}p{margin:15px 0}code{font:0.86em/1.7 ui-monospace,SFMono-Regular,Consolas,monospace;background:#edf2f8;padding:2px 5px;border-radius:4px;overflow-wrap:anywhere}pre{padding:18px;background:#f6f8fb;border:1px solid var(--line);border-radius:8px;overflow:auto;line-height:1.7}pre code{background:none;padding:0;white-space:pre;overflow-wrap:normal}.table-wrap{overflow:auto;margin:20px 0;border:1px solid var(--line);border-radius:8px}table{border-collapse:collapse;width:100%;font-size:14px;line-height:1.65}th,td{padding:13px 14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;min-width:110px}th{background:#edf3fb;font-weight:650}tr:last-child td{border-bottom:0}tbody tr:nth-child(even){background:#fafbfd}.number{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}li{margin:9px 0}footer{color:var(--muted);font-size:13px;margin-top:30px;padding-top:20px;border-top:1px solid var(--line)}
@media(max-width:900px){.layout{display:block;padding:0 18px}header{padding:28px 18px 22px}nav{position:static;display:flex;flex-wrap:wrap;gap:8px 18px;padding:0 0 20px}h1{font-size:28px}article{padding:10px 20px 25px}.cards{gap:8px}.card{padding:12px}.card strong{font-size:23px}}
@media(max-width:520px){.cards{grid-template-columns:1fr}.card strong{display:inline-block;margin:0 10px}body{font-size:15px}article{padding:8px 15px 20px}h2{font-size:21px}}
@media print{body{background:white;font-size:11px}header{padding:0}.layout{display:block;padding:0;margin:0}nav{display:none}article{border:0;padding:0}.cards{margin:10px 0}.card{padding:8px}h2{break-after:avoid;margin:20px 0 10px;padding-top:14px}.table-wrap,pre{overflow:visible}table{font-size:10px}th,td{padding:7px;min-width:0}.number{white-space:normal}a{color:inherit}header,.layout{max-width:none}}
'''
    title = html.escape(source.splitlines()[0][2:])
    page = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>{css}</style></head>
<body><header><div class="eyebrow">BSC PQC PLAYGROUND · LOCAL EXPERIMENT</div><h1>{title}</h1><p>SLH-DSA-SHA2-128s / Falcon-512 · Solidity 与原生预编译 · 2026-10-27</p>
<div class="cards"><div class="card"><span>v3 Falcon 转账，平均外层 gas</span><strong>{transfer:,.0f} gas</strong><small>三笔实测，原生 EOA 为 21,000 gas</small></div><div class="card"><span>转账 gas 相对同链 SLH 减少</span><strong>{reduction:.2f}%</strong><small>同链、同 Bundler；各三笔样本</small></div><div class="card"><span>Falcon 预编译实验定价</span><strong>{price:,} gas</strong><small>五轮 CPU 测量；含余量与取整</small></div></div></header>
<div class="layout"><nav aria-label="报告目录">{navigation}<a href="README.md">Markdown 源文档 ↗</a></nav><article>{body}<footer>由 README.md 和原始 JSON 自动生成，无外部资源依赖。更新后运行 <code>python3 tools/chain-bench/report.py</code>。</footer></article></div></body></html>'''
    (REPORT / 'report.html').write_text(page)
    print(REPORT / 'report.html')


if __name__ == '__main__':
    main()
