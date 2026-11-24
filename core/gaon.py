"""가온(gaon.hanex.co.kr) 넥사크로 dynamicService 클라이언트.

로그인·공통 호출(call)은 SAMG 자동화(samg_order/gaon_client.py)에서 실서버 검증된 코드를 가져왔다.
P&G 수주 등록(register_orders)은 가온 쪽 수주 업로드 화면의 HAR 캡처가 오면 그 통신을 재현해 채운다.
그 전까지는 NotRegistered 예외를 내고, 포털은 오더를 '가온 미등록' 상태로 둔다.
"""
import re
import threading
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import requests

NS = "{http://www.nexacroplatform.com/platform/dataset}"
SVC_COLS = ["name", "inMapping", "inputDataset", "outMapping", "strParam", "useRowType", "condition"]


class GaonError(Exception):
    pass


class NotImplementedYet(GaonError):
    """HAR 분석 전: 가온 수주 등록 통신이 아직 정의되지 않음."""


def esc(v):
    if v is None:
        v = ""
    return escape(str(v), {'"': "&quot;", "'": "&#39;", "\r": "&#13;", "\n": "&#10;", " ": "&#32;"})


def cell(cid, v):
    return '<Col id="%s">%s</Col>' % (cid, esc(v)) if v else '<Col id="%s" />' % cid


def dataset(ds_id, cols, rows, row_type=None):
    ci = "".join('<Column id="%s" type="STRING" size="256" />' % c for c in cols)
    attr = ' type="%s"' % row_type if row_type else ""
    rs = "".join("<Row%s>%s</Row>" % (attr, "".join(cell(c, r.get(c)) for c in cols)) for r in rows)
    return '<Dataset id="%s"><ColumnInfo>%s</ColumnInfo><Rows>%s</Rows></Dataset>' % (ds_id, ci, rs)


def svc(name, in_map="gdsComIn=gdsComIn", out_map="", str_param="", condition=""):
    return {"name": name, "inMapping": re.sub(r":[uUaAnN]\b", "", in_map), "inputDataset": in_map, "outMapping": out_map,
            "strParam": str_param, "useRowType": "rowType", "condition": condition}


def parse_params(str_param):
    return [(k, a or b) for k, a, b in re.findall(r'(\w+)=(?:"([^"]*)"|\'([^\']*)\')', str_param)]


def parse_ssv_first(raw, ds_id):
    recs = raw.split("\x1e")
    for i, r in enumerate(recs):
        if r == "Dataset:" + ds_id and i + 2 < len(recs):
            cols = [c.split(":")[0] for c in recs[i + 1].split("\x1f")]
            vals = recs[i + 2].split("\x1f")
            return {c: v for c, v in zip(cols, vals) if "pwd" not in c.lower()}
    return {}


class GaonClient:
    def __init__(self, base="https://gaon.hanex.co.kr", company="001"):
        self.base = base.rstrip("/")
        self.company = company
        self.user_id = ""
        self.user_nm = ""
        self.detail_role = ""
        self.http = requests.Session()
        self.http.headers.update({"Content-Type": "text/xml", "Accept": "application/xml, text/xml, */*",
                                  "X-Requested-With": "XMLHttpRequest"})

    def _send(self, url, body):
        r = self.http.post(url, data=body.encode("utf-8"), timeout=120)
        if r.status_code != 200:
            raise GaonError("HTTP %s" % r.status_code)
        return r.content

    def login(self, user_id, password):
        body = ('<?xml version="1.0" encoding="UTF-8"?><Root xmlns="http://www.nexacroplatform.com/platform/dataset"><Parameters>'
                '<Parameter id="sCompanyCd">100</Parameter><Parameter id="sUserId">%s</Parameter>'
                '<Parameter id="sUserPwd">%s</Parameter><Parameter id="sDomain">ko_KR</Parameter>'
                '<Parameter id="sWebViewType">desktop</Parameter><Parameter id="__tcsFormId">frmLogin</Parameter>'
                "</Parameters></Root>") % (esc(user_id), esc(password))
        raw = self._send(self.base + "/hanex/ex/login.do", body).decode("utf-8", "replace")
        m = re.search(r"ErrorCode=(-?\d+)", raw)
        if not m or m.group(1) != "0":
            em = re.search(r"ErrorMsg=([^\x1e\x1f\r\n]*)", raw)
            raise GaonError("로그인 실패" + (": " + em.group(1) if em else ""))
        info = parse_ssv_first(raw, "gdsUserInfo")
        self.user_id = user_id
        self.user_nm = info.get("user_nm", "")
        self.detail_role = info.get("io_detail_class", "")
        return True

    def call(self, services, log_type, remark, datasets="", prog=""):
        params = []
        for s in services:
            params += parse_params(s["strParam"])
        log = svc("Sys.Log.logInsert", "dsHistoryLog=dsHistoryLog gdsComIn=gdsComIn")
        log["inputDataset"] = "dsHistoryLog=dsHistoryLog:U gdsComIn=gdsComIn"
        hist_cols = ["company_cd", "system_div", "user_id", "user_nm", "log_type", "prog_id", "ip_addr", "remark", "session_dt"]
        hist = dataset("dsHistoryLog", hist_cols, [{"company_cd": "100", "system_div": "desktop", "user_id": self.user_id,
                                                    "user_nm": self.user_nm, "log_type": log_type, "prog_id": prog, "remark": remark}], "insert")
        body = ('<?xml version="1.0" encoding="UTF-8"?><Root xmlns="http://www.nexacroplatform.com/platform/dataset"><Parameters>'
                + "".join('<Parameter id="%s">%s</Parameter>' % (k, esc(v)) if v else '<Parameter id="%s" />' % k for k, v in params)
                + '<Parameter id="commonVar1">#{clientIP}</Parameter></Parameters>'
                + dataset("dsService", SVC_COLS, [log] + services)
                + dataset("dsServiceOption", ["mybatisExecutorType"], [{"mybatisExecutorType": "simple"}])
                + hist
                + '<Dataset id="gdsComIn"><ColumnInfo><Column id="userId" type="string" size="255" /><Column id="clientIP" type="string" size="255" /></ColumnInfo><Rows><Row><Col id="userId">#{userId}</Col><Col id="clientIP">#{clientIP}</Col></Row></Rows></Dataset>'
                + datasets + "</Root>")
        raw = self._send(self.base + "/hanex//dynamicService.do", body)
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            raise GaonError("응답 해석 실패 (로그인 세션이 끊겼을 수 있습니다)")
        ec = root.find('.//%sParameter[@id="ErrorCode"]' % NS)
        if ec is not None and (ec.text or "0").strip() != "0":
            em = root.find('.//%sParameter[@id="ErrorMsg"]' % NS)
            raise GaonError("가온오류 %s %s" % (ec.text, em.text if em is not None else ""))
        out = {}
        for ds in root.iter(NS + "Dataset"):
            out[ds.get("id")] = [{c.get("id"): (c.text or "") for c in row if c.tag == NS + "Col"} for row in ds.iter(NS + "Row")]
        return out

    # ---- P&G 업무 (HAR 분석 후 구현) ----
    def register_orders(self, market_code, orders):
        """orders: [{order_no, store_gaon_code, delivery_date, items:[{gaon_code, qty}]}] → [{order_no, ok, slip_no, msg}]

        TODO: 가온 P&G 수주 등록 화면(엑셀 업로드형인지 건별 저장형인지)의 HAR를 받아 통신을 재현한다.
        """
        raise NotImplementedYet("가온 수주 등록 통신이 아직 정의되지 않았습니다 (HAR 분석 대기).")


class GaonSession:
    """서버에 하나만 두는 가온 연결. 비밀번호는 저장하지 않고 로그인 요청에만 쓴다."""

    def __init__(self):
        self.client = None
        self.lock = threading.Lock()
        self.error = ""

    @property
    def connected(self):
        return self.client is not None

    def connect(self, user_id, password):
        with self.lock:
            c = GaonClient()
            try:
                c.login(user_id, password)
            except GaonError as e:
                self.client, self.error = None, str(e)
                raise
            except requests.RequestException as e:
                self.client, self.error = None, "가온 접속 실패: %s" % e
                raise GaonError(self.error)
            self.client, self.error = c, ""
            return c

    def disconnect(self):
        with self.lock:
            self.client = None

    def info(self):
        if not self.client:
            return {"connected": False, "error": self.error}
        return {"connected": True, "user_id": self.client.user_id, "user_nm": self.client.user_nm}


gaon = GaonSession()
