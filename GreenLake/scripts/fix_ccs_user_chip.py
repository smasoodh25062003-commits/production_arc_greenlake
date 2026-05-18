from pathlib import Path

p = Path(__file__).resolve().parent.parent / "gldashboard_bundle/app/templates/ccs_layout.html"
t = p.read_text(encoding="utf-8")
tag = "d" + "iv"
old = (
    f'                <{tag} style="display:flex; align-items:center; gap:8px; '
    "background:rgba(255,255,255,0.04); padding:8px 14px; border-radius:20px; "
    f'border:1px solid rgba(255,255,255,0.08);">'
)
new = (
    f'                <{tag} class="ccs-user-chip" style="display:flex; align-items:center; '
    f'gap:8px; padding:8px 14px; border-radius:20px;">'
)
if old in t:
    t = t.replace(old, new, 1)
    t = t.replace("font-weight:500; color:rgba(255,255,255,0.75);", "font-weight:500;", 1)
    p.write_text(t, encoding="utf-8")
    print("updated")
else:
    print("pattern not found")
