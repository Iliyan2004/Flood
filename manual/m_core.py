import os, re
from PIL import Image
HERE = os.path.dirname(os.path.abspath(__file__)); SH = f"{HERE}/shots"; FG = f"{HERE}/figs"; IMG = f"{HERE}/build/img"; os.makedirs(IMG, exist_ok=True)
TOC = []          # (level, id, title)
_ctr = {"c": 0, "s": 0}

def web(path, w=1500, q=84):
    out = f"{IMG}/{os.path.basename(path).rsplit('.', 1)[0]}.jpg"
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(path):
        im = Image.open(path).convert("RGB")
        if im.width > w: im = im.resize((w, int(im.height * w / im.width)), Image.LANCZOS)
        im.save(out, "JPEG", quality=q, optimize=True)
    return out

def chap(title, key):
    _ctr["c"] += 1; _ctr["s"] = 0; TOC.append((1, key, f"{_ctr['c']}  {title}"))
    return f'<h1 class="chap" id="{key}"><span class="num">{_ctr["c"]}</span> {title}</h1>'

def sec(title, key):
    _ctr["s"] += 1; TOC.append((2, key, f"{_ctr['c']}.{_ctr['s']}  {title}"))
    return f'<h2 id="{key}">{_ctr["c"]}.{_ctr["s"]}  {title}</h2>'

def steps(*items): return '<ol class="steps">' + "".join(f"<li>{i}</li>" for i in items) + "</ol>"
def tip(t, kind=""): 
    lab = {"": "Tip", "warn": "Important", "note": "Note"}[kind]
    return f'<div class="callout {kind}"><b class="t">{lab}</b>{t}</div>'
def warn(t): return tip(t, "warn")
def note(t): return tip(t, "note")

def shot(name, caption, legend=None, w=1500):
    p = f"{SH}/{name}.png"
    if not os.path.exists(p): return f'<p class="small"><i>[screenshot {name} not available]</i></p>'
    leg = ('<ol class="legend">' + "".join(f"<li>{x}</li>" for x in legend) + "</ol>") if legend else ""
    return f'<figure><img class="shot" src="{web(p, w)}"><figcaption>{caption}</figcaption>{leg}</figure>'

def fig(name, caption, width="100%"):
    return f'<figure><img class="fig" src="{FG}/{name}.png" style="width:{width}"><figcaption>{caption}</figcaption></figure>'

def toc_html():
    out = '<div class="toc"><h1>Contents</h1><ul>'
    for lv, key, t in TOC:
        out += f'<li class="l{lv}"><a href="#{key}">{t}</a></li>'
    return out + "</ul></div>"
