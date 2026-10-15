"""部署後的快速檢查（V1～V8）：python tools/smoke.py https://你的專案.vercel.app 通行碼 [--admin 管理者通行碼] [--write] [--no-tts]
不用登入的網站（AUTH_MODE=open）：python tools/smoke.py https://你的專案.vercel.app --open [--admin 管理者通行碼] [--write] [--no-tts]
只用標準函式庫。不會印出通行碼或 cookie。--write 會新增再刪掉一個測試情境（要管理者權限）。"""

import http.client
import json
import ssl
import sys
import time
from urllib.parse import urlparse

RESULTS = []


def report(code, ok, text):
    tag = {True: "PASS", False: "FAIL", None: "SKIP"}[ok]
    RESULTS.append(ok)
    print(f"{code:<3} {tag:<4} {text}", flush=True)


class Site:
    def __init__(self, base):
        u = urlparse(base.rstrip("/"))
        if u.scheme not in ("http", "https") or not u.hostname:
            sys.exit("網址要像 https://你的專案.vercel.app")
        self.scheme, self.host, self.port = u.scheme, u.hostname, u.port
        self.cookie = ""

    def conn(self, timeout=60):
        if self.scheme == "https":
            return http.client.HTTPSConnection(self.host, self.port or 443, timeout=timeout, context=ssl.create_default_context())
        return http.client.HTTPConnection(self.host, self.port or 80, timeout=timeout)

    def headers(self, body):
        h = {"Accept": "application/json"}
        if body is not None:
            h["Content-Type"] = "application/json"
        if self.cookie:
            h["Cookie"] = self.cookie
        return h

    def call(self, method, path, body=None, timeout=60):
        c = self.conn(timeout)
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        c.request(method, path, body=data, headers=self.headers(body))
        r = c.getresponse()
        raw = r.read()
        c.close()
        for k, v in r.getheaders():
            if k.lower() == "set-cookie" and v.startswith("aic_session="):
                self.cookie = v.split(";", 1)[0]
        try:
            js = json.loads(raw.decode("utf-8"))
        except ValueError:
            js = None
        return r.status, js, raw

    def stream(self, path, body, stop_after=None, timeout=120):
        """回傳 [(秒數, 事件)]；stop_after：收到幾個 delta 就直接斷線（模擬業務插話）。"""
        c = self.conn(timeout)
        c.request("POST", path, body=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=self.headers(body))
        r = c.getresponse()
        if r.status != 200:
            raw = r.read()
            c.close()
            return r.status, [(0, {"error": raw[:200].decode("utf-8", "replace")})]
        t0, out, deltas = time.monotonic(), [], 0
        while True:
            line = r.readline()
            if not line:
                break
            if not line.startswith(b"data:"):
                continue
            try:
                ev = json.loads(line[5:].decode("utf-8"))
            except ValueError:
                continue
            out.append((time.monotonic() - t0, ev))
            if "delta" in ev:
                deltas += 1
                if stop_after and deltas >= stop_after:
                    break
            if ev.get("done") or ev.get("error"):
                break
        r.close()
        c.close()
        return 200, out


def main():
    args = sys.argv[1:]
    open_mode = "--open" in args
    if not args or args[0].startswith("-") or (not open_mode and (len(args) < 2 or args[1].startswith("-"))):
        sys.exit(__doc__)
    base, passcode = args[0], "" if open_mode else args[1]
    admin = args[args.index("--admin") + 1] if "--admin" in args else ""
    write, tts = "--write" in args, "--no-tts" not in args
    site = Site(base)

    # V1：要登入的網站，沒登入不能用 API；不用登入的網站（--open）直接能用，還會拿到一個匿名 cookie
    status, js, _ = site.call("GET", "/api/status")
    if open_mode:
        ok = status == 200 and site.cookie.startswith("aic_session=")
        report("V1", ok, f"不用登入呼叫 /api/status → HTTP {status}{'' if site.cookie else '（沒有拿到匿名 cookie）'}")
    else:
        report("V1", status == 401 and bool((js or {}).get("login")), f"沒登入呼叫 /api/status → HTTP {status}")
    if status == 503:
        print("    雲端設定還沒完成：" + "、".join((js or {}).get("setup_problems") or [str((js or {}).get("error") or "")]))
    if open_mode and not ok:
        return finish()

    # V3：登入（--open 不用登入；有給 --admin 才用管理者通行碼登入）
    if open_mode and not admin:
        report("V3", None, "不用登入（AUTH_MODE=open），略過")
    else:
        status, js, _ = site.call("POST", "/api/login", {"passcode": admin or passcode, "name": "smoke"})
        ok = status == 200 and site.cookie.startswith("aic_session=")
        report("V3", ok, f"用通行碼登入 → HTTP {status}{'' if ok else '（' + str((js or {}).get('error') or '') + '）'}")
        if not ok:
            return finish()
    status, st, _ = site.call("GET", "/api/status")
    st = st or {}
    storage = st.get("storage") or {}
    cg = st.get("chatgpt") or {}
    print(f"    大腦：{st.get('label', '?')}；資料庫：{storage.get('kind', '?')}；ChatGPT：{cg.get('mode', '?')}"
          f"{'（已同步）' if cg.get('signed_in') else ''}")
    for p in st.get("setup_problems") or []:
        print("    待設定：" + str(p))

    # V2：程式、情境檔、.env 不能被直接下載
    leaks = []
    for path in ("/scenarios/01-dca-volatility.json", "/server.py", "/app.py", "/storage.py", "/.env", "/chatgpt_brain.py"):
        code, _, raw = site.call("GET", path)
        if code != 404 or b"server_only" in raw:
            leaks.append(f"{path}={code}")
    report("V2", not leaks, "程式和情境檔不會被下載" if not leaks else "下載得到：" + "、".join(leaks))

    # V4：客戶回話要一段一段到
    code, js, _ = site.call("GET", "/api/scenarios")
    scenarios = (js or {}).get("scenarios") or []
    if not scenarios:
        report("V4", False, f"讀不到情境（HTTP {code}）")
        return finish()
    sc = scenarios[0]
    chat = {"scenario_id": sc["id"], "messages": [{"role": "rep", "text": "您好，我是理專，想跟您聊聊最近的投資。"}]}
    code, events = site.stream("/api/chat", chat)
    times = [t for t, e in events if "delta" in e]
    brain = next((e.get("brain") for _, e in events if e.get("brain")), "")
    err = next((e["error"] for _, e in events if e.get("error")), "")
    if err or not times:
        report("V4", False, f"/api/chat 沒有回話：{err or code}")
    else:
        spread = times[-1] - times[0]
        report("V4", len(times) >= 2 and spread > 0.05,
               f"收到 {len(times)} 段，第一段 {times[0]:.2f} 秒、最後一段 {times[-1]:.2f} 秒{('（' + brain + '）') if brain else ''}")

    # V5：插話（斷線）後伺服器要停：這裡只能確認斷線本身沒問題，要到 Vercel 的 Logs 看有沒有停
    code, events = site.stream("/api/chat", chat, stop_after=1)
    report("V5", None, "已模擬插話斷線；請到 Vercel → Logs 確認這個請求約 1 秒內結束")

    # V6：不同瀏覽器的「我的背景」互相看不到
    other = Site(base)
    if open_mode:
        other.call("GET", "/api/status")  # 拿另一個匿名 cookie
    else:
        other.call("POST", "/api/login", {"passcode": passcode, "name": "smoke2"})
    profile = {"surname": "測", "given_name": "", "address": "", "role": "wealth", "custom_title": "",
               "company": "smoke-" + str(int(time.time())), "branch": "", "years": None, "products": [], "knowledge": ""}
    _, before, _ = site.call("GET", "/api/profile")
    shared = bool((before or {}).get("shared"))
    code = None if shared else site.call("POST", "/api/profile", {"profile": profile})[0]
    if shared:
        # 大家共用同一份：存測試資料再清掉，會把你真的背景洗掉
        report("V6", None, "「我的背景」是共用的（PROFILE_SCOPE=shared），不動你的資料，略過")
    elif code == 403:
        report("V6", None, "背景目前鎖住（PROFILE_LOCKED），略過")
    elif code != 200:
        report("V6", False, f"存不了背景（HTTP {code}）")
    else:
        _, mine, _ = site.call("GET", "/api/profile")
        _, theirs, _ = other.call("GET", "/api/profile")
        seen_mine = ((mine or {}).get("profile") or {}).get("company") == profile["company"]
        seen_other = ((theirs or {}).get("profile") or {}).get("company") == profile["company"]
        report("V6", seen_mine and not seen_other,
               "另一個瀏覽器看不到這邊的背景" if not seen_other else "另一個瀏覽器也看得到（PROFILE_SCOPE=shared？）")
        site.call("POST", "/api/profile", {"reset": True})

    # V7：情境存在資料庫（重新部署後還在）
    durable = storage.get("kind") == "redis" and storage.get("durable")
    report("V7", bool(durable), "資料存在 Upstash，重新部署不會不見" if durable else
           "沒有接 Upstash：" + str(storage.get("warning") or "資料不會保存"))
    if write and durable:
        code, js, _ = site.call("POST", "/api/editor/get", {"id": sc["id"]})
        draft = (js or {}).get("scenario") or {}
        draft["title"] = "smoke 測試（會自動刪除）"
        code, saved, _ = site.call("POST", "/api/editor/save", {"copy_from": sc["id"], "scenario": draft})
        sid = (saved or {}).get("id")
        if code == 200 and sid:
            _, rows, _ = site.call("POST", "/api/editor/list", {})
            found = any(r.get("id") == sid for r in (rows or {}).get("scenarios") or [])
            site.call("POST", "/api/editor/delete", {"id": sid})
            report("V7", found, "新增的情境讀得回來（已刪除）")
        else:
            report("V7", None if code == 403 else False, f"新增測試情境 → HTTP {code}（要管理者通行碼請加 --admin）")

    # V8：開場白語音有快取（第二次很快）
    if not tts:
        report("V8", None, "--no-tts：略過")
        return finish()
    code, ts, _ = site.call("GET", "/api/tts/status")
    if (ts or {}).get("provider") in (None, "browser"):
        report("V8", None, "用的是瀏覽器內建聲音，沒有語音快取")
        return finish()
    body = {"text": sc.get("opening_line", ""), "avatar": (sc.get("customer") or {}).get("avatar", "")}
    durations = []
    for _ in range(2):
        t0 = time.monotonic()
        code, _, raw = site.call("POST", "/api/tts", body, timeout=150)
        durations.append(time.monotonic() - t0)
        if code != 200:
            report("V8", False, f"/api/tts → HTTP {code}")
            return finish()
    report("V8", durations[1] < 1.5 or durations[1] < durations[0] / 2,
           f"開場白第一次 {durations[0]:.1f} 秒、第二次 {durations[1]:.1f} 秒")
    return finish()


def finish():
    fails = sum(1 for r in RESULTS if r is False)
    print(f"\n{'全部通過' if not fails else f'{fails} 項沒過'}（PASS {RESULTS.count(True)}，SKIP {RESULTS.count(None)}）")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
