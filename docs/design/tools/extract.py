"""从设计稿 HTML 抽取章节结构为 JSON，供 docx 生成脚本使用。"""
import json
import re
import sys

from bs4 import BeautifulSoup, NavigableString, Tag

TAG_COLORS = {"t-p1": "6A4FD8", "t-p2": "1263F0", "t-p3": "9A6B12", "t-ok": "22A05A", "t-warn": "C27A00",
              "t-bad": "D01F1F", "t-sc": "D2541B", "t-mute": "6B7A8C", "t-new": "1263F0"}


def runs(el, style=None):
    style = dict(style or {})
    out = []
    for c in el.children:
        if isinstance(c, NavigableString):
            t = re.sub(r"\s+", " ", str(c))
            if t:
                out.append({"t": t, **style})
        elif isinstance(c, Tag):
            if c.name == "br":
                out.append({"t": " ", **style})
                continue
            st = dict(style)
            if c.name in ("b", "strong", "th"):
                st["b"] = True
            if c.name == "code" or "mono" in (c.get("class") or []):
                st["code"] = True
            if c.name == "small":
                st["small"] = True
            cls = c.get("class") or []
            if "tag" in cls:
                col = next((TAG_COLORS[k] for k in cls if k in TAG_COLORS), "333333")
                out.append({"t": "【" + c.get_text(" ", strip=True) + "】", "b": True, "color": col})
                continue
            if c.name == "small":
                out.append({"t": "（", **st})
                out += runs(c, st)
                out.append({"t": "）", **st})
                continue
            out += runs(c, st)
    return out


def clean(rs):
    # 合并空白、去掉首尾空格
    while rs and not rs[0]["t"].strip():
        rs.pop(0)
    while rs and not rs[-1]["t"].strip():
        rs.pop()
    if rs:
        rs[0]["t"] = rs[0]["t"].lstrip()
        rs[-1]["t"] = rs[-1]["t"].rstrip()
    return rs


def table_block(tb):
    rows = []
    head = []
    thead = tb.find("thead")
    if thead:
        head = [clean(runs(th, {"b": True})) for th in thead.find_all("th")]
    body = tb.find("tbody") or tb
    for tr in body.find_all("tr", recursive=False):
        cells = []
        for td in tr.find_all(["td", "th"], recursive=False):
            cells.append({"runs": clean(runs(td)), "rowspan": int(td.get("rowspan", 1)),
                          "colspan": int(td.get("colspan", 1))})
        rows.append(cells)
    return {"type": "table", "head": head, "rows": rows}


def walk(container, blocks):
    for el in container.children:
        if not isinstance(el, Tag):
            continue
        cls = el.get("class") or []
        n = el.name
        if n == "h2":
            blocks.append({"type": "h1", "text": el.get_text(" ", strip=True).replace("　", " ")})
        elif n == "h3":
            blocks.append({"type": "h2", "text": el.get_text(" ", strip=True).replace("　", " ")})
        elif n == "h4":
            blocks.append({"type": "h3", "runs": clean(runs(el))})
        elif n == "p":
            if "flow-note" in cls:
                blocks.append({"type": "note", "runs": clean(runs(el))})
            else:
                blocks.append({"type": "p", "runs": clean(runs(el))})
        elif n == "ul":
            blocks.append({"type": "ul", "items": [clean(runs(li)) for li in el.find_all("li", recursive=False)]})
        elif "tbl" in cls:
            t = el.find("table")
            if t:
                blocks.append(table_block(t))
        elif "flow" in cls:
            steps = []
            for x in el.children:
                if isinstance(x, Tag) and "node" in (x.get("class") or []):
                    sm = x.find("small")
                    sub = sm.get_text(" ", strip=True) if sm else ""
                    if sm:
                        sm.extract()
                    kind = "dec" if "dec" in x["class"] else ("end" if "end" in x["class"] else
                                                                ("opt" if "opt" in x["class"] else ""))
                    steps.append({"name": x.get_text(" ", strip=True), "sub": sub, "kind": kind})
            blocks.append({"type": "flow", "steps": steps})
        elif "states" in cls:
            items = []
            for st in el.find_all(class_="st"):
                em = st.find("em")
                label = em.get_text(strip=True) if em else ""
                if em:
                    em.extract()
                items.append(f"{label}（{st.get_text(strip=True)}）")
            blocks.append({"type": "states", "items": items})
        elif "callout" in cls:
            blocks.append({"type": "callout", "warn": "warn" in cls, "runs": clean(runs(el))})
        elif "grid2" in cls:
            for pn in el.find_all(class_="panel", recursive=False):
                sub = []
                walk(pn, sub)
                # 面板标题作为三级标题
                blocks += sub
        elif "arch" in cls:
            rows = []
            for ly in el.find_all(class_="layer"):
                name = ly.find(class_="lname").get_text(strip=True)
                boxes = []
                for bx in ly.find_all(class_="box"):
                    b = bx.find("b")
                    sm = bx.find("small")
                    boxes.append([b.get_text(strip=True) if b else bx.get_text(strip=True),
                                  sm.get_text(strip=True) if sm else ""])
                rows.append({"layer": name, "boxes": boxes})
            blocks.append({"type": "arch", "rows": rows})
        elif "counters" in cls:
            blocks.append({"type": "counters", "items": [
                [d.contents[0].strip() if d.contents else "", d.find("b").get_text(strip=True)]
                for d in el.find_all("div", recursive=False)]})
        elif n == "footer":
            blocks.append({"type": "p", "runs": clean(runs(el))})
        elif n in ("div", "section"):
            walk(el, blocks)


def main(path, out):
    soup = BeautifulSoup(open(path, encoding="utf-8").read(), "html.parser")
    main_el = soup.find("main")
    home = main_el.find(id="home")
    meta = {"title": home.find("h1").get_text(strip=True), "lead": home.find(class_="lead").get_text(" ", strip=True),
            "meta": [s.get_text(" ", strip=True) for s in home.find(class_="meta").find_all("span")],
            "counters": [[d.contents[0].strip(), d.find("b").get_text(strip=True)]
                         for d in home.find(class_="counters").find_all("div", recursive=False)]}
    blocks = []
    for card in main_el.find_all("section", class_="card", recursive=False):
        if card.get("id") == "home":
            continue
        if card.get("id") == "proto":
            blocks.append({"type": "h1", "text": card.find("h2").get_text(" ", strip=True).replace("　", " ")})
            blocks.append({"type": "p", "runs": clean(runs(card.find(class_="sec-intro")))})
            blocks.append({"type": "protos"})
            continue
        walk(card, blocks)
    json.dump({"meta": meta, "blocks": blocks}, open(out, "w"), ensure_ascii=False, indent=1)
    print(len(blocks), "blocks")


main(sys.argv[1], sys.argv[2])
