"""주소 → 위경도 (카카오 로컬 REST API).

zone_map / 배송 시뮬레이터에서 검증된 다단계 전략:
  1) 원문 주소검색 → 2) 괄호·쉼표 뒤·층/호 꼬리 제거 → 3) 뒤에서 토큰 하나씩 잘라 재시도 → 4) 키워드(장소) 검색 폴백
결과는 geocode_cache 테이블에 저장해 같은 주소는 다시 호출하지 않는다.
"""
import re

import requests

from .db import execute, now, q, setting

ADDR_URL = "https://dapi.kakao.com/v2/local/search/address.json"
KW_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"


class GeocodeError(Exception):
    pass


def _clean(addr):
    s = re.sub(r"\([^)]*\)", " ", addr)            # 괄호
    s = s.split(",")[0]                             # 쉼표 뒤
    s = re.sub(r"\s*\d+\s*(층|호|F)\b.*$", "", s)    # 3층, 101호 이후
    s = re.sub(r"\s+(지하|B)\d+.*$", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def _call(url, key, params):
    r = requests.get(url, headers={"Authorization": "KakaoAK %s" % key}, params=params, timeout=5)
    if r.status_code == 401:
        raise GeocodeError("카카오 API 키가 유효하지 않습니다(401). 관리 > 설정에서 REST 키를 확인하세요.")
    if r.status_code != 200:
        raise GeocodeError("카카오 API 오류 HTTP %s" % r.status_code)
    return r.json().get("documents", [])


def geocode(address, key=None):
    """반환 dict(lat, lng, matched, method) 또는 None. API 키 문제는 GeocodeError."""
    address = (address or "").strip()
    if not address:
        return None
    c = q("SELECT * FROM geocode_cache WHERE address=?", (address,), one=True)
    if c:
        return {"lat": c["lat"], "lng": c["lng"], "matched": c["matched"], "method": c["method"]} if c["lat"] is not None else None
    key = key or setting("kakao_rest_key", "")
    if not key:
        raise GeocodeError("카카오 REST API 키가 설정되지 않았습니다 (관리 > 설정).")
    tried, result = set(), None
    cands = [address, _clean(address)]
    toks = _clean(address).split(" ")
    for n in range(len(toks) - 1, 2, -1):           # 뒤에서 토큰 절단 (최소 3토큰 유지)
        cands.append(" ".join(toks[:n]))
    for cand in cands:
        if not cand or cand in tried:
            continue
        tried.add(cand)
        docs = _call(ADDR_URL, key, {"query": cand, "size": 1})
        if docs:
            d = docs[0]
            result = {"lat": float(d["y"]), "lng": float(d["x"]), "matched": d.get("address_name") or cand, "method": "address" if cand == address else "address_clean"}
            break
    if result is None:
        for cand in (address, _clean(address)):
            docs = _call(KW_URL, key, {"query": cand, "size": 1})
            if docs:
                d = docs[0]
                result = {"lat": float(d["y"]), "lng": float(d["x"]), "matched": "%s (%s)" % (d.get("place_name", ""), d.get("road_address_name") or d.get("address_name", "")), "method": "keyword"}
                break
    execute("INSERT OR REPLACE INTO geocode_cache(address,lat,lng,matched,method,at) VALUES(?,?,?,?,?,?)",
            (address, result["lat"] if result else None, result["lng"] if result else None, result["matched"] if result else None,
             result["method"] if result else "fail", now()))
    return result


def geocode_table(table, ids=None, only_missing=True, limit=500):
    """stores/hubs 행의 address로 lat/lng 채움. 반환 {'ok':n,'fail':[(id,name,address)],'skipped':n}"""
    where = ["address IS NOT NULL", "TRIM(address)!=''"]
    args = []
    if only_missing:
        where.append("(lat IS NULL OR lng IS NULL)")
    if ids:
        where.append("id IN (%s)" % ",".join("?" * len(ids)))
        args += list(ids)
    rows = q("SELECT id, name, address FROM %s WHERE %s LIMIT ?" % (table, " AND ".join(where)), args + [limit])
    res = {"ok": 0, "fail": [], "skipped": 0, "total": len(rows)}
    for r in rows:
        g = geocode(r["address"])
        if g:
            execute("UPDATE %s SET lat=?, lng=? WHERE id=?" % table, (g["lat"], g["lng"], r["id"]))
            res["ok"] += 1
        else:
            res["fail"].append((r["id"], r["name"], r["address"]))
    return res
