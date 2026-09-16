from pathlib import Path


def apply_replacements(text: str, replacements: dict[str, str]) -> tuple[str, int]:
    changed = 0
    for old, new in replacements.items():
        if old in text:
            count = text.count(old)
            text = text.replace(old, new)
            changed += count
    return text, changed


# Live Sheet: remove every nested f-string from the large outer HTML f-string.
live = Path("packages/ai-engine/dashboard/pages/1_Live_Sheet.py")
text = live.read_text(encoding="utf-8")
replacements = {
    "{f' <span style=\"font-size:0.6rem;color:#666;\">{sl_source}</span>' if sl_source else ''}": "{sl_source_html}",
    "{f' <span style=\"font-size:0.6rem;color:#666;\">{tp1_source}</span>' if tp1_source else ''}": "{tp1_source_html}",
    "{f'<span style=\"font-size:0.68rem;color:#3b82f6;\">→{rr_2:.1f}x</span>' if rr_2 > 0 else ''}": "{rr2_html}",
    "{f'<span style=\"font-size:0.68rem;color:#8b5cf6;\">→{rr_3:.1f}x</span>' if rr_3 > 0 else ''}": "{rr3_html}",
    "{f'<div style=\"display:flex; gap:8px; font-size:0.72rem; margin-top:2px;\">{levels_html}</div>' if levels_html else ''}": "{levels_html_card}",
}
text, changed = apply_replacements(text, replacements)

marker = "        # Precompute optional TP2/TP3 HTML outside the outer card f-string.\n"
if marker not in text:
    raise SystemExit(f"{live}: TP helper marker not found")
helper = """        # Precompute all nested HTML snippets outside the outer card f-string.
        sl_source_html = f'<span style=\"font-size:0.6rem;color:#666;\">{sl_source}</span>' if sl_source else ''
        tp1_source_html = f'<span style=\"font-size:0.6rem;color:#666;\">{tp1_source}</span>' if tp1_source else ''
        rr2_html = f'<span style=\"font-size:0.68rem;color:#3b82f6;\">→{rr_2:.1f}x</span>' if rr_2 > 0 else ''
        rr3_html = f'<span style=\"font-size:0.68rem;color:#8b5cf6;\">→{rr_3:.1f}x</span>' if rr_3 > 0 else ''
        levels_html_card = f'<div style=\"display:flex; gap:8px; font-size:0.72rem; margin-top:2px;\">{levels_html}</div>' if levels_html else ''
"""
if "sl_source_html =" not in text:
    text = text.replace(marker, helper + marker, 1)

live.write_text(text, encoding="utf-8")
print(f"{live}: applied {changed} nested-f-string replacements")


# Smart Money: remove nested R:R badge f-strings from the outer card HTML.
smart = Path("packages/ai-engine/dashboard/pages/2_Smart_Money.py")
text = smart.read_text(encoding="utf-8")
replacements = {
    "{f'<span style=\"font-size:0.65rem;color:#3b82f6;\">→{rr_2:.1f}x</span>' if rr_2 > 0 else ''}": "{rr2_html}",
    "{f'<span style=\"font-size:0.65rem;color:#8b5cf6;\">→{rr_3:.1f}x</span>' if rr_3 > 0 else ''}": "{rr3_html}",
}
text, changed = apply_replacements(text, replacements)

marker = "    # Precompute optional TP2/TP3 HTML outside the outer card f-string.\n"
if marker not in text:
    raise SystemExit(f"{smart}: TP helper marker not found")
helper = """    # Precompute all nested HTML snippets outside the outer card f-string.
    rr2_html = f'<span style=\"font-size:0.65rem;color:#3b82f6;\">→{rr_2:.1f}x</span>' if rr_2 > 0 else ''
    rr3_html = f'<span style=\"font-size:0.65rem;color:#8b82f6;\">→{rr_3:.1f}x</span>' if rr_3 > 0 else ''
"""
if "    rr2_html =" not in text:
    text = text.replace(marker, helper + marker, 1)

smart.write_text(text, encoding="utf-8")
print(f"{smart}: applied {changed} nested-f-string replacements")
