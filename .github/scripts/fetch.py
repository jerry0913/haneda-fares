"""查 2/11（或 2/10）去、2/15 回 的北京⇄东京羽田直飞往返最低价（去回都直飞），追加到 prices.json。

优先用 SerpApi（设置了 SERPAPI_KEY 时）：人民币报价 + Google 记录的历史价格。
没有 key 时用 faster-flights 直接读 Google Flights。
"""
import json
import os
import re
import sys
import traceback
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
TRIPS = {
    "d11": {"out": "2027-02-11", "ret": "2027-02-15", "label": "2/11 去 · 2/15 回"},
    "d10": {"out": "2027-02-10", "ret": "2027-02-15", "label": "2/10 去 · 2/15 回"},
}
ORIGINS = ["PEK", "PKX"]
DEST = "HND"
LOG = []


def log(*a):
    line = " ".join(str(x) for x in a)
    print(line)
    LOG.append(line)


def to_int(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(round(v))
    digits = re.sub(r"[^\d.]", "", str(v))
    return int(round(float(digits))) if digits else None


# ---------------- SerpApi ----------------
def via_serpapi(trip, key):
    params = {
        "engine": "google_flights",
        "departure_id": ",".join(ORIGINS),
        "arrival_id": DEST,
        "outbound_date": trip["out"],
        "return_date": trip["ret"],
        "type": "1",
        "stops": "1",
        "travel_class": "1",
        "adults": "1",
        "currency": "CNY",
        "hl": "zh-cn",
        "api_key": key,
    }
    url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as r:
        data = json.load(r)
    if data.get("error"):
        raise RuntimeError(data["error"])
    options = (data.get("best_flights") or []) + (data.get("other_flights") or [])
    options = [o for o in options if to_int(o.get("price"))]
    if not options:
        raise RuntimeError("SerpApi 没有返回带价格的航班")
    options = [o for o in options if len(o.get("flights") or []) == 1]
    if not options:
        raise RuntimeError("SerpApi 没有直飞航班")
    best = min(options, key=lambda o: to_int(o["price"]))
    legs = best.get("flights") or [{}]
    first = legs[0]
    ins = data.get("price_insights") or {}
    hist = []
    for ts, price in ins.get("price_history") or []:
        day = datetime.fromtimestamp(int(ts), JST).strftime("%Y-%m-%d")
        hist.append([day, to_int(price)])
    rng = ins.get("typical_price_range") or [None, None]
    return {
        "price": to_int(best["price"]),
        "airline": first.get("airline") or "",
        "airport": (first.get("departure_airport") or {}).get("id") or "",
        "depart_time": ((first.get("departure_airport") or {}).get("time") or "")[-5:],
        "price_level": ins.get("price_level") or "",
        "typical_low": to_int(rng[0]) if len(rng) > 0 else None,
        "typical_high": to_int(rng[1]) if len(rng) > 1 else None,
        "source": "Google Flights（SerpApi）",
        "stops": 0,
        "nonstop": True,
    }, hist


# ---------------- faster-flights ----------------
def ff_option(f, origin):
    price = to_int(getattr(f, "price", None))
    airlines = getattr(f, "airlines", None) or []
    if isinstance(airlines, str):
        airlines = [airlines]
    legs = getattr(f, "flights", None) or []
    dep = ""
    if legs:
        t = getattr(getattr(legs[0], "departure", None), "time", None) or []
        if t:
            dep = f"{int(t[0]):02d}:{int(t[1]) if len(t) > 1 else 0:02d}"
    via = [getattr(getattr(l, "to_airport", None), "code", "") for l in legs[:-1]]
    return {
        "price": price,
        "airline": "、".join(dict.fromkeys(str(a) for a in airlines)),
        "airport": origin,
        "stops": max(0, len(legs) - 1) if legs else None,
        "via": "、".join(v for v in via if v),
        "depart_time": dep,
    }


def via_fastflights(trip):
    import fast_flights as ff

    try:
        import typing
        from fast_flights import shopping as _sh
        log("调试 排序选项:", typing.get_args(getattr(_sh, "ShoppingResultSort", None)), typing.get_args(getattr(_sh, "ShoppingRankingMode", None)))
    except Exception as e:  # noqa: BLE001
        log("调试 排序选项读取失败", repr(e)[:200])
    best = None
    for origin in ORIGINS:
        try:
            kw = dict(
                flights=[
                    ff.FlightQuery(date=trip["out"], from_airport=origin, to_airport=DEST, max_stops=0),
                    ff.FlightQuery(date=trip["ret"], from_airport=DEST, to_airport=origin, max_stops=0),
                ],
                trip="round-trip",
                seat="economy",
                passengers=ff.Passengers(adults=1),
                language="zh-CN",
                currency="CNY",
            )
            q = ff.create_query(**kw)
            raw = list(ff.get_flights(q))
            log(f"  {origin} 原始结果 {len(raw)} 条")
            for f in raw[:12]:
                o = ff_option(f, origin)
                log(f"    原始：{o['airline']} {o['depart_time']} ¥{o['price']} 段数{len(getattr(f, 'flights', []) or [])}")
            outs = [(f, ff_option(f, origin)) for f in raw]
            directs = sorted([(f, o) for f, o in outs if o["stops"] == 0 and o["price"]], key=lambda x: x[1]["price"])
            log(f"  {origin} 直飞去程 {len(directs)} 个：" + "；".join(f"{o['airline']} {o['depart_time']} ¥{o['price']}" for _, o in directs))
            for f, o in directs[:8]:
                try:
                    rets = [ff_option(r, origin) for r in ff.get_return_flights(ff.select_flight(q, f))]
                    rd = sorted([r for r in rets if r["stops"] == 0 and r["price"]], key=lambda r: r["price"])
                    log(f"    {o['airline']} {o['depart_time']} 去 → 直飞回程：" + ("；".join(f"{r['airline']} {r['depart_time']} 往返¥{r['price']}" for r in rd) or "没有"))
                    if not rd:
                        continue
                    r = rd[0]
                    cand = {
                        "price": r["price"],
                        "airline": o["airline"],
                        "airport": origin,
                        "depart_time": o["depart_time"],
                        "ret_airline": r["airline"],
                        "ret_time": r["depart_time"],
                    }
                    if best is None or cand["price"] < best["price"]:
                        best = cand
                except Exception as e:  # noqa: BLE001
                    log("    选回程出错:", repr(e)[:300])
        except Exception as e:  # noqa: BLE001
            log(origin, "faster-flights 出错:", repr(e)[:400])
            log(traceback.format_exc()[-600:])
    if not best:
        raise RuntimeError("没找到去回都直飞的往返价")
    best.update({"stops": 0, "nonstop": True, "price_level": "", "source": "Google Flights"})
    return best, []


def main():
    now = datetime.now(JST)
    path = "prices.json"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("rows", [])
    data.setdefault("google_history", {})
    data["rows"] = [r for r in data["rows"] if r.get("trip") in TRIPS and r.get("nonstop")]  # 只保留这段行程的直飞往返

    key = os.environ.get("SERPAPI_KEY", "").strip()
    new_rows = []
    for tid, trip in TRIPS.items():
        log(f"== {trip['label']} ==")
        result = hist = None
        if key:
            try:
                result, hist = via_serpapi(trip, key)
            except Exception as e:  # noqa: BLE001
                log("SerpApi 出错:", repr(e)[:300])
        if result is None:
            try:
                result, hist = via_fastflights(trip)
            except Exception as e:  # noqa: BLE001
                log("没拿到价格:", repr(e)[:300])
        if result is None:
            continue
        row = {
            "id": now.strftime("%Y-%m-%dT%H:%M") + "_" + tid,
            "checked": now.strftime("%Y-%m-%d"),
            "checked_at": now.isoformat(timespec="minutes"),
            "trip": tid,
            "trip_label": trip["label"],
            **result,
        }
        log("记录:", json.dumps(row, ensure_ascii=False))
        data["rows"].append(row)
        new_rows.append(row["id"])
        if hist:
            merged = {d: p for d, p in data["google_history"].get(tid, [])}
            for d, p in hist:
                if p:
                    merged[d] = p
            data["google_history"][tid] = sorted(merged.items())

    if new_rows:
        data["updated"] = now.isoformat(timespec="minutes")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.write("\n")
    with open("new_rows.json", "w", encoding="utf-8") as f:
        json.dump(new_rows, f)
    with open("fetch-log.txt", "w", encoding="utf-8") as f:
        f.write(now.isoformat(timespec="minutes") + "\n" + "\n".join(LOG) + "\n")
    if not new_rows:
        sys.exit("这次两个日期都没拿到价格，详见 fetch-log.txt")


if __name__ == "__main__":
    main()
