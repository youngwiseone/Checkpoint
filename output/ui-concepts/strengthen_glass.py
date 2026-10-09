from pathlib import Path
p=Path('backend/checkpoint/desktop/windows.py')
s=p.read_text(encoding='utf-8').replace('colors.alpha(c.card, 175)','colors.alpha(c.card, 210)').replace('colors.alpha(c.card, 160)','colors.alpha(c.card, 205)').replace('colors.alpha(c.field, 165)','colors.alpha(c.field, 210)').replace('colors.alpha(c.button, 165)','colors.alpha(c.button, 205)')
p.write_text(s,encoding='utf-8')
