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
    code = origin
    if legs:
        t = getattr(getattr(legs[0], "departure", None), "time", None) or []
        if t:
            dep = f"{int(t[0]):02d}:{int(t[1]) if len(t) > 1 else 0:02d}"
        code = getattr(getattr(legs[0], "from_airport", None), "code", None) or origin
    via = [getattr(getattr(l, "to_airport", None), "code", "") for l in legs[:-1]]
    return {
        "price": price,
        "airline": "、".join(dict.fromkeys(str(a) for a in airlines)),
        "airport": code,
        "stops": max(0, len(legs) - 1) if legs else None,
        "via": "、".join(v for v in via if v),
        "depart_time": dep,
    }


def via_fastflights(trip):
    import fast_flights as ff

    def make_query(origin, nonstop):
        extra = {"max_stops": 0} if nonstop else {}
        return ff.create_query(
            flights=[
                ff.FlightQuery(date=trip["out"], from_airport=origin, to_airport=DEST, **extra),
                ff.FlightQuery(date=trip["ret"], from_airport=DEST, to_airport=origin, **extra),
            ],
            trip="round-trip",
            seat="economy",
            passengers=ff.Passengers(adults=1),
            language="zh-CN",
            currency="CNY",
        )

    # 两种搜法合起来：只搜直飞 + 不加筛选（Google 两边都可能漏航班），再加上“北京”城市代码
    variants = [("PEK", True), ("PKX", True), ("BJS", True), ("PEK", False), ("PKX", False)]
    outbound = {}
    for origin, nonstop in variants:
        try:
            q = make_query(origin, nonstop)
            raw = list(ff.get_flights(q))
            got = [(f, ff_option(f, origin)) for f in raw]
            d = [(f, o) for f, o in got if o["stops"] == 0 and o["price"]]
            log(f"  {origin}{'（只搜直飞）' if nonstop else '（不筛选）'} 共 {len(raw)} 条，直飞 {len(d)} 条：" + "；".join(f"{o['airline']} {o['airport']} {o['depart_time']} ¥{o['price']}" for _, o in d))
            for f, o in d:
                k = (o["airport"], o["airline"], o["depart_time"])
                if k not in outbound or o["price"] < outbound[k][2]["price"]:
                    outbound[k] = (q, f, o)
        except Exception as e:  # noqa: BLE001
            log(f"  {origin} 出错:", repr(e)[:300])
    best = None
    best_objs = None
    for q, f, o in sorted(outbound.values(), key=lambda x: x[2]["price"])[:10]:
        try:
            rets = [ff_option(r, o["airport"]) for r in ff.get_return_flights(ff.select_flight(q, f))]
            rd = sorted([r for r in rets if r["stops"] == 0 and r["price"]], key=lambda r: r["price"])
            log(f"    {o['airline']} {o['airport']} {o['depart_time']} 去 → 直飞回程：" + ("；".join(f"{r['airline']} {r['depart_time']} 往返¥{r['price']}" for r in rd) or "没有"))
            if not rd:
                continue
            r = rd[0]
            cand = {"price": r["price"], "airline": o["airline"], "airport": o["airport"], "depart_time": o["depart_time"],
                    "ret_airline": r["airline"], "ret_time": r["depart_time"]}
            if best is None or cand["price"] < best["price"]:
                best = cand
                best_objs = (q, f, rets, r)
        except Exception as e:  # noqa: BLE001
            log("    选回程出错:", repr(e)[:300])
    if not best:
        raise RuntimeError("没找到去回都直飞的往返价")
    # 调试：看看能不能拿到各平台报价
    try:
        import inspect
        for name in ("select_flight", "get_selected_flight_page", "build_booking_url", "SelectedFlightPage"):
            obj = getattr(ff, name, None)
            try:
                log(f"调试 {name}:", str(inspect.signature(obj))[:400])
            except Exception as e:  # noqa: BLE001
                log(f"调试 {name}: -", repr(e)[:80])
        q, f, rets, _ = best_objs
        rq = ff.select_flight(q, f)
        rets_objs = list(ff.get_return_flights(rq))
        target = min((x for x in rets_objs if ff_option(x, "")["stops"] == 0 and ff_option(x, "")["price"]), key=lambda x: ff_option(x, "")["price"])
        sel = ff.select_flight(rq, target)
        log("调试 选定后的类型:", type(sel).__name__)
        page = ff.get_selected_flight_page(sel)
        log("调试 选定页:", repr(page)[:3000])
    except Exception as e:  # noqa: BLE001
        log("调试 各平台报价失败:", repr(e)[:500])
        log(traceback.format_exc()[-1200:])
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
            "src": "google",
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
