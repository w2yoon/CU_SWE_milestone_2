import os,sqlite3,json,re,secrets,hashlib,base64,datetime,time,threading,socket,struct
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
DB='/app/huddle.db'; PORT=int(os.environ.get('PORT','8000')); NODE=int(os.environ.get('NODE_ID','0'))
def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
    c=sqlite3.connect(DB,timeout=5,check_same_thread=False)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA synchronous=NORMAL'); c.execute('PRAGMA busy_timeout=5000')
    return c
def init():
    c=db()
    c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS channel_seq(channel_id INTEGER PRIMARY KEY,last_event_id INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload_json TEXT,PRIMARY KEY(channel_id,event_id));''')
    c.commit(); c.close()
init()
def user(r):
    return {'id':r['id'],'username':r['username'],'display_name':r['display_name'],'timezone':r['timezone'],'avatar_url':r['avatar_url'],'status_text':r['status_text'],'status_emoji':r['status_emoji']}
def token_user(h):
    if not h.startswith('Bearer '): return None
    c=db(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(h[7:],)).fetchone(); c.close(); return r
def chan(c,id):
    return c.execute('SELECT * FROM channels WHERE id=?',(id,)).fetchone()
def msgobj(c,r):
    a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    reps=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':reps,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[],'mentions':[]}
def event(c,cid,typ,payload):
    c.execute('INSERT OR IGNORE INTO channel_seq(channel_id,last_event_id) VALUES(?,0)',(cid,))
    c.execute('UPDATE channel_seq SET last_event_id=last_event_id+1 WHERE channel_id=?',(cid,))
    eid=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(cid,)).fetchone()[0]
    c.execute('INSERT INTO events VALUES(?,?,?,?)',(cid,eid,typ,json.dumps(payload)))
    return eid
class H(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*a): pass
    def sendj(self,st,obj):
        b=json.dumps(obj,separators=(',',':')).encode(); self.send_response(st); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def readj(self):
        try:
            n=int(self.headers.get('Content-Length','0')); x=json.loads(self.rfile.read(n) or b'{}'); return x if isinstance(x,dict) else None
        except: return None
    def do_GET(self):
        if self.path=='/api/health': return self.sendj(200,{'status':'ok','node_id':NODE})
        if self.path.startswith('/api/ws'): return self.websocket()
        u=token_user(self.headers.get('Authorization',''))
        if not u:return self.sendj(401,{'error':'unauthorized'})
        p=self.path.split('?')[0]; c=db()
        if p=='/api/auth/me': c.close(); return self.sendj(200,{'user':user(u)})
        m=re.match(r'/api/workspaces/([^/]+)/members$',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:c.close();return self.sendj(404,{'error':'not found'})
            a=[dict(x) for x in c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],))];c.close();return self.sendj(200,{'members':a})
        m=re.match(r'/api/workspaces/([^/]+)$',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:c.close();return self.sendj(404,{'error':'not found'})
            ch=[dict(x) for x in c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],))]
            c.close();return self.sendj(200,{'workspace':dict(w),'channels':ch,'read_state':[]})
        if p=='/api/workspaces':
            rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close();return self.sendj(200,{'workspaces':[dict(x) for x in rows]})
        m=re.match(r'/api/channels/(\d+)/messages$',p)
        if m:
            q=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 ORDER BY id DESC',(int(m.group(1)),)).fetchall(); out=[msgobj(c,x) for x in q];c.close();return self.sendj(200,{'messages':out,'next_cursor':None})
        m=re.match(r'/api/messages/(\d+)/replies$',p)
        if m:
            q=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(int(m.group(1)),)).fetchall();out=[msgobj(c,x) for x in q];c.close();return self.sendj(200,{'replies':out,'next_cursor':None})
        m=re.match(r'/api/channels/(\d+)/members$',p)
        if m:
            q=c.execute('SELECT u.* FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?',(int(m.group(1)),)).fetchall();c.close();return self.sendj(200,{'members':[user(x) for x in q]})
        c.close();return self.sendj(404,{'error':'not found'})
    def do_POST(self):
        u=token_user(self.headers.get('Authorization',''))
        if self.path=='/api/auth/register':
            d=self.readj()
            if not d or not re.fullmatch(r'[A-Za-z0-9_]+',str(d.get('username',''))) or len(str(d.get('password','')))<8:return self.sendj(400,{'error':'invalid'})
            c=db()
            try:
                c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(d['username'],hashlib.sha256(d['password'].encode()).hexdigest(),d.get('display_name') or d['username']));c.commit()
            except sqlite3.IntegrityError:c.close();return self.sendj(409,{'error':'duplicate'})
            r=c.execute('SELECT * FROM users WHERE username=?',(d['username'],)).fetchone();t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id']));c.commit();c.close();return self.sendj(201,{'user':user(r),'token':t})
        if self.path=='/api/auth/login':
            d=self.readj();c=db();r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(d.get('username'),hashlib.sha256(str(d.get('password','')).encode()).hexdigest())).fetchone()
            if not r:c.close();return self.sendj(401,{'error':'invalid'})
            t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id']));c.commit();c.close();return self.sendj(200,{'user':user(r),'token':t})
        if not u:return self.sendj(401,{'error':'unauthorized'})
        d=self.readj() or {};c=db()
        if self.path=='/api/workspaces':
            if not re.fullmatch(r'[a-z0-9-]{2,32}',str(d.get('slug',''))) or not str(d.get('name','')).strip():
                c.close(); return self.sendj(400,{'error':'invalid'})
            try:
                c.execute('BEGIN IMMEDIATE')
                c.execute('INSERT INTO workspaces(slug,name,owner_id,join_mode) VALUES(?,?,?,?)',(d['slug'],d['name'],u['id'],'open'))
                wid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
                c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'))
                c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,0,?)',(wid,'general',''))
                cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
                c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']))
                c.execute('INSERT INTO channel_seq VALUES(?,0)',(cid,))
                c.commit()
            except sqlite3.IntegrityError:
                c.rollback(); c.close(); return self.sendj(409,{'error':'duplicate'})
            w=dict(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone())
            g=dict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())
            c.close(); return self.sendj(201,{'workspace':w,'general_channel':g})
        m=re.match(r'/api/workspaces/([^/]+)/channels$',self.path)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:c.close();return self.sendj(404,{'error':'not found'})
            mem=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            if not mem:c.close();return self.sendj(403,{'error':'forbidden'})
            if not re.fullmatch(r'[a-z0-9-]{1,32}',str(d.get('name',''))):c.close();return self.sendj(400,{'error':'invalid'})
            if d.get('is_private') and mem['role'] not in ('owner','admin'):c.close();return self.sendj(403,{'error':'forbidden'})
            try:c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],d['name'],int(bool(d.get('is_private'))),d.get('topic',''))); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']));c.execute('INSERT INTO channel_seq VALUES(?,0)',(cid,));c.commit()
            except sqlite3.IntegrityError:c.rollback();c.close();return self.sendj(409,{'error':'duplicate'})
            r=dict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone());c.close();return self.sendj(201,{'channel':r})
        m=re.match(r'/api/channels/(\d+)/join$',self.path)
        if m:
            ch=chan(c,int(m.group(1)))
            if not ch:c.close();return self.sendj(404,{'error':'not found'})
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();mem=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            if ch['is_private']:c.close();return self.sendj(403,{'error':'private'})
            if not mem:c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w['id'],u['id'],'member'))
            c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']));c.commit();c.close();return self.sendj(200,{'channel':dict(ch)})
        m=re.match(r'/api/channels/(\d+)/messages$',self.path)
        if m:
            cid=int(m.group(1));ch=chan(c,cid)
            if not ch:c.close();return self.sendj(404,{'error':'not found'})
            if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone():c.close();return self.sendj(403,{'error':'forbidden'})
            body=str(d.get('body',''))
            if not body.strip():c.close();return self.sendj(400,{'error':'empty'})
            try:
                t=now();c.execute('BEGIN IMMEDIATE');c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,u['id'],body,d.get('parent_id'),t));mid=c.execute('SELECT last_insert_rowid()').fetchone()[0];r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();mo=msgobj(c,r);event(c,cid,'message.reply' if d.get('parent_id') else 'message.created',{'message':mo});c.commit();c.close();return self.sendj(201,{'message':mo})
            except Exception:
                c.rollback();c.close();return self.sendj(500,{'error':'write failed'})
        c.close();return self.sendj(404,{'error':'not found'})
    def do_PATCH(self):
        u=token_user(self.headers.get('Authorization',''))
        if not u:return self.sendj(401,{'error':'unauthorized'})
        p=self.path;d=self.readj() or {};c=db();m=re.match(r'/api/channels/(\d+)$',p)
        if m:
            ch=chan(c,int(m.group(1)))
            if not ch:c.close();return self.sendj(404,{'error':'not found'})
            c.execute('UPDATE channels SET topic=? WHERE id=?',(d.get('topic',''),ch['id']));c.commit();r=dict(chan(c,ch['id']));c.close();return self.sendj(200,{'channel':r})
        c.close();return self.sendj(404,{'error':'not found'})
    def do_DELETE(self): self.sendj(404,{'error':'not found'})
    def websocket(self):
        q=self.path.split('?',1)[1] if '?' in self.path else ''; tok=dict(x.split('=',1) for x in q.split('&') if '=' in x).get('token',''); c=db();u=c.execute('SELECT user_id FROM tokens WHERE token=?',(tok,)).fetchone();c.close()
        if not u:self.send_response(401);self.end_headers();return
        key=self.headers.get('Sec-WebSocket-Key');accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode();self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers();self.wfile.flush(); self.connection.settimeout(.05); sub={}; last={}
        try:
            while True:
                try:
                    def exact(n):
                        b=b''
                        while len(b)<n:
                            z=self.connection.recv(n-len(b))
                            if not z: raise OSError()
                            b+=z
                        return b
                    h=exact(2)
                    n=h[1]&127
                    if n==126:n=struct.unpack('>H',exact(2))[0]
                    elif n==127:n=struct.unpack('>Q',exact(8))[0]
                    mask=exact(4);data=exact(n);data=bytes(data[i]^mask[i%4] for i in range(n));o=json.loads(data)
                    if o.get('type') in ('subscribe','resume'):
                        cid=int(o.get('channel_id'));typ=o.get('type');c=db(); row=c.execute('SELECT COALESCE(last_event_id,0) x FROM channel_seq WHERE channel_id=?',(cid,)).fetchone(); head=row['x'] if row else 0;start=int(o.get('since_event_id',0)); sub[cid]=True;last[cid]=start
                        if typ=='subscribe':
                            self.ws({'type':'subscribed','channel_id':cid,'head_event_id':head})
                        else:
                            rows=c.execute('SELECT event_id,type,payload_json FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,start)).fetchall()
                            for rr in rows:
                                x={'type':rr['type'],'event_id':rr['event_id'],'channel_id':cid};x.update(json.loads(rr['payload_json']));self.ws(x)
                            last[cid]=head
                            self.ws({'type':'resumed','channel_id':cid,'head_event_id':head})
                        c.close()
                except socket.timeout: pass
                c=db()
                for cid in list(sub):
                    rows=c.execute('SELECT event_id,type,payload_json FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last.get(cid,0))).fetchall()
                    for r in rows:
                        x={'type':r['type'],'event_id':r['event_id'],'channel_id':cid};x.update(json.loads(r['payload_json']));self.ws(x);last[cid]=r['event_id']
                    if rows and False: pass
                c.close();time.sleep(.05)
        except (OSError,BrokenPipeError,ValueError): return
    def ws(self,o):
        b=json.dumps(o,separators=(',',':')).encode();n=len(b);h=b'\x81'+(bytes([n]) if n<126 else (b'\x7e'+struct.pack('>H',n) if n<65536 else b'\x7f'+struct.pack('>Q',n)));self.wfile.write(h+b);self.wfile.flush()
ThreadingHTTPServer.daemon_threads=True
ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
