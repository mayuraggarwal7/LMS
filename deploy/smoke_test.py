"""Read-only smoke test of a deployed CampusFlow: real logins, crawl every linked page per role, key checks.
usage: python deploy/smoke_test.py https://host [limit_per_role]"""
import re, sys, time
from collections import Counter
import requests

B = sys.argv[1].rstrip("/")
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 400
SKIP = ("/static", "/media", "/logout", "/admin/")
results = []

def check(name, cond, detail=""):
    results.append((bool(cond), name, detail)); print(("PASS " if cond else "FAIL ") + name, detail)

def login(user, pw):
    s = requests.Session(); s.headers["User-Agent"] = "campusflow-smoke"
    r = s.get(B + "/login/", timeout=60)
    tok = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', r.text).group(1)
    r = s.post(B + "/login/", data={"username": user, "password": pw, "csrfmiddlewaretoken": tok},
               headers={"Referer": B + "/login/"}, timeout=60)
    return s, ("Log out" in r.text or "logout" in r.text.lower()) and "/login/" not in r.url

def crawl(s, seeds, limit):
    seen, q, bad, times = set(), list(seeds), [], []
    while q and len(seen) < limit:
        u = q.pop(0)
        if u in seen: continue
        seen.add(u)
        t0 = time.time()
        try: r = s.get(B + u, timeout=60, allow_redirects=True)
        except Exception as e: bad.append((u, repr(e)[:120])); continue
        times.append(time.time() - t0)
        if r.status_code not in (200, 403): bad.append((u, r.status_code)); continue
        if r.status_code == 200 and r.headers.get("Content-Type", "").startswith("text/html"):
            for l in re.findall(r'href="(/[^"#]*)"', r.text):
                l = l.replace("&amp;", "&")
                if not l.startswith(SKIP) and "format=" not in l and "export" not in l and l not in seen: q.append(l)
    return seen, bad, times

# infrastructure
r = requests.get(B + "/login/", timeout=60)
check("HTTPS login page", r.status_code == 200 and r.url.startswith("https://"), f"{r.status_code} {r.elapsed.total_seconds():.2f}s")
r = requests.get(B.replace("https://", "http://") + "/login/", timeout=60, allow_redirects=False)
check("HTTP redirects to HTTPS", r.status_code in (301, 302, 308) and r.headers.get("Location", "").startswith("https://"), str(r.status_code))
css = re.search(r'href="(/static/[^"]+\.css)"', requests.get(B + "/login/", timeout=60).text)
check("static CSS served", css and requests.get(B + css.group(1), timeout=60).status_code == 200, css.group(1) if css else "no css link")
r = requests.get(B + "/does-not-exist-xyz/", timeout=60)
check("404 page without debug info", r.status_code == 404 and "Traceback" not in r.text and "URLconf" not in r.text, str(r.status_code))
r = requests.post(B + "/api/biometric/punches", json={"punches": []}, timeout=60)
check("biometric API rejects missing key", r.status_code == 403, str(r.status_code))
r = requests.get(B + "/admin-console/", timeout=60, allow_redirects=False)
check("admin console needs login", r.status_code in (302, 403), str(r.status_code))

roles = [("admin", "admin123", ["/", "/admin-console/", "/admin-console/rules/", "/admin-console/aicte/", "/students/", "/hr/",
                                "/hr/allocation/", "/finance/", "/finance/payroll/", "/biometric/"]),
         ("teacher", "teacher123", ["/"]), ("s01", "student123", ["/"]), ("demo-comp-2024-001", "student123", ["/", "/students/me/"])]
for user, pw, seeds in roles:
    s, ok = login(user, pw)
    check(f"login {user}", ok)
    if not ok: continue
    seen, bad, times = crawl(s, seeds, LIMIT if user in ("admin", "teacher") else 150)
    times.sort()
    check(f"crawl {user}: {len(seen)} pages, no errors", not bad,
          f"median {times[len(times)//2]:.2f}s, slowest {times[-1]:.2f}s" + (f" | bad: {bad[:8]}" if bad else ""))
    if user == "s01":
        r = s.get(B + "/admin-console/", timeout=60, allow_redirects=False)
        check("student blocked from admin console", r.status_code in (302, 403), str(r.status_code))
        r = s.get(B + "/finance/", timeout=60, allow_redirects=False)
        check("student blocked from finance", r.status_code in (302, 403), str(r.status_code))
    if user == "admin":
        for path, text in [("/admin-console/aicte/", "AICTE"), ("/hr/", "Deshmukh"), ("/admin-console/", "Conceicao")]:
            check(f"{path} shows '{text}'", text in s.get(B + path, timeout=60).text)

print(f"\n{sum(r[0] for r in results)}/{len(results)} checks passed")
