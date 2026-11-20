import os,sqlite3,json,secrets,hashlib,re,time,base64,struct,socket,threading
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
DB='/app/huddle.db'; NODE=int(os.getenv('NODE_ID','0')); PORT=int(os.getenv('PORT','8000'))
def conn():
    c=sqlite3.connect(DB,timeout=30,check_same_thread=False); c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=5000')
    for _ in range(20):
        try:
            c.execute('PRAGMA journal_mode=WAL')
            break
        except sqlite3.OperationalError:
            time.sleep(0.05)
    return c
def init():
    for attempt in range(60):
        c=conn()
        try:
            c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS cmembers(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_seq(channel_id INTEGER PRIMARY KEY,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload_json TEXT,PRIMARY KEY(channel_id,event_id));''')
            c.commit(); c.close(); return
        except sqlite3.OperationalError as e:
            c.close()
            if 'locked' not in str(e).lower(): raise
            time.sleep(0.1)
    raise RuntimeError('database initialization locked out')
init()
def now(): return time.strftime('%Y-%m-%dT%H:%M:%S.',time.gmtime())+('%03d'%(int(time.time()*1000)%1000))+'Z'
def user(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def auth(h):
    x=h.get('Authorization','')
    if not x.startswith('Bearer '): return None
    c=conn(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(x[7:],)).fetchone(); c.close(); return r
def emit(c,ch,typ,payload):
    c.execute('INSERT OR IGNORE INTO channel_seq(channel_id,last_event_id) VALUES(?,0)',(ch,))
    c.execute('UPDATE channel_seq SET last_event_id=last_event_id+1 WHERE channel_id=?',(ch,))
    eid=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(ch,)).fetchone()[0]
    c.execute('INSERT INTO events VALUES(?,?,?,?)',(ch,eid,typ,json.dumps(payload))); return eid
def msgobj(c,r):
    a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    replies=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':replies,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[],'mentions':[]}
def wsinfo(c,ch,uid):
    return c.execute('SELECT c.*,w.owner_id,w.join_mode FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE c.id=?',(ch,)).fetchone()
class H(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*a): pass
    def sendj(self,st,obj):
        b=json.dumps(obj).encode(); self.send_response(st); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def body(self):
        n=int(self.headers.get('Content-Length','0')); x=self.rfile.read(n)
        try: v=json.loads(x); return v if isinstance(v,dict) else {}
        except: return {}
    def do_GET(self):
        if self.path=='/api/health': return self.sendj(200,{'status':'ok','node_id':NODE})
        if self.path.startswith('/api/ws'): return self.websocket()
        u=auth(self.headers)
        if self.path=='/api/auth/me':
            return self.sendj(200,{'user':user(u)}) if u else self.sendj(401,{'error':'unauthorized'})
        if not u: return self.sendj(401,{'error':'unauthorized'})
        path=self.path.split('?')[0]; c=conn()
        try:
            m=re.match(r'^/api/channels/(\d+)/messages$',path)
            if m:
                ch=int(m.group(1)); ci=wsinfo(c,ch,u['id'])
                if not ci:return self.sendj(404,{'error':'not found'})
                if not c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(ch,u['id'])).fetchone():return self.sendj(403,{'error':'forbidden'})
                rs=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 ORDER BY id DESC',(ch,)).fetchall()
                return self.sendj(200,{'messages':[msgobj(c,r) for r in rs],'next_cursor':None})
            m=re.match(r'^/api/messages/(\d+)/replies$',path)
            if m:
                r=c.execute('SELECT * FROM messages WHERE id=?',(int(m.group(1)),)).fetchone()
                if not r:return self.sendj(404,{'error':'not found'})
                rs=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(r['id'],))
                return self.sendj(200,{'replies':[msgobj(c,x) for x in rs],'next_cursor':None})
            m=re.match(r'^/api/channels/(\d+)/members$',path)
            if m:
                ch=int(m.group(1)); ci=wsinfo(c,ch,u['id'])
                if not ci:return self.sendj(404,{'error':'not found'})
                rs=c.execute('SELECT u.* FROM cmembers x JOIN users u ON u.id=x.user_id WHERE x.channel_id=?',(ch,))
                return self.sendj(200,{'members':[user(x) for x in rs]})
            if path=='/api/workspaces':
                rs=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],))
                return self.sendj(200,{'workspaces':[dict(x) for x in rs]})
            m=re.match(r'^/api/workspaces/([^/]+)$',path)
            if m:
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
                if not w:return self.sendj(404,{'error':'not found'})
                cs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],))
                return self.sendj(200,{'workspace':dict(w),'channels':[dict(x) for x in cs],'read_state':{}})
            m=re.match(r'^/api/workspaces/([^/]+)/members$',path)
            if m:
                w=c.execute('SELECT id FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
                if not w:return self.sendj(404,{'error':'not found'})
                rs=c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],))
                return self.sendj(200,{'members':[dict(x) for x in rs]})
            return self.sendj(404,{'error':'not found'})
        finally:c.close()
    def do_POST(self):
        if self.path=='/api/auth/register':
            b=self.body(); name=b.get('username',''); pw=b.get('password','')
            if not re.fullmatch(r'[A-Za-z0-9_]+',name) or len(pw)<8:return self.sendj(400,{'error':'invalid'})
            c=conn()
            try:
                c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(name,pw,b.get('display_name') or name)); uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; tok=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(tok,uid));c.commit(); r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone();return self.sendj(201,{'user':user(r),'token':tok})
            except sqlite3.IntegrityError:return self.sendj(409,{'error':'duplicate'})
            finally:c.close()
        if self.path=='/api/auth/login':
            b=self.body(); c=conn()
            r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(b.get('username',''),b.get('password',''))).fetchone()
            if not r:
                c.close(); return self.sendj(401,{'error':'unauthorized'})
            tok=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(tok,r['id'])); c.commit(); c.close()
            return self.sendj(200,{'user':user(r),'token':tok})
        u=auth(self.headers)
        if not u:return self.sendj(401,{'error':'unauthorized'})
        b=self.body(); path=self.path.split('?')[0]; c=conn()
        try:
            if path=='/api/workspaces':
                slug=b.get('slug',''); name=b.get('name','')
                if not re.fullmatch(r'[a-z0-9-]{2,32}',slug) or not name:return self.sendj(400,{'error':'invalid'})
                try:
                    c.execute('BEGIN IMMEDIATE'); c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(slug,name,u['id'])); wid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner')); c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')); ch=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO cmembers VALUES(?,?)',(ch,u['id'])); c.commit(); w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); return self.sendj(201,{'workspace':dict(w),'general_channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())})
                except sqlite3.IntegrityError:c.rollback();return self.sendj(409,{'error':'duplicate'})
            m=re.match(r'^/api/workspaces/([^/]+)/channels$',path)
            if m:
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
                if not w:return self.sendj(404,{'error':'not found'})
                role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not role:return self.sendj(403,{'error':'forbidden'})
                name=b.get('name','')
                if not re.fullmatch(r'[a-z0-9-]{1,32}',name):return self.sendj(400,{'error':'invalid'})
                try:c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],name,int(bool(b.get('is_private'))),b.get('topic','')));ch=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO cmembers VALUES(?,?)',(ch,u['id']));c.commit();return self.sendj(201,{'channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())})
                except sqlite3.IntegrityError:return self.sendj(409,{'error':'duplicate'})
            m=re.match(r'^/api/channels/(\d+)/join$',path)
            if m:
                ch=int(m.group(1)); ci=wsinfo(c,ch,u['id'])
                if not ci:return self.sendj(404,{'error':'not found'})
                if ci['is_private']:return self.sendj(403,{'error':'private'})
                c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ci['workspace_id'],u['id'],'member'));c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(ch,u['id']));c.commit();return self.sendj(200,{'channel':dict(ci)})
            m=re.match(r'^/api/channels/(\d+)/(leave|archive|unarchive)$',path)
            if m:
                ch=int(m.group(1));ci=wsinfo(c,ch,u['id'])
                if not ci:return self.sendj(404,{'error':'not found'})
                if m.group(2)=='leave':c.execute('DELETE FROM cmembers WHERE channel_id=? AND user_id=?',(ch,u['id']))
                else:
                    if ci['owner_id']!=u['id']:return self.sendj(403,{'error':'forbidden'})
                    c.execute('UPDATE channels SET is_archived=? WHERE id=?',(m.group(2)=='archive',ch))
                c.commit();return self.sendj(200,{'channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())})
            m=re.match(r'^/api/channels/(\d+)/messages$',path)
            if m:
                ch=int(m.group(1));ci=wsinfo(c,ch,u['id'])
                if not ci:return self.sendj(404,{'error':'not found'})
                if not c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(ch,u['id'])).fetchone():return self.sendj(403,{'error':'forbidden'})
                if ci['is_archived']:return self.sendj(423,{'error':'archived'})
                body=b.get('body','')
                if not isinstance(body,str) or not body.strip():return self.sendj(400,{'error':'empty'})
                if body.startswith('/shrug'): body=(body[6:].strip()+' ¯\\_(ツ)_/¯').strip()
                c.execute('BEGIN IMMEDIATE'); t=now(); parent=b.get('parent_id');c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch,u['id'],body,parent,t)); mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); typ='message.reply' if parent else 'message.created'; eid=emit(c,ch,typ,{'type':typ,'channel_id':ch,'message':msgobj(c,r),'event_id':0}); c.commit(); return self.sendj(201,{'message':msgobj(c,r)})
            return self.sendj(404,{'error':'not found'})
        except Exception as e:
            try:c.rollback()
            except:pass
            return self.sendj(400,{'error':'bad request'})
        finally:c.close()
    def do_PATCH(self):
        u=auth(self.headers)
        if not u:return self.sendj(401,{'error':'unauthorized'})
        p=self.path.split('?')[0]; b=self.body(); c=conn()
        m=re.match(r'^/api/channels/(\d+)$',p)
        if m:
            ci=wsinfo(c,int(m.group(1)),u['id'])
            if not ci:return self.sendj(404,{'error':'not found'})
            if not c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(ci['id'],u['id'])).fetchone() and ci['owner_id']!=u['id']:return self.sendj(403,{'error':'forbidden'})
            if not isinstance(b.get('topic',''),str) or len(b.get('topic',''))>250:return self.sendj(400,{'error':'invalid'})
            c.execute('UPDATE channels SET topic=? WHERE id=?',(b.get('topic',''),ci['id']));c.commit();return self.sendj(200,{'channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(ci['id'],)).fetchone())})
        return self.sendj(404,{'error':'not found'})
    def websocket(self):
        q=self.path.split('?',1)[1] if '?' in self.path else ''; tok=dict(x.split('=',1) for x in q.split('&') if '=' in x).get('token','')
        c=conn(); ok=c.execute('SELECT 1 FROM tokens WHERE token=?',(tok,)).fetchone(); c.close()
        if not ok:self.send_response(401);self.end_headers();return
        key=self.headers.get('Sec-WebSocket-Key'); acc=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',acc);self.end_headers(); self.wfile.flush()
        self.connection.settimeout(.2); sub={}; last={}
        def out(o):
            b=json.dumps(o).encode(); n=len(b)
            if n < 126: h=b'\x81'+bytes([n])
            elif n < 65536: h=b'\x81\x7e'+struct.pack('>H',n)
            else: h=b'\x81\x7f'+struct.pack('>Q',n)
            self.connection.sendall(h+b)
        end=time.time()+60
        try:
            while time.time()<end:
                try:
                    h=self.connection.recv(2)
                    if h:
                        n=h[1]&127
                        if n==126: n=struct.unpack('>H',self.connection.recv(2))[0]
                        mask=self.connection.recv(4); data=self.connection.recv(n); data=bytes(x^mask[i%4] for i,x in enumerate(data)); z=json.loads(data); ch=int(z.get('channel_id')); typ=z.get('type'); c=conn(); head=c.execute('SELECT COALESCE(last_event_id,0) FROM channel_seq WHERE channel_id=?',(ch,)).fetchone(); head=head[0] if head else 0
                        if typ=='subscribe': sub[ch]=True;last[ch]=head;out({'type':'subscribed','channel_id':ch,'head_event_id':head})
                        elif typ=='resume':
                            since=int(z.get('since_event_id',0)); rows=c.execute('SELECT event_id,payload_json FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since)).fetchall()
                            for r in rows: out(json.loads(r['payload_json'])|{'event_id':r['event_id']})
                            last[ch]=head;out({'type':'resumed','channel_id':ch,'head_event_id':head})
                        c.close()
                except socket.timeout: pass
                except Exception: pass
                if sub:
                    c=conn()
                    for ch in list(sub):
                        rows=c.execute('SELECT event_id,payload_json FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,last.get(ch,0))).fetchall()
                        for r in rows: out(json.loads(r['payload_json'])|{'event_id':r['event_id']});last[ch]=r['event_id']
                    c.close()
        except (OSError,BrokenPipeError): pass

if __name__=='__main__':
    ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
