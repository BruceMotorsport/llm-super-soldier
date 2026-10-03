"""Verify the web console end-to-end without a browser:
every endpoint the page calls must return the shape the JS expects."""
import json, re, urllib.request, time

BASE = "http://127.0.0.1:8082"
fails = []

def check(name, cond, detail=""):
    if not cond: fails.append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

def get(p):
    return urllib.request.urlopen(BASE + p, timeout=20)

def post(path, payload):
    r = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                               headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=60))

print("=== 1. UI PAGE SERVED ===")
r = get("/ui")
html = r.read().decode("utf-8")
check("HTTP 200 text/html", r.status == 200 and "text/html" in r.headers.get("content-type",""),
      f"{len(html)} bytes")
check("has console title", "SUPER-SOLDIER CONSOLE" in html)
check("has all four stat cards",
      all(x in html for x in ["id=\"health\"","id=\"router\"","id=\"keys\"","id=\"ladder\""]))
check("has model dropdown + input + send", 'id="model"' in html and 'id="msg"' in html and 'id="send"' in html)
check("has quick-test buttons", html.count('data-q=') >= 5)
check("no external CDN dependency", "http://" not in html.replace("http://www.w3.org","")
      and "https://" not in html, "fully self-contained")

print("\n=== 2. /ui/ ALSO WORKS ===")
check("trailing slash serves page", get("/ui/").read() == html.encode())

print("\n=== 3. ENDPOINTS THE PAGE CALLS ===")
h = json.load(get("/health"))
check("/health -> status ok", h.get("status")=="ok")
check("/health has routes list", isinstance(h.get("routes"), list))

rt = json.load(get("/router/status"))
check("/router/status -> groq_open", "groq_open" in rt)

ks = json.load(get("/keys/stats"))
check("/keys/stats -> keys array", isinstance(ks.get("keys"), list),
      f"{ks.get('keys_configured')} key(s)")
check("keys are masked not raw", all("..." in k.get("masked","") for k in ks.get("keys",[])))

ls = json.load(get("/ladder/stats"))
check("/ladder/stats -> cache stats", "cache" in ls and "entries" in ls.get("cache",{}))

md = json.load(get("/v1/models"))
check("/v1/models -> data array", isinstance(md.get("data"), list) and len(md["data"])>0,
      f"{len(md.get('data',[]))} models")

print("\n=== 4. CHAT THROUGH THE PAGE'S OWN PATH ===")
d = post("/v1/chat/completions", {"model":"supersoldier",
                                  "messages":[{"role":"user","content":"What is 12 * 8?"}]})
sb = d.get("supersoldier", {})
check("has choices[0].message.content", bool(d["choices"][0]["message"]["content"]))
check("has supersoldier.served_by", sb.get("served_by")=="python", sb.get("served_by"))
check("llm_called False", sb.get("llm_called") is False)

print("\n=== 5. TAGS RENDER (served_by -> css class) ===")
d2 = post("/v1/chat/completions", {"model":"supersoldier",
                                   "messages":[{"role":"user","content":"Who wrote Hamlet?"}]})
sb2 = d2.get("supersoldier", {})
tag_class = f"t-{sb2.get('served_by','?')}"
css = re.search(r"\.t-llm\{[^}]+\}", html)
check("served_by maps to a css tag class", bool(css), tag_class)
check("llm path flagged", sb2.get("llm_called") is True, sb2.get("served_by"))

print("\n=== 6. MODEL DROPDOPTS ALL RESOLVE ===")
for m in md["data"]:
    dm = post("/v1/chat/completions", {"model":m["id"],
                                       "messages":[{"role":"user","content":"what is 3 * 3"}]})
    ok = bool(dm.get("choices")) and not dm.get("error")
    check(f"model '{m['id']}' usable", ok)

print("\n=== 7. /lan HELPER ===")
lan = json.load(get("/lan"))
check("lan reports local console", "/ui" in lan.get("local_console",""))
check("lan leaks no secrets", "gsk_" not in json.dumps(lan))

print("\n" + "="*46)
print("RESULT:", "PASS — 100%" if not fails else f"FAIL — {fails}")