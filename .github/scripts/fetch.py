"""查 2/11（或 2/10）去、2/15 回 的北京⇄东京羽田往返最低价，追加到 prices.json。

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
        "stops": max(0, len(legs) - 1),
        "depart_time": ((first.get("departure_airport") or {}).get("time") or "")[-5:],
        "price_level": ins.get("price_level") or "",
        "typical_low": to_int(rng[0]) if len(rng) > 0 else None,
        "typical_high": to_int(rng[1]) if len(rng) > 1 else None,
        "source": "Google Flights（SerpApi）",
    }, hist


# ---------------- faster-flights ----------------
def ff_extract(f):
    price = to_int(getattr(f, "price", None))
    airline = getattr(f, "airlines", None) or getattr(f, "name", None) or getattr(f, "airline", None) or ""
    if isinstance(airline, (list, tuple)):
        airline = "、".join(str(a) for a in airline)
    stops = getattr(f, "stops", None)
    return price, str(airline), stops


def via_fastflights(trip):
    import fast_flights as ff  # noqa: F401

    log("fast_flights 版本:", getattr(ff, "__version__", "?"), "可用:", [n for n in dir(ff) if not n.startswith("_")])
    best = None
    for origin in ORIGINS:
        try:
            if hasattr(ff, "create_query"):
                q = ff.create_query(
                    flights=[
                        ff.FlightQuery(date=trip["out"], from_airport=origin, to_airport=DEST),
                        ff.FlightQuery(date=trip["ret"], from_airport=DEST, to_airport=origin),
                    ],
                    trip="round-trip",
                    seat="economy",
                    passengers=ff.Passengers(adults=1),
                    language="zh-CN",
                    currency="CNY",
                )
                res = ff.get_flights(q)
                flights = list(res)
                log(origin, "结果条数:", len(flights), "首条:", repr(flights[0])[:400] if flights else "无")
                total = None
                if flights and hasattr(ff, "select_flight") and hasattr(ff, "get_return_flights"):
                    try:
                        cheapest = min(flights, key=lambda f: ff_extract(f)[0] or 10**9)
                        rq = ff.select_flight(q, cheapest)
                        rets = list(ff.get_return_flights(rq))
                        log(origin, "回程条数:", len(rets), "首条:", repr(rets[0])[:300] if rets else "无")
                        prices = [ff_extract(r)[0] for r in rets if ff_extract(r)[0]]
                        if prices:
                            total = min(prices)
                    except Exception as e:  # noqa: BLE001
                        log(origin, "选回程失败:", repr(e)[:300])
                for f in flights:
                    price, airline, stops = ff_extract(f)
                    if not price:
                        continue
                    cand = {"price": price, "airline": airline, "airport": origin, "stops": stops if isinstance(stops, int) else None}
                    if best is None or price < best["price"]:
                        best = cand
                if total and best and best["airport"] == origin:
                    log(origin, "往返总价（选定去程后）:", total, "去程列表最低:", best["price"])
                    best["price_selected_total"] = total
            else:
                res = ff.get_flights(
                    flight_data=[
                        ff.FlightData(date=trip["out"], from_airport=origin, to_airport=DEST),
                        ff.FlightData(date=trip["ret"], from_airport=DEST, to_airport=origin),
                    ],
                    trip="round-trip",
                    seat="economy",
                    passengers=ff.Passengers(adults=1),
                    fetch_mode="fallback",
                )
                log(origin, "旧版结果:", repr(res)[:600])
                for f in getattr(res, "flights", []) or []:
                    raw = str(getattr(f, "price", ""))
                    if "$" in raw or "US" in raw:
                        continue
                    price, airline, stops = ff_extract(f)
                    if price and (best is None or price < best["price"]):
                        best = {"price": price, "airline": airline, "airport": origin, "stops": stops if isinstance(stops, int) else None,
                                "price_level": getattr(res, "current_price", "") or ""}
        except Exception as e:  # noqa: BLE001
            log(origin, "faster-flights 出错:", repr(e)[:400])
            log(traceback.format_exc()[-800:])
    if not best:
        raise RuntimeError("faster-flights 没拿到人民币价格")
    best.setdefault("price_level", "")
    best["source"] = "Google Flights"
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
    data["rows"] = [r for r in data["rows"] if r.get("trip") in TRIPS]  # 只保留这段行程

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
        if result.get("price_selected_total"):
            result["price"] = result.pop("price_selected_total")
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
