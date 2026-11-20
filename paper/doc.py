"""Tiny document model: automatic numbering of sections, figures, tables, equations and citations, resolved after assembly."""
import json, os, re, html

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
FIGDIR = os.path.join(HERE, "figures")


def load(name, default=None):
    p = os.path.join(DATA, name)
    if os.path.exists(p):
        try:
            return json.load(open(p))
        except Exception:
            return default
    return default


class Doc:
    def __init__(self, refs):
        self.refs = refs
        self.secs, self.figs, self.tabs, self.eqs = {"repro": "A", "metrics": "B", "schema": "C"}, {}, {}, {}
        self.s1 = self.s2 = 0
        self.nf = self.nt = self.ne = 0
        self.parts = []

    # ---- registries -----------------------------------------------------
    def h(self, level, key, title):
        if level == 2:
            self.s1 += 1; self.s2 = 0; num = f"{self.s1}"
        else:
            self.s2 += 1; num = f"{self.s1}.{self.s2}"
        self.secs[key] = num
        tag = "h2" if level == 2 else "h3"
        return f'<{tag} id="s-{key}"><span class="n">{num}</span> {title}</{tag}>'

    def appendix(self, key, letter, title):
        self.secs[key] = letter
        return f'<h2 id="s-{key}"><span class="n">{letter}</span> {title}</h2>'

    def fig(self, key, img, caption, width="100%"):
        self.nf += 1; self.figs[key] = self.nf
        return (f'<figure id="f-{key}"><img src="{os.path.join(FIGDIR, img)}" style="width:{width}">'
                f'<figcaption><b>Figure {self.nf}.</b> {caption}</figcaption></figure>')

    def table(self, key, caption, thead, rows, note=None, cls=None, numcols=(), hl=()):
        self.nt += 1; self.tabs[key] = self.nt
        def cell(tag, i, c):
            return f'<{tag}{" class=num" if i in numcols else ""}>{c}</{tag}>'
        th = "".join(cell("th", i, c) for i, c in enumerate(thead))
        body = ""
        for r, row in enumerate(rows):
            body += f'<tr{" class=hl" if r in hl else ""}>' + "".join(cell("td", i, c) for i, c in enumerate(row)) + "</tr>"
        n = f'<p class="tnote">{note}</p>' if note else ""
        return (f'<div class="tablewrap" id="t-{key}"><table class="t"><caption><b>Table {self.nt}.</b> {caption}</caption>'
                f'<thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>{n}</div>')

    def eq(self, key, tex, size=13):
        import eqs
        self.ne += 1; self.eqs[key] = self.ne
        path = eqs.render(tex, size)
        return f'<div class="eq" id="e-{key}"><div class="m"><img src="{path}"></div><div class="t">({self.ne})</div></div>'

    def add(self, *chunks):
        self.parts.extend(chunks)

    # ---- resolution -----------------------------------------------------
    def render(self):
        body = "\n".join(self.parts)
        order = []

        def cite(m):
            keys = [k.strip() for k in m.group(1).split(",")]
            nums = []
            for k in keys:
                if k not in self.refs:
                    raise KeyError(f"unknown reference {k}")
                if k not in order:
                    order.append(k)
                nums.append(order.index(k) + 1)
            nums = sorted(set(nums))
            # compress runs
            out, i = [], 0
            while i < len(nums):
                j = i
                while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
                    j += 1
                out.append(str(nums[i]) if i == j else (f"{nums[i]}, {nums[j]}" if j == i + 1 else f"{nums[i]}–{nums[j]}"))
                i = j + 1
            return "[" + ", ".join(out) + "]"
        body = re.sub(r"\[\[c:([^\]]+)\]\]", cite, body)
        body = re.sub(r"\[\[f:([^\]]+)\]\]", lambda m: f'<a href="#f-{m.group(1)}">Figure {self.figs[m.group(1)]}</a>', body)
        body = re.sub(r"\[\[t:([^\]]+)\]\]", lambda m: f'<a href="#t-{m.group(1)}">Table {self.tabs[m.group(1)]}</a>', body)
        body = re.sub(r"\[\[e:([^\]]+)\]\]", lambda m: f'<a href="#e-{m.group(1)}">({self.eqs[m.group(1)]})</a>', body)
        body = re.sub(r"\[\[s:([^\]]+)\]\]", lambda m: f'<a href="#s-{m.group(1)}">{self.secs[m.group(1)]}</a>', body)
        refs_html = "<ol class='refs'>" + "".join(f"<li>[{i + 1}] {self.refs[k]}</li>" for i, k in enumerate(order)) + "</ol>"
        return body, refs_html, order


APPX = {"repro", "metrics", "schema"}


def todo(text):
    return f'<span class="todo">TODO: {html.escape(text)}</span>'
