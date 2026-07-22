#!/usr/bin/env python3
import http.cookiejar, json, os, shutil, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; port="42991"; data=Path(tempfile.gettempdir())/"primoscore-smoke-data"
shutil.rmtree(data,ignore_errors=True)
env={**os.environ,"PRIMOSCORE_PORT":port,"PRIMOSCORE_ADMIN_USER":"admin","PRIMOSCORE_ADMIN_PASSWORD":"Test-Sicuro-2026","PRIMOSCORE_DATA_DIR":str(data),"PRIMOSCORE_PUBLIC_ENABLED":"1"}
process=subprocess.Popen([sys.executable,str(ROOT/"server.py")],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
jar=http.cookiejar.CookieJar(); opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)); base=f"http://127.0.0.1:{port}"

def request(path,method="GET",body=None,headers=None):
    raw=json.dumps(body).encode() if body is not None else None
    req=urllib.request.Request(base+path,data=raw,method=method,headers={"Content-Type":"application/json",**(headers or {})})
    with opener.open(req,timeout=4) as response:
        content=response.read(); return response.status,(json.loads(content) if "json" in response.headers.get_content_type() else content)

try:
    for _ in range(30):
        try:
            if request("/api/health")[0]==200: break
        except Exception: time.sleep(.1)
    status,lead=request("/api/leads","POST",{"name":"Mario Rossi","phone":"3331234567","email":"mario@example.it","province":"Genova","score":76,"score_band":"Profilo solido","privacy_consent":True,"contact_consent":True,"source":"test","answers":{"purpose":"Acquisto prima casa","income":2500},"dimensions":{"Sostenibilità":80}}); assert status==201
    assert request(f"/api/leads/{lead['id']}/consultation","POST",{"token":lead["token"]})[0]==200
    status,login=request("/api/admin/login","POST",{"username":"admin","password":"Test-Sicuro-2026"}); assert status==200
    csrf=login["csrf"]; status,payload=request("/api/admin/leads"); assert status==200 and payload["count"]==1 and payload["leads"][0]["consultation_requested"]==1
    assert request(f"/api/admin/leads/{lead['id']}","PATCH",{"status":"contacted","notes":"Test completato"},{"X-CSRF-Token":csrf})[0]==200
    assert request("/api/admin/export.csv")[0]==200
    assert request(f"/api/admin/leads/{lead['id']}","DELETE",None,{"X-CSRF-Token":csrf})[0]==200
    assert request("/api/admin/leads")[1]["count"]==0
    print("OK: lead, consulenza, login, dashboard, modifica, export e cancellazione")
finally:
    process.terminate(); process.wait(timeout=5); shutil.rmtree(data,ignore_errors=True)
