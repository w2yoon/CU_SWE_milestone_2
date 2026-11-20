#!/bin/bash
source "$(dirname "$0")/lib.sh"
py <<'PY'
from chk import *
(u0, u1), slug, ws, ch = setup(2)
(t0, i0), (t1, i1) = u0, u1
data = b"hello file " + S.encode()
def up(tok, name, content, ct="text/plain", field="file"):
    raw, ctype = multipart(field, name, content, ct)
    return http("POST", "/api/files", tok, raw=raw, ctype=ctype)
r = up(t0, "hello.txt", data); f = r[1].get("file") or {}
expect("C2.1", "upload -> 201 FileObj", 201, r, f.get("size") == len(data) and f.get("filename") == "hello.txt" and f.get("content_type") == "text/plain" and f.get("uploader_id") == i0 and str(f.get("created_at", "")).endswith("Z") and "id" in f)
fid = f.get("id")
r = http("GET", f"/api/files/{fid}", t1, port=8001)
expect("C2.2", "file metadata on another node", 200, r, (r[1].get("file") or {}).get("id") == fid)
expect("C2.3", "unknown file -> 404", 404, http("GET", "/api/files/999999999", t0))
st, js, hd, raw = http("GET", f"/api/files/{fid}/download", t0, port=8002, full=True)
cd = (hd or {}).get("Content-Disposition", "") if hd else ""
check("C2.4", "download bytes + Content-Type + Content-Disposition", st == 200 and raw == data and str((hd or {}).get("Content-Type", "")).startswith("text/plain") and "hello.txt" in cd, (st, raw[:40], cd))
r = up(t0, "big.bin", b"x" * (10 * 1024 * 1024 + 1), "application/octet-stream")
expect("C2.5", "upload > 10 MiB -> 413", 413, r)
a = up(t0, "x.txt", b"x", field="other"); b = http("POST", "/api/files", None, raw=b"x", ctype="multipart/form-data; boundary=zz")
check("C2.6", "no file part 400 / no token 401", (a[0], b[0]) == (400, 401), (a[0], b[0]))
r = post(t0, ch, "see file", file_ids=[fid]); fs = (r[1].get("message") or {}).get("files") or []
st, h = http("GET", f"/api/channels/{ch}/messages", t1)
hf = [m.get("files") for m in h.get("messages", []) if m.get("body") == "see file"]
expect("C2.7", "attach own file -> 201, message.files, also in history", 201, r, len(fs) == 1 and fs[0].get("id") == fid and hf and hf[0] and hf[0][0].get("id") == fid)
expect("C2.8", "attach the same file again -> 400", 400, post(t0, ch, "again", file_ids=[fid]))
o = up(t1, "theirs.txt", b"theirs").__getitem__(1).get("file", {}).get("id")
a = post(t0, ch, "steal", file_ids=[o]); b = post(t0, ch, "ghost", file_ids=[999999999]); c = post(t0, ch, "bad", file_ids="nope")
check("C2.9", "someone else's / unknown / non-list file_ids -> 400", (a[0], b[0], c[0]) == (400, 400, 400), (a[0], b[0], c[0]))
finish()
PY
