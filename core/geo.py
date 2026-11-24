"""거리·예상도착 계산.

1순위 OSRM 경로 API(settings.osrm_url, 기본 공개 데모 서버) → 도로 거리·소요시간.
실패하면 직선거리 × 도로계수(road_factor) / 평균속도(avg_speed_kmh)로 추정한다.
"""
import datetime as dt
import math

import requests

from .db import setting


def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _num(key, default):
    try:
        return float(setting(key) or default)
    except ValueError:
        return default


def eta(lat, lng, dlat, dlng):
    """현재 위치 → 목적지. 반환 {'dist_km','min','source','eta_at'} 또는 좌표 없으면 None."""
    if None in (lat, lng, dlat, dlng):
        return None
    out = None
    base = (setting("osrm_url") or "").strip().rstrip("/")
    if base:
        try:
            r = requests.get("%s/route/v1/driving/%s,%s;%s,%s" % (base, lng, lat, dlng, dlat), params={"overview": "false"}, timeout=4)
            j = r.json()
            if j.get("code") == "Ok" and j.get("routes"):
                rt = j["routes"][0]
                out = {"dist_km": round(rt["distance"] / 1000, 1), "min": round(rt["duration"] / 60), "source": "osrm"}
        except Exception:
            out = None
    if out is None:
        d = haversine_km(lat, lng, dlat, dlng) * _num("road_factor", 1.3)
        out = {"dist_km": round(d, 1), "min": round(d / max(_num("avg_speed_kmh", 40), 5) * 60), "source": "estimate"}
    out["eta_at"] = (dt.datetime.now() + dt.timedelta(minutes=out["min"])).strftime("%Y-%m-%d %H:%M")
    return out
