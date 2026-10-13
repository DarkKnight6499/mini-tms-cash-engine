"""Portable Markdown tables and a self-contained static HTML dashboard."""

from html import escape
from pathlib import Path
from typing import Mapping

import pandas as pd


def markdown_table(frame: pd.DataFrame) -> str:
    """Render a table without optional tabulate dependencies."""
    def cell(value: object) -> str:
        if isinstance(value, float):
            return f"{value:,.2f}" if pd.notna(value) else "N/A"
        return str(value).replace("|", "\\|").replace("\n", " ")
    rows = ["| " + " | ".join(map(str, frame.columns)) + " |",
            "| " + " | ".join(":" + chr(45) for _ in frame.columns) + " |"]
    rows.extend("| " + " | ".join(cell(x) for x in row) + " |" for row in frame.itertuples(index=False, name=None))
    return "\n".join(rows)


def dashboard(path: Path, summaries: Mapping[str, float | int | str],
              position: pd.DataFrame, forecast: pd.DataFrame, exceptions: pd.DataFrame) -> None:
    """Write escaped HTML with inline CSS and no scripts or remote assets."""
    cards = "".join(f'<div class="card"><strong>{escape(str(k))}</strong><p>{escape(str(v))}</p></div>'
                    for k, v in summaries.items())
    sections = "".join(f"<section><h2>{escape(title)}</h2>{frame.to_html(index=False, escape=True, border=0, float_format=lambda x: f'{x:,.2f}')}</section>"
                       for title, frame in (("Latest position, synthetic", position),
                                            ("Weekly forecast, USD", forecast),
                                            ("Statement exceptions", exceptions)))
    html = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Synthetic treasury workflow</title><style>
body {{font:16px system-ui,sans-serif;margin:0;color:#18212c;background:#f6f7f9}}
main {{max-width:1200px;margin:auto;padding:28px}}
h1 {{font-size:30px}} h2 {{font-size:22px}}
.cards {{display:flex;gap:12px;flex-wrap:wrap}} .card {{padding:16px;background:white;border:1px solid #d5dae1;flex:1;min-width:180px}}
section {{margin-top:24px;overflow:auto;background:white;padding:20px;border:1px solid #d5dae1}}
table {{border-collapse:collapse;width:100%;font-size:14px}} th,td {{padding:9px;text-align:right;border-bottom:1px solid #ddd}}
th {{text-align:left}} p {{line-height:1.5}} .note {{max-width:900px}}
</style></head><body><main><h1>Synthetic treasury workflow</h1>
<p class="note">Seeded synthetic data. Prototype workflow, not a replacement for a commercial treasury system.
Balances with exceptions are quarantined. Collection probabilities, buffers, rates and hedge ratios are stylized assumptions.
Payment output has structural validation only. No bank connectivity.</p><div class="cards">{cards}</div>{sections}
</main></body></html>'''
    path.write_text(html, encoding="utf-8")
