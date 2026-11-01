"""prices.json 更新后检查是否降价，降价就推送到微信（PushPlus 或 Server酱）。"""
import json
import os
import sys
import urllib.parse
import urllib.request

SITE = "https://jerry0913.github.io/haneda-fares/"
CTRIP = "https://flights.ctrip.com/online/list/round-bjs-hnd?depdate=2027-02-11_2027-02-15&cabin=y_s&adult=1"
NAMES = {"exact": "你的日期 2/11–2/15", "pek_feb": "首都–羽田 2月最低", "pkx_feb": "大兴–羽田 2月最低"}
ORDER = ["exact", "pek_feb", "pkx_feb"]


def yuan(n):
    return f"¥{int(n):,}"


def load_rows():
    with open("prices.json", encoding="utf-8") as f:
        data = json.load(f)
    rows = data["rows"] if isinstance(data, dict) else data
    out = []
    for r in rows:
        try:
            out.append({**r, "price": int(float(r["price"])), "checked": str(r["checked"])[:10]})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def fare(r):
    who = f"{r.get('airline') or ''}{r.get('direct') or ''}"
    when = (r.get("fare_dates") or "").replace(" · ", "，")
    return "，".join(x for x in (who, when) if x)


def find_changes(rows):
    if not rows:
        return []
    latest = max(r["checked"] for r in rows)
    changes = []
    for s in ORDER:
        series = sorted((r for r in rows if r.get("series") == s), key=lambda r: r["checked"])
        today = [r for r in series if r["checked"] == latest]
        if not today:
            continue
        cur = today[-1]
        before = [r for r in series if r["checked"] < latest]
        if not before:
            if s == "exact":
                changes.append(("first", s, cur, None, None))
            continue
        last = before[-1]
        low = min(before, key=lambda r: r["price"])
        if cur["price"] < last["price"]:
            changes.append(("drop", s, cur, last, cur["price"] < low["price"]))
    return changes


def build_message(changes):
    lines, title = [], None
    for kind, s, cur, last, new_low in changes:
        if kind == "first":
            t = f"查到你的日期报价 {yuan(cur['price'])}"
            lines.append(f"**{NAMES[s]}**：第一次查到报价 **{yuan(cur['price'])}**（{fare(cur)}）")
        else:
            drop = last["price"] - cur["price"]
            tag = "，历史新低" if new_low else ""
            t = f"{NAMES[s]}降到 {yuan(cur['price'])}{tag}"
            lines.append(f"**{NAMES[s]}**：{yuan(last['price'])} → **{yuan(cur['price'])}**，降了 {yuan(drop)}{tag}（{fare(cur)}）")
        title = title or t
    body = "\n\n".join(lines)
    body += f"\n\n[打开价格看板]({SITE})　[去携程查 2/11–2/15]({CTRIP})"
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
    rows = load_rows()
    if os.environ.get("MODE") == "test":
        latest = {}
        for r in sorted(rows, key=lambda r: r["checked"]):
            latest[r.get("series")] = r
        lines = [f"**{NAMES[s]}**：{yuan(latest[s]['price'])}（{latest[s]['checked']} 查到）" for s in ORDER if s in latest]
        body = "微信降价提醒已经接通。以后价格一降，就会推送到这里。\n\n现在的价格：\n\n" + "\n\n".join(lines)
        body += f"\n\n[打开价格看板]({SITE})"
        send("测试：机票降价提醒已接通", body)
        return
    changes = find_changes(rows)
    if not changes:
        print("这次没有降价，不推送。")
        return
    send(*build_message(changes))


if __name__ == "__main__":
    main()
