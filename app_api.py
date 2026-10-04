"""智学网「手阅作业」抓取客户端（本模块属于 zhixuewang_http 仓库）。

逆向结论（智伴 com.zhixue.lite + 实测验证）：
  - 智伴 App 的登录链路为 open.changyan.com/sso/v1/api（密码反转+RSA PKCS1，即 App 版
    R2/P）-> /container/app/login/casLogin(at,userId) 换 token。但实测 casLogin 对
    changyan 签发的 at 一律返回 50011（账号或密码错误），该链路当前不可用。
  - 可行链路（实测通过）：web 端登录（custom_provider.http_login，CAS 全流程含验证码）
    -> /container/app/token/getToken 换 XToken(JWT)
    -> zxbReport 系列接口（请求头带 authbizcode/authguid/authtimestamp/authtoken/XToken）。

接口链路：
  1. POST /zxbReport/report/getPageAllExamList  reportType=homework -> 作业列表
  2. POST /zxbReport/report/exam/getReportMain  examId -> paperId + 分数
  3. POST /zxbReport/report/paper/getCheckSheet examId+paperId -> 答题卡图片 URL、
     sheetDatas 坐标、score/standardScore、stepDatas（手阅作业通常为空）

详细接口说明见 docs/shouyue_homework_api.md。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid

import requests

from custom_provider import http_login

ZHIXUE_BASE = "https://www.zhixue.com"


class ZhixueAppClient:
    """手阅作业 API 客户端：web 会话 cookie + XToken 访问 zxbReport。"""

    def __init__(self, cookies: dict | None = None):
        self.s = requests.Session()
        self.s.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        if cookies:
            for k, v in cookies.items():
                self.s.cookies.set(k, v, domain=".zhixue.com")
        self.xtoken: str | None = None

    # ------------------------------------------------------------------
    def login(self, username: str, password: str) -> None:
        self.s.cookies.clear()
        cookies = http_login(username, password, print_fn=lambda *a: None)
        for k, v in cookies.items():
            self.s.cookies.set(k, v, domain=".zhixue.com")
        self.get_xtoken()

    def get_xtoken(self) -> str:
        r = self.s.get(
            f"{ZHIXUE_BASE}/container/app/token/getToken",
            headers=self._auth_headers(),
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("errorCode") != 0 or not data.get("result"):
            raise RuntimeError(f"getToken 失败: {data}")
        self.xtoken = data["result"]
        return self.xtoken

    def _auth_headers(self) -> dict:
        guid = str(uuid.uuid4())
        ts = str(int(time.time() * 1000))
        tok = hashlib.md5((guid + ts + "iflytek!@#123student").encode()).hexdigest()
        h = {"authbizcode": "0001", "authguid": guid, "authtimestamp": ts, "authtoken": tok}
        if self.xtoken:
            h["XToken"] = self.xtoken
        return h

    def _post(self, path: str, data: dict) -> dict:
        r = self.s.post(
            f"{ZHIXUE_BASE}{path}", data=data, headers=self._auth_headers(), timeout=60
        )
        r.raise_for_status()
        resp = r.json()
        if resp.get("errorCode") != 0:
            raise RuntimeError(f"{path} 失败: {resp.get('errorInfo')} ({resp.get('errorCode')})")
        return resp

    # ------------------------------------------------------------------
    def get_homework_list(self, page: int = 1, page_size: int = 10) -> list[dict]:
        """手阅/作业列表。返回 examInfoList（examId/examName/examType/score...）。"""
        resp = self._post(
            "/zxbReport/report/getPageAllExamList",
            {
                "reportType": "homework",
                "pageIndex": page,
                "pageSize": page_size,
                "actualPosition": 0,
            },
        )
        return resp["result"]["examInfoList"]

    def get_report_main(self, exam_id: str) -> dict:
        """单场作业报告主页 -> paperList（paperId/paperName/subjectName/userScore...）。"""
        resp = self._post(
            "/zxbReport/report/exam/getReportMain", {"examId": exam_id, "token": self.xtoken}
        )
        return resp["result"]

    def get_check_sheet(self, exam_id: str, paper_id: str) -> dict:
        """答题卡：sheetImages(已批改图片URL列表)/sheetDatas(坐标)/score/standardScore。"""
        resp = self._post(
            "/zxbReport/report/paper/getCheckSheet",
            {"examId": exam_id, "paperId": paper_id, "token": self.xtoken},
        )
        return resp["result"]

    def download_sheet_images(self, check_sheet: dict, out_dir: str) -> list[str]:
        """下载答题卡图片到 out_dir，返回本地文件路径列表。"""
        os.makedirs(out_dir, exist_ok=True)
        urls = json.loads(check_sheet.get("sheetImages") or "[]")
        paths = []
        for i, url in enumerate(urls):
            r = self.s.get(url, timeout=60)
            r.raise_for_status()
            fp = os.path.join(out_dir, f"sheet_p{i + 1}.jpg")
            with open(fp, "wb") as f:
                f.write(r.content)
            paths.append(fp)
        return paths


def main():
    username = sys.argv[1] if len(sys.argv) > 1 else input("账号: ").strip()
    password = sys.argv[2] if len(sys.argv) > 2 else input("密码: ").strip()

    c = ZhixueAppClient()
    c.login(username, password)

    exams = c.get_homework_list(page_size=10)
    print(f"===== 作业列表（前 {len(exams)} 条）=====")
    for i, e in enumerate(exams, 1):
        print(f"  {i}. {e['examName']}  examId={e['examId']}")

    if not exams:
        return
    exam = exams[0]
    main = c.get_report_main(exam["examId"])
    print(f"\n===== {exam['examName']} 报告 =====")
    for p in main.get("paperList", []):
        print(
            f"  {p['subjectName']}: {p['userScore']}/{p['standardScore']}  paperId={p['paperId']}"
        )

    paper = main["paperList"][0]
    sheet = c.get_check_sheet(exam["examId"], paper["paperId"])
    print("\n===== 答题卡 =====")
    print(
        f"  score={sheet.get('score')}/{sheet.get('standardScore')}  "
        f"图片数={len(json.loads(sheet.get('sheetImages') or '[]'))}"
    )
    paths = c.download_sheet_images(sheet, "data/answer_sheets")
    for fp in paths:
        print("  saved:", fp)
    with open("data/answer_sheets/checksheet_homework.json", "w", encoding="utf-8") as f:
        json.dump(sheet, f, ensure_ascii=False, indent=1)
    print("  saved: data/answer_sheets/checksheet_homework.json")


if __name__ == "__main__":
    main()
