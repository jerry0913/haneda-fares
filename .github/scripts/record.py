"""把 Issue 表单里填的平台价格写进 prices.json。"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
PLATFORMS = {"携程": "ctrip", "去哪儿": "qunar", "飞猪": "fliggy", "同程": "tongcheng", "航司官网": "airline", "其他": "other"}
TRIPS = {"2/11 去 · 2/15 回": "d11", "2/10 去 · 2/15 回": "d10"}


def fields(body):
    out, key = {}, None
    for line in body.splitlines():
        if line.startswith("### "):
            key = line[4:].strip()
            out[key] = ""
        elif key is not None and line.strip() and line.strip() != "_No response_":
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


def done(msg, ok=True):
    with open("record-result.txt", "w", encoding="utf-8") as f:
        f.write(msg)
    print(msg)
    if not ok:
        sys.exit(1)


def main():
    f = fields(os.environ.get("ISSUE_BODY", ""))
    plat = PLATFORMS.get(f.get("平台", ""))
    trip = TRIPS.get(f.get("日期", ""))
    price_key = next((k for k in f if k.startswith("直飞往返价")), None)
    digits = re.sub(r"[^\d]", "", f.get(price_key, "") if price_key else "")
    price = int(digits) if digits else 0
    airline = next((v for k, v in f.items() if k.startswith("航司")), "")[:20]
    if not plat or not trip:
        done("没记上：平台或日期没选。", ok=False)
    if not 1000 <= price <= 60000:
        done(f"没记上：价格 “{f.get(price_key, '')}” 看起来不对，请填往返总价，比如 5228。", ok=False)
    created = os.environ.get("ISSUE_CREATED", "")
    try:
        when = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(JST)
    except ValueError:
        when = datetime.now(JST)
    with open("prices.json", encoding="utf-8") as fh:
        data = json.load(fh)
    num = os.environ.get("ISSUE_NUMBER", "")
    row = {
        "id": when.strftime("%Y-%m-%dT%H:%M") + f"_{trip}_{plat}_{num}",
        "checked": when.strftime("%Y-%m-%d"),
        "checked_at": when.isoformat(timespec="minutes"),
        "trip": trip,
        "trip_label": f.get("日期", ""),
        "price": price,
        "airline": airline,
        "stops": 0,
        "nonstop": True,
        "src": plat,
        "source": f"{f.get('平台')}（手动记录）",
    }
    data.setdefault("rows", []).append(row)
    with open("prices.json", "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    done(f"已记录：{f.get('平台')} {f.get('日期')} 直飞往返 ¥{price:,}{('（' + airline + '）') if airline else ''}。网站一两分钟后更新。")


if __name__ == "__main__":
    main()
