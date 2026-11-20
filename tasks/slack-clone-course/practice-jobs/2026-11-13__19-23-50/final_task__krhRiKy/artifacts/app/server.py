import os,json,base64,hashlib,socket,threading,time,re
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
import core
NODE=int(os.environ.get('NODE_ID','0')); PORT=int(os.environ.get('PORT','8000'))

def sendws(s,obj):
    b=json.dumps(obj,separators=(',',':')).encode(); n=len(b)
    h=bytes([0x81,n]) if n<126 else b'\x81\x7e'+n.to_bytes(2,'big')
    s.sendall(h+b)
def recvws(s):
    h=s.recv(2)
    if len(h)<2:return None
    n=h[1]&127
    if n==126:n=int.from_bytes(s.recv(2),'big')
    elif n==127:n=int.from_bytes(s.recv(8),'big')
    mask=s.recv(4); b=bytearray(s.recv(n))
    for i in range(len(b)):b[i]^=mask[i%4]
    return json.loads(b)
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def js(self,code,obj):
        b=json.dumps(obj).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def body(self):
        try:return json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))) or b'{}')
        except:return None
    def user(self,c):
        x=self.headers.get('Authorization','')
        return core.auth(c,x[7:] if x.startswith('Bearer ') else '')
    def get_http(self):
        p=urlparse(self.path); a=p.path.strip('/').split('/'); c=core.db()
        try:
            if p.path=='/api/health': return self.js(200,{'status':'ok','node_id':NODE})
            if p.path=='/': return self.html()
            u=self.user(c)
            if p.path=='/api/auth/me':
                if not u:return self.js(401,{'error':'unauthorized'})
                return self.js(200,{'user':core.user_obj(u)})
            if len(a)>=3 and a[1]=='workspaces' and a[3:] == ['members']:
                if not u:return self.js(401,{'error':'unauthorized'})
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(a[2],)).fetchone()
                if not w:return self.js(404,{'error':'not found'})
                rows=c.execute('SELECT user_id,role FROM workspace_members WHERE workspace_id=?',(w['id'],))
                return self.js(200,{'members':[dict(x) for x in rows]})
            if p.path=='/api/workspaces':
                if not u:return self.js(401,{'error':'unauthorized'})
                rows=c.execute('SELECT w.* FROM workspaces w JOIN workspace_members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],))
                return self.js(200,{'workspaces':[dict(x) for x in rows]})
            if len(a)==3 and a[1]=='workspaces':
                if not u:return self.js(401,{'error':'unauthorized'})
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(a[2],)).fetchone()
                if not w:return self.js(404,{'error':'not found'})
                chans=[dict(x) for x in c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],))]
                return self.js(200,{'workspace':dict(w),'channels':chans,'read_state':{}})
            if len(a)==3 and a[1]=='channels' and a[2].isdigit() and p.path.endswith('/members'):
                if not u:return self.js(401,{'error':'unauthorized'})
                ch=c.execute('SELECT * FROM channels WHERE id=?',(int(a[2]),)).fetchone()
                if not ch:return self.js(404,{'error':'not found'})
                rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],))
                return self.js(200,{'members':[core.user_obj(x) for x in rows]})
            if len(a)==4 and a[1]=='channels' and a[3]=='messages':
                if not u:return self.js(401,{'error':'unauthorized'})
                ch=int(a[2]); x=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
                if not x:return self.js(404,{'error':'not found'})
                if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch,u['id'])).fetchone():return self.js(403,{'error':'forbidden'})
                rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 ORDER BY id DESC',(ch,))
                return self.js(200,{'messages':[core.msg_obj(c,r) for r in rows],'next_cursor':None})
            if len(a)==4 and a[1]=='messages' and a[3]=='replies':
                if not u:return self.js(401,{'error':'unauthorized'})
                rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(int(a[2]),))
                return self.js(200,{'replies':[core.msg_obj(c,r) for r in rows],'next_cursor':None})
            return self.js(404,{'error':'not found'})
        except Exception as e:self.js(500,{'error':str(e)})
    def do_POST(self):
        p=urlparse(self.path); a=p.path.strip('/').split('/'); c=core.db(); b=self.body()
        try:
            if p.path=='/api/auth/register':
                if not isinstance(b,dict) or not re.fullmatch(r'[A-Za-z0-9_]+',str(b.get('username',''))) or len(str(b.get('password','')))<8:return self.js(400,{'error':'invalid'})
                try:
                    cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],b['password'],b.get('display_name') or b['username'])); uid=cur.lastrowid
                    tok=base64.urlsafe_b64encode(os.urandom(18)).decode();c.execute('INSERT INTO tokens VALUES(?,?)',(tok,uid));c.commit()
                    return self.js(201,{'user':core.user_obj(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()),'token':tok})
                except: c.rollback();return self.js(409,{'error':'duplicate'})
            if p.path=='/api/auth/login':
                u=c.execute('SELECT * FROM users WHERE username=? AND password=?',(b.get('username'),b.get('password'))).fetchone()
                if not u:return self.js(401,{'error':'invalid'})
                tok=base64.urlsafe_b64encode(os.urandom(18)).decode();c.execute('INSERT INTO tokens VALUES(?,?)',(tok,u['id']));c.commit();return self.js(200,{'user':core.user_obj(u),'token':tok})
            u=self.user(c)
            if not u:return self.js(401,{'error':'unauthorized'})
            if p.path=='/api/workspaces':
                if not re.fullmatch(r'[a-z0-9-]{2,32}',str(b.get('slug',''))) or not b.get('name'):return self.js(400,{'error':'invalid'})
                try:
                    c.execute('BEGIN IMMEDIATE');c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id'])); wid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
                    c.execute('INSERT INTO workspace_members VALUES(?,?,?)',(wid,u['id'],'owner'));c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']));c.commit()
                    return self.js(201,{'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
                except:c.rollback();return self.js(409,{'error':'duplicate'})
            if len(a)==4 and a[1]=='workspaces' and a[3]=='channels':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(a[2],)).fetchone()
                if not w:return self.js(404,{'error':'not found'})
                mem=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not mem:return self.js(403,{'error':'forbidden'})
                if not re.fullmatch(r'[a-z0-9-]{1,32}',str(b.get('name',''))):return self.js(400,{'error':'invalid'})
                if b.get('is_private') and mem['role'] not in ('owner','admin'):return self.js(403,{'error':'forbidden'})
                try:c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b['name'],int(bool(b.get('is_private'))),b.get('topic','')));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']));c.commit();return self.js(201,{'channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
                except:c.rollback();return self.js(409,{'error':'duplicate'})
            if len(a)==4 and a[1]=='channels' and a[3]=='join':
                ch=c.execute('SELECT * FROM channels WHERE id=?',(int(a[2]),)).fetchone()
                if not ch:return self.js(404,{'error':'not found'})
                if ch['is_private']:return self.js(403,{'error':'forbidden'})
                c.execute('INSERT OR IGNORE INTO workspace_members VALUES(?,?,?)',(ch['workspace_id'],u['id'],'member'));c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']));c.commit();return self.js(200,{'channel':dict(ch)})
            if len(a)==4 and a[1]=='channels' and a[3]=='messages':
                c.execute('BEGIN IMMEDIATE')
                try:mo,eid=core.create_message(c,int(a[2]),u['id'],str(b.get('body','')),b.get('parent_id'));c.commit();return self.js(201,{'message':mo})
                except ValueError:c.rollback();return self.js(400,{'error':'empty'})
                except LookupError:c.rollback();return self.js(404,{'error':'not found'})
                except PermissionError as e:c.rollback();return self.js(423 if str(e)=='archived' else 403,{'error':str(e)})
            return self.js(404,{'error':'not found'})
        except Exception as e:
            try:c.rollback()
            except:pass
            return self.js(500,{'error':str(e)})
    def do_PUT(self): return self.do_POST()
    def do_PATCH(self): return self.js(404,{'error':'not found'})
    def do_DELETE(self): return self.js(404,{'error':'not found'})
    def html(self):
        b=b'<html><head><title>Huddle</title></head><body><div id="app">Huddle</div></body></html>';self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def do_CONNECT(self): pass
    def handle_ws(self):
        c=core.db(); q=parse_qs(urlparse(self.path).query); tok=(q.get('token') or [None])[0]; u=core.auth(c,tok) if tok else self.user(c)
        if not u:return self.send_error(401)
        key=self.headers.get('Sec-WebSocket-Key',''); acc=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',acc);self.end_headers(); self.connection.settimeout(.05); subs={}
        while True:
            try:
                try:
                    x=recvws(self.connection)
                    if x:
                        ch=int(x.get('channel_id')); subs[ch]=int(x.get('since_event_id',0)); typ='resume' if x.get('type')=='resume' else 'subscribe'
                        if typ=='resume':
                            for r in c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,subs[ch])):
                                z=json.loads(r['payload_json']);z.update(type=r['type'],event_id=r['event_id'],channel_id=ch);sendws(self.connection,z);subs[ch]=r['event_id']
                        hr=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(ch,)).fetchone(); head=hr[0] if hr else 0
                        sendws(self.connection,{'type':'resumed' if typ=='resume' else 'subscribed','channel_id':ch,'head_event_id':head})
                except socket.timeout:pass
                for ch,last in list(subs.items()):
                    for r in c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,last)):
                        z=json.loads(r['payload_json']);z.update(type=r['type'],event_id=r['event_id'],channel_id=ch);sendws(self.connection,z);subs[ch]=r['event_id']
                time.sleep(.05)
            except (OSError,ValueError):break
    def do_GET(self):
        if self.path.startswith('/api/ws') and self.headers.get('Upgrade','').lower()=='websocket': return self.handle_ws()
        return self.get_http()
ThreadingHTTPServer.daemon_threads=True
ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
