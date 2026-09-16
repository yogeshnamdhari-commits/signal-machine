from pathlib import Path


def replace_required(text: str, replacements: dict[str, str], path: Path) -> str:
    changed = False
    for old, new in replacements.items():
        if old in text:
            text = text.replace(old, new)
            changed = True
    if not changed:
        raise SystemExit(f"{path}: none of the expected syntax-defect patterns were found")
    return text


live = Path("packages/ai-engine/dashboard/pages/1_Live_Sheet.py")
text = live.read_text(encoding="utf-8")
replacements = {
    "{f' <span style=\\\"font-size:0.6rem;color:#666;\\\">{sl_source}</span>' if sl_source else ''}": "{sl_source_html}",
    "{f' <span style=\\\"font-size:0.6rem;color:#666;\\\">{tp1_source}</span>' if tp1_source else ''}": "{tp1_source_html}",
    "{f'<span style=\\\"font-size:0.68rem;color:#3b82f6;\\\">→{rr_2:.1f}x</span>' if rr_2 > 0 else ''}": "{rr2_html}",
    "{f'<span style=\\\"font-size:0.68rem;color:#8b5cf6;\\\">→{rr_3:.1f}x</span>' if rr_3 > 0 else ''}": "{rr3_html}",
}
text = replace_required(text, replacements, live)
marker = "        # Precompute optional TP2/TP3 HTML outside the outer card f-string.\n"
helper = """        # Precompute nested HTML snippets outside the outer card f-string.
        sl_source_html = f' <span style=\"font-size:0.6rem;color:#666;\">{sl_source}</span>' if sl_source else ''
        tp1_source_html = f' <span style=\"font-size:0.6rem;color:#666;\">{tp1_source}</span>' if tp1_source else ''
        rr2_html = f'<span style=\"font-size:0.68rem;color:#3b82f6;\">→{rr_2:.1f}x</span>' if rr_2 > 0 else ''
        rr3_html = f'<span style=\"font-size:0.68rem;color:#8b5cf6;\">→{rr_3:.1f}x</span>' if rr_3 > 0 else ''
"""
if marker not in text:
    raise SystemExit(f"{live}: helper marker not found")
if "sl_source_html =" not in text:
    text = text.replace(marker, helper + marker, 1)
live.write_text(text, encoding="utf-8")


smart = Path("packages/ai-engine/dashboard/pages/2_Smart_Money.py")
text = smart.read_text(encoding="utf-8")
replacements = {
    "{f'<span style=\\\"font-size:0.65rem;color:#3b82f6;\\\">→{rr_2:.1f}x</span>' if rr_2 > 0 else ''}": "{rr2_html}",
    "{f'<span style=\\\"font-size:0.65rem;color:#8b5cf6;\\\">→{rr_3:.1f}x</span>' if rr_3 > 0 else ''}": "{rr3_html}",
}
text = replace_required(text, replacements, smart)
marker = "    # Precompute optional TP2/TP3 HTML outside the outer card f-string.\n"
helper = """    # Precompute nested HTML snippets outside the outer card f-string.
    rr2_html = f'<span style=\"font-size:0.65rem;color:#3b82f6;\">→{rr_2:.1f}x</span>' if rr_2 > 0 else ''
    rr3_html = f'<span style=\"font-size:0.65rem;color:#8b5cf6;\">→{rr_3:.1f}x</span>' if rr_3 > 0 else ''
"""
if marker not in text:
    raise SystemExit(f"{smart}: helper marker not found")
if "    rr2_html =" not in text:
    text = text.replace(marker, helper + marker, 1)
smart.write_text(text, encoding="utf-8")

print("Dashboard syntax repairs applied.")
