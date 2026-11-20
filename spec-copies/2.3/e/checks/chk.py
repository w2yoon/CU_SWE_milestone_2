import json, os, socket, struct, base64, time, random, signal, subprocess, urllib.request, urllib.error

VERBOSE = os.environ.get("FEEDBACK") == "verbose"
P = F = 0
S = str(random.randint(10**7, 10**8))
PW = "password123"


def ok(i, desc):
    global P
    P += 1
    if VERBOSE: print(f"PASS {i} {desc}", flush=True)


def bad(i, desc, why):
    global F
    F += 1
    if VERBOSE: print(f"FAIL {i} {desc}: {str(why)[:160]}", flush=True)


def check(i, desc, cond, why=""):
    ok(i, desc) if cond else bad(i, desc, why)
    return cond


def expect(i, desc, want, got, cond=True, body=None):
    """want: status; got: (status, body) tuple from http()."""
    st, b = got[0], got[1]
    if st == want and cond: ok(i, desc)
    else: bad(i, desc, f"expected {want}, got {st} {json.dumps(b)[:120] if body is None else body}")
    return st == want and cond


def finish():
    print(f"RESULT: {P} passed, {F} failed", flush=True)
    os._exit(0 if F == 0 else 1)


def _abort(*_):
    if VERBOSE: print("ABORT check exceeded 26s (a request or socket hangs)", flush=True)
    global F; F += 1
    finish()


signal.signal(signal.SIGALRM, _abort)
signal.alarm(25)
T0 = time.time()


def rule(i, desc, fn, budget=20):
    if time.time() - T0 > budget:
        return bad(i, desc, "not run: the check's 25 s budget is used up (earlier steps were too slow)")
    try:
        r = fn(); check(i, desc, r is True, r)
    except Exception as e:
        bad(i, desc, str(e).split("\n")[0])


def http(method, path, token=None, body=None, port=8000, raw=None, ctype="application/json", full=False):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"Content-Type": ctype}
    if token: h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method, headers=h)
    try:
        r = urllib.request.urlopen(req, timeout=4); st, hd, out = r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        st, hd, out = e.code, e.headers, e.read()
    except Exception as e:
        return (0, {"error": repr(e)}) + ((None, b""),) * full
    try: js = json.loads(out or b"{}")
    except Exception: js = {}
    return (st, js, hd, out) if full else (st, js)


def multipart(field, filename, content, ctype="text/plain"):
    bd = "----chk" + S
    body = (f"--{bd}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n").encode() + content + f"\r\n--{bd}--\r\n".encode()
    return body, "multipart/form-data; boundary=" + bd


def reg(name):
    st, d = http("POST", "/api/auth/register", body={"username": name, "password": PW})
    if st != 201 and VERBOSE: print(f"NOTE setup: register {name} -> {st} {json.dumps(d)[:100]}", flush=True)
    return d.get("token"), (d.get("user") or {}).get("id")


def setup(n=3):
    """owner + (n-1) members in a fresh workspace; all joined to a fresh public channel."""
    users = [reg(f"u{i}x{S}") for i in range(n)]
    ot = users[0][0]
    slug = "w" + S
    st, d = http("POST", "/api/workspaces", ot, {"slug": slug, "name": "W"})
    ws = d.get("workspace") or {}
    st, d = http("POST", f"/api/workspaces/{slug}/channels", ot, {"name": "c" + S})
    ch = (d.get("channel") or {}).get("id")
    for t, _ in users[1:]:
        http("POST", f"/api/channels/{ch}/join", t)
    return users, slug, ws, ch


def post(token, ch, body, port=8000, **kw):
    return http("POST", f"/api/channels/{ch}/messages", token, {"body": body, **kw}, port)


def port_pid(port):
    out = subprocess.run(["ss", "-ltnpH", f"sport = :{port}"], capture_output=True, text=True).stdout
    for part in out.split("pid=")[1:]:
        return int(part.split(",")[0])


def kill_port(port):
    p = port_pid(port)
    if p: os.kill(p, signal.SIGKILL)
    return p


def wait_health(port, secs):
    end = time.time() + secs
    while time.time() < end:
        if http("GET", "/api/health", port=port)[0] == 200: return True
        time.sleep(0.3)
    return False


class WS:
    def __init__(self, port, token):
        self.buf = b""; self.ok = False
        try:
            self.s = socket.create_connection(("127.0.0.1", port), timeout=3)
            key = base64.b64encode(os.urandom(16)).decode()
            self.s.sendall((f"GET /api/ws?token={token} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
                            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
            hdr = b""
            while b"\r\n\r\n" not in hdr:
                c = self.s.recv(1)
                if not c: return
                hdr += c
            self.ok = b" 101 " in hdr.split(b"\r\n")[0]
        except OSError:
            pass

    def send(self, obj):
        raw = json.dumps(obj).encode(); mask = os.urandom(4); n = len(raw)
        hdr = b"\x81" + (bytes([0x80 | n]) if n < 126 else b"\xfe" + struct.pack(">H", n))
        self.s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(raw)))

    def _need(self, n, deadline):
        while len(self.buf) < n:
            self.s.settimeout(max(0.02, deadline - time.time()))
            c = self.s.recv(65536)
            if not c: raise EOFError
            self.buf += c
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self, timeout=1.0):
        deadline = time.time() + timeout
        try:
            while True:
                h = self._need(2, deadline); op = h[0] & 15; n = h[1] & 127
                if n == 126: n = struct.unpack(">H", self._need(2, deadline))[0]
                elif n == 127: n = struct.unpack(">Q", self._need(8, deadline))[0]
                data = self._need(n, deadline)
                if op == 8: return None
                if op in (9, 10): continue
                return json.loads(data)
        except (socket.timeout, EOFError, OSError, ValueError):
            return None

    def drain(self, idle=1.0, limit=6.0):
        out = []; end = time.time() + limit; last = time.time()
        while time.time() < end and time.time() - last < idle:
            m = self.recv(0.2)
            if m is not None: out.append(m); last = time.time()
        return out

    def subscribe(self, ch):
        self.send({"type": "subscribe", "channel_id": ch})
        for _ in range(5):
            m = self.recv(2)
            if m is None or m.get("type") == "subscribed": return m

    def close(self):
        try: self.s.close()
        except OSError: pass


class IRC:
    def __init__(self, token=None, nick=None, port=6667):
        self.buf = b""; self.lines = []
        self.s = socket.create_connection(("127.0.0.1", port), timeout=3)
        if token: self.send(f"PASS {token}")
        if nick: self.send(f"NICK {nick}"); self.send(f"USER {nick} 0 * :{nick}")

    def send(self, line):
        self.s.sendall((line + "\r\n").encode())

    def read(self, until=None, timeout=3.0):
        """read lines until a line contains `until` (or timeout); returns all lines read."""
        got = []; end = time.time() + timeout
        while time.time() < end:
            while b"\r\n" in self.buf or b"\n" in self.buf:
                sep = b"\r\n" if b"\r\n" in self.buf else b"\n"
                l, self.buf = self.buf.split(sep, 1)
                l = l.decode(errors="replace"); got.append(l)
                if until and until in l: return got
            try:
                self.s.settimeout(max(0.02, end - time.time()))
                c = self.s.recv(4096)
                if not c: got.append("<closed>"); return got
                self.buf += c
            except socket.timeout:
                break
            except OSError:
                got.append("<closed>"); return got
        return got


def code(lines, num):
    return any(len(l.split()) > 1 and l.split()[1] == num for l in lines)


def redis_ping():
    try:
        s = socket.create_connection(("127.0.0.1", 6379), timeout=0.5); s.sendall(b"PING\r\n")
        r = s.recv(64); s.close(); return r.startswith(b"+PONG")
    except OSError:
        return False


def wait(fn, secs, step=0.2):
    end = time.time() + secs
    while time.time() < end:
        if fn(): return True
        time.sleep(step)
    return False


def msgs(frames, kind="message.created"):
    return [f for f in frames if f.get("type") == kind]
