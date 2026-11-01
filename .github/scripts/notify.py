"""查价后检查 2/11（或 2/10）去、2/15 回 的往返价有没有降，降了就推送到微信（PushPlus 或 Server酱）。"""
import json
import os
import sys
import urllib.parse
import urllib.request

SITE = "https://jerry0913.github.io/haneda-fares/"
CTRIP = "https://flights.ctrip.com/online/list/round-bjs-hnd?depdate=2027-02-11_2027-02-15&cabin=y_s&adult=1"
LABELS = {"d11": "2/11 去 · 2/15 回", "d10": "2/10 去 · 2/15 回"}
LEVELS = {"low": "偏低", "typical": "正常", "high": "偏高"}


def yuan(n):
    return f"¥{int(n):,}"


def load():
    with open("prices.json", encoding="utf-8") as f:
        data = json.load(f)
    rows = [r for r in data.get("rows", []) if r.get("trip") in LABELS and r.get("price")]
    for r in rows:
        r["price"] = int(r["price"])
    rows.sort(key=lambda r: r.get("checked_at") or r.get("checked") or "")
    return rows


def detail(r):
    bits = [r.get("airline") or "", r.get("airport") or ""]
    if r.get("stops") == 0:
        bits.append("直飞")
    elif isinstance(r.get("stops"), int):
        bits.append(f"转机{r['stops']}次")
    s = " ".join(b for b in bits if b)
    lvl = LEVELS.get(r.get("price_level") or "")
    if lvl:
        s += f"；Google 判断价格{lvl}"
        if r.get("typical_low") and r.get("typical_high"):
            s += f"（这段日子通常 {yuan(r['typical_low'])}–{yuan(r['typical_high'])}）"
    return s


def changes_for(rows, ids):
    out = []
    for r in rows:
        if r["id"] not in ids:
            continue
        before = [x for x in rows if x["trip"] == r["trip"] and (x.get("checked_at") or "") < (r.get("checked_at") or "")]
        if not before:
            out.append(("first", r, None, None))
            continue
        last, low = before[-1], min(before, key=lambda x: x["price"])
        if r["price"] < last["price"]:
            out.append(("drop", r, last, r["price"] < low["price"]))
    out.sort(key=lambda c: 0 if c[1]["trip"] == "d11" else 1)
    return out


def message(changes):
    lines, title = [], None
    for kind, r, last, new_low in changes:
        name = LABELS[r["trip"]]
        if kind == "first":
            t = f"{name} 往返 {yuan(r['price'])}"
            lines.append(f"**{name}**：第一次记录到往返 **{yuan(r['price'])}**\n{detail(r)}")
        else:
            tag = "，历史新低" if new_low else ""
            t = f"{name} 降到 {yuan(r['price'])}{tag}"
            lines.append(f"**{name}**：{yuan(last['price'])} → **{yuan(r['price'])}**，降了 {yuan(last['price'] - r['price'])}{tag}\n{detail(r)}")
        title = title or t
    body = "\n\n".join(lines) + f"\n\n[打开价格看板]({SITE})　[去携程买]({CTRIP})"
    return "机票降价：" + title, body


def send(title, body):
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    key = os.environ.get("SERVERCHAN_KEY", "").strip()
    if token:
        req = urllib.request.Request(
            "https://www.pushplus.plus/send",
            data=json.dumps({"token": token, "title": title, "content": body, "template": "markdown"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
    elif key:
        req = urllib.request.Request(
            f"https://sctapi.ftqq.com/{key}.send",
            data=urllib.parse.urlencode({"title": title[:32], "desp": body}).encode("utf-8"),
        )
    else:
        print("没有设置 PUSHPLUS_TOKEN 或 SERVERCHAN_KEY，跳过推送。")
        return
    with urllib.request.urlopen(req, timeout=20) as resp:
        text = resp.read().decode("utf-8", "replace")
    print("推送结果：", text[:300])
    try:
        code = json.loads(text).get("code")
    except ValueError:
        code = None
    if code not in (200, 0):
        sys.exit("推送失败，请检查 token 是否正确。")


def main():
    rows = load()
    if os.environ.get("MODE") == "test":
        latest = {}
        for r in rows:
            latest[r["trip"]] = r
        lines = [f"**{LABELS[t]}**：{yuan(latest[t]['price'])}（{latest[t].get('checked_at', '')[:16].replace('T', ' ')} 查到）" for t in LABELS if t in latest]
        body = "微信降价提醒已经接通。2/11（或 2/10）去、2/15 回 的往返价一降，就会推送到这里。"
        if lines:
            body += "\n\n现在的价格：\n\n" + "\n\n".join(lines)
        body += f"\n\n[打开价格看板]({SITE})"
        send("测试：机票降价提醒已接通", body)
        return
    try:
        with open("new_rows.json", encoding="utf-8") as f:
            ids = set(json.load(f))
    except FileNotFoundError:
        last_by_trip = {}
        for r in rows:
            last_by_trip[r["trip"]] = r["id"]
        ids = set(last_by_trip.values())
    changes = changes_for(rows, ids)
    if not changes:
        print("这次没有降价，不推送。")
        return
    send(*message(changes))


if __name__ == "__main__":
    main()
