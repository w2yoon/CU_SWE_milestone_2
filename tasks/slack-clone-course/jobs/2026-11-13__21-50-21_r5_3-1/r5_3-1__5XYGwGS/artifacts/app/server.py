import os,re,json,shutil,time,sqlite3,hashlib,secrets,base64,datetime,threading,urllib.parse
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
DB='/app/huddle.db'; PORT=int(os.getenv('PORT','8000')); NODE=int(os.getenv('NODE_ID','0'))
lock=threading.Lock()
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
    c=sqlite3.connect(DB,timeout=5); c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=5000'); return c
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
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload_json TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,uses INTEGER DEFAULT 0,max_uses INTEGER DEFAULT 1,expires_at TEXT);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji)); CREATE TABLE IF NOT EXISTS message_files(message_id INTEGER,file_id INTEGER,PRIMARY KEY(message_id,file_id)); CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,node_id INTEGER); CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,UNIQUE(workspace_id,handle)); CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id)); CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,path TEXT); CREATE TABLE IF NOT EXISTS dm_pairs(user_a INTEGER,user_b INTEGER UNIQUE,channel_id INTEGER); CREATE TABLE IF NOT EXISTS read_states(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id)); CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);''')
    for q in ("ALTER TABLE messages ADD COLUMN event_id INTEGER DEFAULT 0", "ALTER TABLE messages ADD COLUMN mentions_json TEXT DEFAULT '[]'", "ALTER TABLE invitations ADD COLUMN invited_username TEXT", "ALTER TABLE invitations ADD COLUMN created_by INTEGER", "ALTER TABLE invitations ADD COLUMN revoked INTEGER DEFAULT 0"):
        try: c.execute(q)
        except sqlite3.OperationalError: pass
    try:
        cols=[x['name'] for x in c.execute('PRAGMA table_info(dm_pairs)').fetchall()]
        if cols and 'user_b' in cols:
            c.execute('ALTER TABLE dm_pairs RENAME TO dm_pairs_old')
            c.execute('CREATE TABLE dm_pairs(user_a INTEGER,user_b INTEGER,channel_id INTEGER,UNIQUE(user_a,user_b))')
            c.execute('INSERT OR IGNORE INTO dm_pairs SELECT user_a,user_b,channel_id FROM dm_pairs_old')
    except sqlite3.OperationalError: pass
    try: c.execute('CREATE UNIQUE INDEX IF NOT EXISTS dm_pair_unique ON dm_pairs(user_a,user_b)')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE presence ADD COLUMN last_seen REAL DEFAULT 0')
    except sqlite3.OperationalError: pass
    c.commit(); c.close()
os.makedirs('/app/data/files',exist_ok=True)
init()
def user_obj(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def ws_obj(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def ch_obj(r): return {k:(bool(r[k]) if k in ('is_private','is_dm','is_archived') else r[k]) for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
def ws_frame(obj):
    b=json.dumps(obj,separators=(',',':')).encode(); n=len(b)
    if n<126:h=b'\x81'+bytes([n])
    elif n<65536:h=b'\x81\x7e'+__import__('struct').pack('>H',n)
    else:h=b'\x81\x7f'+__import__('struct').pack('>Q',n)
    return h+b
def event_row(c,ch,eid,typ,payload):
    c.execute('INSERT INTO events(channel_id,event_id,type,payload_json) VALUES(?,?,?,?)',(ch,eid,typ,json.dumps(payload)))
def bump(c,ch,typ,payload):
    c.execute('UPDATE channel_seq SET last_event_id=last_event_id+1 WHERE channel_id=?',(ch,))
    eid=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(ch,)).fetchone()['last_event_id'];event_row(c,ch,eid,typ,payload);return eid
def fobj(r): return {k:r[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}
def multipart(handler):
    n=int(handler.headers.get('Content-Length','0'))
    if n>10485760:
        handler.rfile.read(n)
        return None,'large'
    raw=handler.rfile.read(n); ct=handler.headers.get('Content-Type','')
    m=re.search(r'boundary=([^;]+)',ct)
    if not m:return None,'missing'
    b=('--'+m.group(1).strip('"')).encode()
    for part in raw.split(b):
        if b'filename=' not in part or not re.search(br'name="file"',part):continue
        head,sep,data=part.partition(b'\r\n\r\n')
        if not sep:continue
        fn=re.search(br'filename="([^"]*)"',head); typ=re.search(br'Content-Type:\s*([^\r\n]+)',head,re.I)
        if fn:
            return (fn.group(1).decode(errors='replace'),(typ.group(1).decode() if typ else 'application/octet-stream'),data.rstrip(b'\r\n-')),None
    return None,'missing'
class H(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*a): pass
    def sendj(self,status,obj):
        b=json.dumps(obj,separators=(',',':')).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def body(self):
        n=int(self.headers.get('Content-Length','0')); x=self.rfile.read(n)
        try: v=json.loads(x or b'{}'); return v if isinstance(v,dict) else None
        except: return None
    def auth(self):
        x=self.headers.get('Authorization','')
        if not x.startswith('Bearer '): return None
        c=db(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(x[7:],)).fetchone(); c.close(); return r
    def require(self):
        u=self.auth()
        if not u: self.sendj(401,{'error':'unauthorized'})
        return u
    def request_path(self):
        q=urllib.parse.urlsplit(self.path); return q.path,q.query
    def do_GET(self):
        p,q=self.request_path()
        if p=='/api/health': return self.sendj(200,{'status':'ok','node_id':NODE})
        if p=='/api/ws':
            tok=urllib.parse.parse_qs(q).get('token',[''])[0]
            c=db(); uu=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(tok,)).fetchone();c.close()
            if not uu:return self.sendj(401,{'error':'unauthorized'})
            import hashlib,base64,struct
            key=self.headers.get('Sec-WebSocket-Key',''); accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            self.send_response(101,'Switching Protocols');self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers()
            sock=self.connection;sock.settimeout(.05); subs={}; conn_id=secrets.token_urlsafe(12); pc=db();pc.execute('INSERT INTO presence(conn_id,user_id,node_id,last_seen) VALUES(?,?,?,?)',(conn_id,uu['id'],NODE,time.time()));pc.commit();pc.close()
            def send(x): sock.sendall(ws_frame(x))
            def recv():
                try:
                    h=sock.recv(2)
                    if not h:return {'__closed__':True}
                    if len(h)<2:return None
                    op=h[0]&15
                    n=h[1]&127
                    if n==126:n=struct.unpack('>H',sock.recv(2))[0]
                    elif n==127:n=struct.unpack('>Q',sock.recv(8))[0]
                    mask=sock.recv(4);b=b''
                    while len(b)<n:b+=sock.recv(n-len(b))
                    if op==8:return {'__closed__':True}
                    if op==9:
                        sock.sendall(b'\\x8a'+bytes([n])+b);return None
                    return json.loads(bytes(v^mask[i%4] for i,v in enumerate(b)))
                except socket.timeout: return None
                except: return {'__closed__':True}
            end=time.time()+0.15
            while True:
                msg=recv()
                if msg and msg.get('__closed__'):
                    pc=db();pc.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,));pc.commit();pc.close();return
                if msg:
                    typ=msg.get('type'); ch=int(msg.get('channel_id',0))
                    c=db(); head=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(ch,)).fetchone()
                    if typ=='subscribe':
                        h=head['last_event_id'] if head else 0;subs[ch]=h;send({'type':'subscribed','channel_id':ch,'head_event_id':h})
                    elif typ=='resume':
                        since=int(msg.get('since_event_id',0)); rows=c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since)).fetchall();subs[ch]=since
                        for r in rows:
                            z=json.loads(r['payload_json']);z.update({'type':r['type'],'event_id':r['event_id'],'channel_id':ch});send(z);subs[ch]=r['event_id']
                        h=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(ch,)).fetchone();send({'type':'resumed','channel_id':ch,'head_event_id':h['last_event_id'] if h else 0})
                    c.close()
                pc=db();pc.execute('UPDATE presence SET last_seen=? WHERE conn_id=?',(time.time(),conn_id));pc.commit();pc.close()
                c=db()
                for ch,last in list(subs.items()):
                    for r in c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,last)).fetchall():
                        z=json.loads(r['payload_json']);z.update({'type':r['type'],'event_id':r['event_id'],'channel_id':ch});send(z);subs[ch]=r['event_id']
                c.close();time.sleep(.05)
        if p=='/': return self.html()
        u=self.require()
        if not u:return
        c=db()
        if p=='/api/auth/me': self.sendj(200,{'user':user_obj(u)}); c.close(); return
        fm=re.fullmatch(r'/api/files/(\d+)(/download)?',p)
        if fm:
            r=c.execute('SELECT * FROM files WHERE id=?',(int(fm.group(1)),)).fetchone()
            if not r:c.close();return self.sendj(404,{'error':'not found'})
            if fm.group(2):
                data=open(r['path'],'rb').read();self.send_response(200);self.send_header('Content-Type',r['content_type']);self.send_header('Content-Disposition','attachment; filename="'+r['filename']+'"');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);c.close();return
            self.sendj(200,{'file':fobj(r)});c.close();return
        if p=='/api/workspaces':
            rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); self.sendj(200,{'workspaces':[ws_obj(x) for x in rows]}); c.close(); return
        gm=re.fullmatch(r'/api/workspaces/([^/]+)/groups',p)
        if gm:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(gm.group(1),)).fetchone()
            if not w:c.close();return self.sendj(404,{'error':'not found'})
            if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone():c.close();return self.sendj(403,{'error':'forbidden'})
            rs=c.execute('SELECT * FROM groups WHERE workspace_id=?',(w['id'],)).fetchall()
            out=[]
            for g in rs:out.append({'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':[x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],)).fetchall()]})
            self.sendj(200,{'groups':out});c.close();return
        m=re.fullmatch(r'/api/workspaces/([^/]+)/invitations',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:c.close();return self.sendj(404,{'error':'not found'})
            role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            if not role or role['role'] not in ('owner','admin'):c.close();return self.sendj(403,{'error':'forbidden'})
            t=datetime.datetime.now(datetime.timezone.utc).isoformat()
            rs=c.execute("SELECT * FROM invitations WHERE workspace_id=? AND COALESCE(revoked,0)=0 AND uses<max_uses AND (expires_at IS NULL OR expires_at>?)",(w['id'],t)).fetchall()
            self.sendj(200,{'invitations':[dict(x) for x in rs]});c.close();return
        m=re.fullmatch(r'/api/workspaces/([^/]+)',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:return self.sendj(404,{'error':'not found'})
            include_archived=urllib.parse.parse_qs(q).get('include_archived',['0'])[0] in ('1','true'); cs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (? OR is_archived=0) AND (is_private=0 OR id IN (SELECT channel_id FROM channel_members WHERE user_id=?))',(w['id'],int(include_archived),u['id'])).fetchall()
            self.sendj(200,{'workspace':ws_obj(w),'channels':[ch_obj(x) for x in cs],'read_state':{}}); c.close(); return
        m=re.fullmatch(r'/api/workspaces/([^/]+)/members',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:return self.sendj(404,{'error':'not found'})
            rs=c.execute('SELECT m.*,u.username,u.display_name FROM members m JOIN users u ON u.id=m.user_id WHERE workspace_id=?',(w['id'],)).fetchall()
            self.sendj(200,{'members':[dict(x) for x in rs]}); c.close(); return
        m=re.fullmatch(r'/api/messages/(\d+)/replies',p)
        if m:
            qs=urllib.parse.parse_qs(q)
            try: lim=int(qs.get('limit',['50'])[0])
            except: lim=0
            if lim<1 or lim>200:c.close();return self.sendj(400,{'error':'invalid'})
            cur=qs.get('cursor',[None])[0];after=0
            if cur:
                try:after=int(base64.urlsafe_b64decode(cur+'===').decode())
                except:c.close();return self.sendj(400,{'error':'cursor'})
            sql='SELECT m.*,u.username,u.display_name FROM messages m JOIN users u ON u.id=m.author_id WHERE m.parent_id=? AND m.deleted=0';args=[int(m.group(1))]
            if after:sql+=' AND m.id>?';args.append(after)
            sql+=' ORDER BY m.id ASC LIMIT ?';args.append(lim+1);rs=c.execute(sql,args).fetchall();more=len(rs)>lim;rs=rs[:lim];nxt=base64.urlsafe_b64encode(str(rs[-1]['id']).encode()).decode().rstrip('=') if more and rs else None
            self.sendj(200,{'replies':[self.msg(x,c) for x in rs],'next_cursor':nxt});c.close();return
        m=re.fullmatch(r'/api/channels/(\d+)/pins',p)
        if m:
            ch=c.execute('SELECT * FROM channels WHERE id=?',(int(m.group(1)),)).fetchone()
            if not ch:c.close();return self.sendj(404,{'error':'not found'})
            if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone():c.close();return self.sendj(403,{'error':'forbidden'})
            qs=urllib.parse.parse_qs(q)
            try:lim=int(qs.get('limit',['50'])[0])
            except:lim=0
            if lim<1 or lim>200:c.close();return self.sendj(400,{'error':'invalid'})
            sql='SELECT p.*,m.*,u.username,u.display_name FROM pins p JOIN messages m ON m.id=p.message_id JOIN users u ON u.id=m.author_id WHERE m.channel_id=? ORDER BY p.message_id DESC LIMIT ?'
            rs=c.execute(sql,(ch['id'],lim+1)).fetchall();more=len(rs)>lim;rs=rs[:lim];nxt=base64.urlsafe_b64encode(str(rs[-1]['message_id']).encode()).decode().rstrip('=') if more and rs else None
            self.sendj(200,{'pins':[{'message':self.msg(x,c),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rs],'next_cursor':nxt});c.close();return
        if p=='/api/search':
            qs=urllib.parse.parse_qs(q);term=qs.get('q',[''])[0];wid=qs.get('workspace_id',[None])[0]
            sql='SELECT m.*,u.username,u.display_name FROM messages m JOIN users u ON u.id=m.author_id JOIN channels ch ON ch.id=m.channel_id JOIN channel_members cm ON cm.channel_id=ch.id AND cm.user_id=? WHERE m.deleted=0 AND m.body LIKE ?';args=[u['id'],'%'+term+'%']
            if wid:sql+=' AND ch.workspace_id=?';args.append(wid)
            sql+=' ORDER BY m.id DESC LIMIT 200';rs=c.execute(sql,args).fetchall();self.sendj(200,{'results':[self.msg(x,c) for x in rs],'next_cursor':None});c.close();return
        m=re.fullmatch(r'/api/channels/(\d+)/read',p)
        if m:
            ch=c.execute('SELECT * FROM channels WHERE id=?',(int(m.group(1)),)).fetchone()
            if not ch:c.close();return self.sendj(404,{'error':'not found'})
            c.execute('INSERT OR IGNORE INTO read_states VALUES(?,?,0)',(u['id'],ch['id']))
            st=c.execute('SELECT last_read_event_id FROM read_states WHERE user_id=? AND channel_id=?',(u['id'],ch['id'])).fetchone()['last_read_event_id']
            rows=c.execute('SELECT m.*,u.username,u.display_name FROM messages m JOIN users u ON u.id=m.author_id WHERE m.channel_id=? AND m.deleted=0 AND m.event_id>? AND m.author_id!=?',(ch['id'],st,u['id'])).fetchall()
            mention=sum(1 for r in rows if u['id'] in json.loads(r['mentions_json'] or '[]'))
            self.sendj(200,{'read_state':{'channel_id':ch['id'],'last_read_event_id':st,'unread_count':len(rows),'mention_count':mention}});c.commit();c.close();return
        m=re.fullmatch(r'/api/channels/(\d+)/members',p)
        if m:
            ch=c.execute('SELECT * FROM channels WHERE id=?',(int(m.group(1)),)).fetchone()
            if not ch:c.close();return self.sendj(404,{'error':'not found'})
            rs=c.execute('SELECT u.* FROM channel_members cm JOIN users u ON u.id=cm.user_id WHERE cm.channel_id=?',(ch['id'],)).fetchall()
            self.sendj(200,{'members':[user_obj(x) for x in rs]});c.close();return
        m=re.fullmatch(r'/api/channels/(\d+)/messages',p)
        if m:
            ch=c.execute('SELECT * FROM channels WHERE id=?',(int(m.group(1)),)).fetchone()
            if not ch:c.close();return self.sendj(404,{'error':'not found'})
            if ch['is_dm'] and not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone():c.close();return self.sendj(403,{'error':'forbidden'})
            qs=urllib.parse.parse_qs(q)
            try: lim=int(qs.get('limit',['50'])[0])
            except: lim=0
            if lim<1 or lim>200 or 'page' in qs or 'offset' in qs:c.close();return self.sendj(400,{'error':'invalid'})
            cur=qs.get('cursor',[None])[0]; before=None
            if cur:
                try: before=int(base64.urlsafe_b64decode(cur+'===').decode())
                except: c.close();return self.sendj(400,{'error':'cursor'})
            sql='SELECT m.*,u.username,u.display_name FROM messages m JOIN users u ON u.id=m.author_id WHERE m.channel_id=? AND m.deleted=0';args=[ch['id']]
            if before is not None:sql+=' AND m.id<?';args.append(before)
            sql+=' ORDER BY m.id DESC LIMIT ?';args.append(lim+1);rs=c.execute(sql,args).fetchall()
            more=len(rs)>lim;rs=rs[:lim]; nxt=base64.urlsafe_b64encode(str(rs[-1]['id']).encode()).decode().rstrip('=') if more and rs else None
            self.sendj(200,{'messages':[self.msg(x,c) for x in rs],'next_cursor':nxt});c.close();return
        c.close(); self.sendj(404,{'error':'not found'})
    def do_PATCH(self):
        p,q=self.request_path(); u=self.require()
        if not u:return
        c=db(); d=self.body()
        gm=re.fullmatch(r'/api/workspaces/([^/]+)/groups/([^/]+)',p)
        if gm:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(gm.group(1),)).fetchone()
            if not w:c.close();return self.sendj(404,{'error':'not found'})
            g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],gm.group(2))).fetchone()
            if not g:c.close();return self.sendj(404,{'error':'not found'})
            role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            if g['creator_id']!=u['id'] and (not role or role['role']!='owner'):c.close();return self.sendj(403,{'error':'forbidden'})
            name=d.get('name',g['name']);ids=d.get('user_ids')
            c.execute('UPDATE groups SET name=? WHERE id=?',(name,g['id']))
            if ids is not None:
                c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],));c.executemany('INSERT INTO group_members VALUES(?,?)',[(g['id'],x) for x in ids])
            c.commit();out={'id':g['id'],'handle':g['handle'],'name':name,'member_user_ids':[x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],)).fetchall()]};c.close();return self.sendj(200,{'group':out})
        wm=re.fullmatch(r'/api/workspaces/([^/]+)',p)
        if wm:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(wm.group(1),)).fetchone()
            if not w:c.close();return self.sendj(404,{'error':'not found'})
            role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            if not role or role['role'] not in ('owner','admin'):c.close();return self.sendj(403,{'error':'forbidden'})
            if not isinstance(d,dict):c.close();return self.sendj(400,{'error':'invalid'})
            if 'join_mode' in d and d['join_mode'] not in ('open','invite_only'):c.close();return self.sendj(400,{'error':'invalid'})
            name=d.get('name',w['name'])
            if 'name' in d and not str(name).strip():c.close();return self.sendj(400,{'error':'invalid'})
            mode=d.get('join_mode',w['join_mode']);c.execute('UPDATE workspaces SET name=?,join_mode=? WHERE id=?',(name,mode,w['id']));c.commit()
            out={'id':w['id'],'slug':w['slug'],'name':name,'owner_id':w['owner_id'],'join_mode':mode};c.close();return self.sendj(200,{'workspace':out})
        cm=re.fullmatch(r'/api/channels/(\d+)',p)
        if cm:
            ch=c.execute('SELECT * FROM channels WHERE id=?',(int(cm.group(1)),)).fetchone()
            if not ch:c.close();return self.sendj(404,{'error':'not found'})
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
            role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            joined=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
            if not joined and (not role or role['role']!='owner'):c.close();return self.sendj(403,{'error':'forbidden'})
            if not isinstance(d,dict) or 'topic' not in d or not isinstance(d['topic'],str) or len(d['topic'])>250:c.close();return self.sendj(400,{'error':'invalid'})
            c.execute('UPDATE channels SET topic=? WHERE id=?',(d['topic'],ch['id']));c.commit();out=ch_obj(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone());c.close();return self.sendj(200,{'channel':out})
        mm=re.fullmatch(r'/api/messages/(\d+)',p)
        if mm:
            r=c.execute('SELECT m.*,ch.is_archived FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.id=?',(int(mm.group(1)),)).fetchone()
            if not r:c.close();return self.sendj(404,{'error':'not found'})
            if r['is_archived']:c.close();return self.sendj(423,{'error':'archived'})
            if r['author_id']!=u['id']:c.close();return self.sendj(403,{'error':'forbidden'})
            if not isinstance(d,dict) or not str(d.get('body','')).strip():c.close();return self.sendj(400,{'error':'invalid'})
            c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(d['body'],now(),r['id']));eid=bump(c,r['channel_id'],'message.updated',{'message_id':r['id'],'body':d['body']});c.commit()
            z=c.execute('SELECT m.*,u.username,u.display_name FROM messages m JOIN users u ON u.id=m.author_id WHERE m.id=?',(r['id'],)).fetchone();self.sendj(200,{'message':self.msg(z,c)});c.close();return
        m=re.fullmatch(r'/api/workspaces/([^/]+)/members/(\d+)',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:c.close();return self.sendj(404,{'error':'not found'})
            caller=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            if not caller or caller['role'] not in ('owner','admin'):c.close();return self.sendj(403,{'error':'forbidden'})
            target=c.execute('SELECT m.*,u.username,u.display_name FROM members m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? AND m.user_id=?',(w['id'],int(m.group(2)))).fetchone()
            if not target:c.close();return self.sendj(404,{'error':'not found'})
            if target['role']=='owner' or (caller['role']=='admin' and target['role']=='admin'):c.close();return self.sendj(403,{'error':'forbidden'})
            role=(d or {}).get('role')
            if role=='owner' or role not in ('admin','member','guest'):c.close();return self.sendj(400,{'error':'invalid'})
            c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(role,w['id'],target['user_id']));c.commit()
            out=dict(target);out['role']=role;c.close();return self.sendj(200,{'member':out})
        c.close();return self.sendj(404,{'error':'not found'})
        m=re.fullmatch(r'/api/messages/(\d+)/pin',p)
        if m:
            r=c.execute('SELECT m.*,ch.is_archived FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.id=?',(int(m.group(1)),)).fetchone()
            if not r:c.close();return self.sendj(404,{'error':'not found'})
            if r['is_archived']:c.close();return self.sendj(423,{'error':'archived'})
            t=now();c.execute('INSERT OR IGNORE INTO pins(message_id,pinned_by,pinned_at) VALUES(?,?,?)',(r['id'],u['id'],t));c.commit();z=c.execute('SELECT * FROM pins WHERE message_id=?',(r['id'],)).fetchone();c.close();return self.sendj(200,{'pin':dict(z)})
    def do_DELETE(self):
        p,q=self.request_path(); u=self.require()
        if not u:return
        c=db()
        gm=re.fullmatch(r'/api/workspaces/([^/]+)/groups/([^/]+)',p)
        if gm:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(gm.group(1),)).fetchone()
            if not w:c.close();return self.sendj(404,{'error':'not found'})
            g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],gm.group(2))).fetchone()
            if not g:c.close();return self.sendj(200,{'deleted':True})
            role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            if g['creator_id']!=u['id'] and (not role or role['role']!='owner'):c.close();return self.sendj(403,{'error':'forbidden'})
            c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],));c.execute('DELETE FROM groups WHERE id=?',(g['id'],));c.commit();c.close();return self.sendj(200,{'deleted':True})
        dm=re.fullmatch(r'/api/messages/(\d+)',p)
        if dm:
            r=c.execute('SELECT m.*,ch.is_archived,w.owner_id FROM messages m JOIN channels ch ON ch.id=m.channel_id JOIN workspaces w ON w.id=ch.workspace_id WHERE m.id=?',(int(dm.group(1)),)).fetchone()
            if not r:c.close();return self.sendj(404,{'error':'not found'})
            if r['is_archived']:c.close();return self.sendj(423,{'error':'archived'})
            if r['author_id']!=u['id'] and r['owner_id']!=u['id']:c.close();return self.sendj(403,{'error':'forbidden'})
            c.execute('UPDATE messages SET deleted=1 WHERE id=?',(r['id'],));bump(c,r['channel_id'],'message.deleted',{'message_id':r['id']});c.commit();c.close();return self.sendj(200,{'deleted':True})
        gm=re.fullmatch(r'/api/workspaces/([^/]+)/groups',p)
        if gm:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(gm.group(1),)).fetchone()
            if not w:c.close();return self.sendj(404,{'error':'not found'})
            if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone():c.close();return self.sendj(403,{'error':'forbidden'})
            h=(d or {}).get('handle');name=(d or {}).get('name'); ids=(d or {}).get('user_ids',[])
            if not isinstance(h,str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,31}',h) or not isinstance(name,str) or not name.strip() or not isinstance(ids,list):c.close();return self.sendj(400,{'error':'invalid'})
            try:
                cur=c.execute('INSERT INTO groups(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],h,name,u['id']));gid=cur.lastrowid;c.executemany('INSERT OR IGNORE INTO group_members VALUES(?,?)',[(gid,x) for x in ids]);c.commit()
            except sqlite3.IntegrityError:c.close();return self.sendj(409,{'error':'duplicate'})
            g={'id':gid,'handle':h,'name':name,'member_user_ids':[x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(gid,)).fetchall()]};c.close();return self.sendj(201,{'group':g})
        dm=re.fullmatch(r'/api/dms',p)
        if dm:
            if not isinstance(d,dict) or 'user_id' not in d:c.close();return self.sendj(400,{'error':'invalid'})
            other=c.execute('SELECT id FROM users WHERE id=?',(d['user_id'],)).fetchone()
            if not other:c.close();return self.sendj(404,{'error':'not found'})
            if other['id']==u['id']:c.close();return self.sendj(400,{'error':'invalid'})
            a,b=sorted((u['id'],other['id'])); z=c.execute('SELECT channel_id FROM dm_pairs WHERE user_a=? AND user_b=?',(a,b)).fetchone()
            if z:c.close();return self.sendj(200,{'channel_id':z['channel_id']})
            c.commit(); c.execute('BEGIN IMMEDIATE');z=c.execute('SELECT channel_id FROM dm_pairs WHERE user_a=? AND user_b=?',(a,b)).fetchone()
            if not z:
                c.execute("INSERT INTO channels(workspace_id,name,is_private,is_dm) VALUES(NULL,?,?,1)",(f'dm-{a}-{b}',1));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO dm_pairs VALUES(?,?,?)',(a,b,cid));c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,a));c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,b));c.execute('INSERT INTO channel_seq VALUES(?,0)',(cid))
            else:cid=z['channel_id']
            c.commit();c.close();return self.sendj(200,{'channel_id':cid})
        rm=re.fullmatch(r'/api/messages/(\d+)/reactions',p)
        if rm:
            r=c.execute('SELECT m.*,ch.is_archived,ch.is_private FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.id=?',(int(rm.group(1)),)).fetchone()
            if not r:c.close();return self.sendj(404,{'error':'not found'})
            if r['is_archived']:c.close();return self.sendj(423,{'error':'archived'})
            if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(r['channel_id'],u['id'])).fetchone():c.close();return self.sendj(403,{'error':'forbidden'})
            emoji=(d or {}).get('emoji')
            if not isinstance(emoji,str) or not emoji:c.close();return self.sendj(400,{'error':'invalid'})
            exists=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(r['id'],u['id'],emoji)).fetchone()
            if not exists:
                c.execute('INSERT INTO reactions VALUES(?,?,?)',(r['id'],u['id'],emoji));bump(c,r['channel_id'],'reaction.added',{'message_id':r['id'],'emoji':emoji,'user_id':u['id']});c.commit()
            z=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY emoji,user_id',(r['id'],)).fetchall();g={}
            for x in z:g.setdefault(x['emoji'],[]).append(x['user_id'])
            c.close();return self.sendj(200,{'reactions':[{'emoji':e,'count':len(v),'user_ids':v} for e,v in g.items()]})
        rm=re.fullmatch(r'/api/messages/(\d+)/reactions/(.+)',p)
        if rm:
            r=c.execute('SELECT m.*,ch.is_archived FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.id=?',(int(rm.group(1)),)).fetchone()
            if not r:c.close();return self.sendj(404,{'error':'not found'})
            if r['is_archived']:c.close();return self.sendj(423,{'error':'archived'})
            if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(r['channel_id'],u['id'])).fetchone():c.close();return self.sendj(403,{'error':'forbidden'})
            emoji=urllib.parse.unquote(rm.group(2)); old=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(r['id'],u['id'],emoji)).fetchone()
            if old:c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(r['id'],u['id'],emoji));bump(c,r['channel_id'],'reaction.removed',{'message_id':r['id'],'emoji':emoji,'user_id':u['id']});c.commit()
            z=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY emoji,user_id',(r['id'],)).fetchall();g={}
            for x in z:g.setdefault(x['emoji'],[]).append(x['user_id'])
            c.close();return self.sendj(200,{'reactions':[{'emoji':e,'count':len(v),'user_ids':v} for e,v in g.items()]})
        pm=re.fullmatch(r'/api/messages/(\d+)/pin',p)
        if pm:
            c.execute('DELETE FROM pins WHERE message_id=?',(int(pm.group(1)),));c.commit();c.close();return self.sendj(200,{'unpinned':True})
        m=re.fullmatch(r'/api/workspaces/([^/]+)/invitations/([^/]+)',p)
        if not m:c.close();return self.sendj(404,{'error':'not found'})
        w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
        if not w:c.close();return self.sendj(404,{'error':'not found'})
        role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
        if not role or role['role'] not in ('owner','admin'):c.close();return self.sendj(403,{'error':'forbidden'})
        inv=c.execute('SELECT code FROM invitations WHERE code=? AND workspace_id=?',(m.group(2),w['id'])).fetchone()
        if not inv:c.close();return self.sendj(404,{'error':'not found'})
        c.execute('UPDATE invitations SET revoked=1 WHERE code=?',(m.group(2),));c.commit();c.close();return self.sendj(200,{'deleted':True})
    def do_POST(self):
        p,q=self.request_path(); u=None
        if p=='/api/auth/register':
            if u:return
            d=self.body()
            if not d or not re.fullmatch(r'[A-Za-z0-9_]+',str(d.get('username',''))) or len(str(d.get('password','')))<8:return self.sendj(400,{'error':'invalid'})
            c=db()
            try:
                cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(d['username'],hashlib.sha256(d['password'].encode()).hexdigest(),d.get('display_name') or d['username'])); uid=cur.lastrowid; tok=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(tok,uid)); c.commit(); r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); self.sendj(201,{'user':user_obj(r),'token':tok})
            except sqlite3.IntegrityError:self.sendj(409,{'error':'duplicate'})
            finally:c.close()
            return
        if p=='/api/auth/login':
            if u:return
            d=self.body(); c=db(); r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(d.get('username',''),hashlib.sha256(str(d.get('password','')).encode()).hexdigest())).fetchone()
            if not r:c.close(); return self.sendj(401,{'error':'invalid'})
            tok=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(tok,r['id'])); c.commit(); self.sendj(200,{'user':user_obj(r),'token':tok}); c.close(); return
        u=self.require()
        if not u:return
        if p=='/api/files':
            item,err=multipart(self)
            if err=='large':return self.sendj(413,{'error':'too large'})
            if err:return self.sendj(400,{'error':'file required'})
            fn,typ,data=item;c=db();cur=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at,path) VALUES(?,?,?,?,?,?)',(u['id'],fn,typ,len(data),now(),''))
            fid=cur.lastrowid;path=f'/app/data/files/{fid}';open(path,'wb').write(data);c.execute('UPDATE files SET path=? WHERE id=?',(path,fid));c.commit();r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone();c.close();return self.sendj(201,{'file':fobj(r)})
        c=db(); d=self.body() or {}
        gm=re.fullmatch(r'/api/workspaces/([^/]+)/groups',p)
        if gm:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(gm.group(1),)).fetchone()
            if not w:c.close();return self.sendj(404,{'error':'not found'})
            if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone():c.close();return self.sendj(403,{'error':'forbidden'})
            h=(d or {}).get('handle');name=(d or {}).get('name');ids=(d or {}).get('user_ids',[])
            if not isinstance(h,str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,31}',h) or not isinstance(name,str) or not name.strip() or not isinstance(ids,list):c.close();return self.sendj(400,{'error':'invalid'})
            try:
                cur=c.execute('INSERT INTO groups(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],h,name,u['id']));gid=cur.lastrowid;c.executemany('INSERT OR IGNORE INTO group_members VALUES(?,?)',[(gid,x) for x in ids]);c.commit()
            except sqlite3.IntegrityError:c.close();return self.sendj(409,{'error':'duplicate'})
            out={'id':gid,'handle':h,'name':name,'member_user_ids':[x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(gid,)).fetchall()]};c.close();return self.sendj(201,{'group':out})
        if p=='/api/workspaces':
            if not re.fullmatch(r'[a-z0-9-]{2,32}',str(d.get('slug',''))) or not str(d.get('name','')).strip(): c.close(); return self.sendj(400,{'error':'invalid'})
            try:
                cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d['name'],u['id'])); wid=cur.lastrowid; c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner')); cc=c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')); cid=cc.lastrowid; c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id'])); c.execute('INSERT INTO channel_seq VALUES(?,0)',(cid,)); c.commit(); w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); self.sendj(201,{'workspace':ws_obj(w),'general_channel':ch_obj(ch)})
            except sqlite3.IntegrityError:self.sendj(409,{'error':'duplicate'})
            finally:c.close()
            return
        dm=re.fullmatch(r'/api/dms',p)
        if dm:
            if not isinstance(d,dict) or 'user_id' not in d:c.close();return self.sendj(400,{'error':'invalid'})
            other=c.execute('SELECT id FROM users WHERE id=?',(d['user_id'],)).fetchone()
            if not other:c.close();return self.sendj(404,{'error':'not found'})
            if other['id']==u['id']:c.close();return self.sendj(400,{'error':'invalid'})
            a,b=sorted((u['id'],other['id']));c.close();cid=None
            for _ in range(25):
                x=db()
                try:
                    x.execute('BEGIN IMMEDIATE')
                    z=x.execute('SELECT channel_id FROM dm_pairs WHERE user_a=? AND user_b=?',(a,b)).fetchone()
                    if z: cid=z['channel_id'];x.commit();x.close();break
                    x.execute('INSERT INTO channels(workspace_id,name,is_private,is_dm) VALUES(NULL,?,?,1)',(f'dm-{a}-{b}',1))
                    cid=x.execute('SELECT last_insert_rowid()').fetchone()[0]
                    x.execute('INSERT INTO dm_pairs(user_a,user_b,channel_id) VALUES(?,?,?)',(a,b,cid))
                    x.execute('INSERT INTO channel_members VALUES(?,?)',(cid,a));x.execute('INSERT INTO channel_members VALUES(?,?)',(cid,b));x.execute('INSERT INTO channel_seq VALUES(?,0)',(cid,))
                    x.commit();x.close();break
                except (sqlite3.OperationalError,sqlite3.IntegrityError):
                    try:x.rollback();x.close()
                    except:pass
                    time.sleep(.1)
            if cid is None:return self.sendj(503,{'error':'busy'})
            return self.sendj(200,{'channel_id':cid})
        rm=re.fullmatch(r'/api/messages/(\d+)/reactions',p)
        if rm:
            r=c.execute('SELECT m.*,ch.is_archived FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.id=?',(int(rm.group(1)),)).fetchone()
            if not r:c.close();return self.sendj(404,{'error':'not found'})
            if r['is_archived']:c.close();return self.sendj(423,{'error':'archived'})
            if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(r['channel_id'],u['id'])).fetchone():c.close();return self.sendj(403,{'error':'forbidden'})
            emoji=(d or {}).get('emoji')
            if not isinstance(emoji,str) or not emoji:c.close();return self.sendj(400,{'error':'invalid'})
            if not c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(r['id'],u['id'],emoji)).fetchone():
                c.execute('INSERT INTO reactions VALUES(?,?,?)',(r['id'],u['id'],emoji));bump(c,r['channel_id'],'reaction.added',{'message_id':r['id'],'emoji':emoji,'user_id':u['id']});c.commit()
            z=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY emoji,user_id',(r['id'],)).fetchall();g={}
            for x in z:g.setdefault(x['emoji'],[]).append(x['user_id'])
            c.close();return self.sendj(200,{'reactions':[{'emoji':e,'count':len(v),'user_ids':v} for e,v in g.items()]})
        pm=re.fullmatch(r'/api/messages/(\d+)/pin',p)
        if pm:
            r=c.execute('SELECT m.*,ch.is_archived FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.id=?',(int(pm.group(1)),)).fetchone()
            if not r:c.close();return self.sendj(404,{'error':'not found'})
            if r['is_archived']:c.close();return self.sendj(423,{'error':'archived'})
            t=now();c.execute('INSERT OR IGNORE INTO pins(message_id,pinned_by,pinned_at) VALUES(?,?,?)',(r['id'],u['id'],t));c.commit();z=c.execute('SELECT * FROM pins WHERE message_id=?',(r['id'],)).fetchone();c.close();return self.sendj(200,{'pin':dict(z)})
        cm=re.fullmatch(r'/api/channels/(\d+)/(leave|archive|unarchive)',p)
        if cm:
            ch=c.execute('SELECT * FROM channels WHERE id=?',(int(cm.group(1)),)).fetchone()
            if not ch:c.close();return self.sendj(404,{'error':'not found'})
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
            if cm.group(2)=='leave':
                c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id']));bump(c,ch['id'],'member.left',{'user_id':u['id']});c.commit();out=ch_obj(ch);c.close();return self.sendj(200,{'channel':out})
            if w['owner_id']!=u['id']:c.close();return self.sendj(403,{'error':'forbidden'})
            val=1 if cm.group(2)=='archive' else 0;c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id']));bump(c,ch['id'],'channel.archived' if val else 'channel.unarchived',{'channel':ch_obj(ch)});c.commit();out=ch_obj(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone());c.close();return self.sendj(200,{'channel':out})
        m=re.fullmatch(r'/api/channels/(\d+)/read',p)
        if m:
            ch=c.execute('SELECT * FROM channels WHERE id=?',(int(m.group(1)),)).fetchone()
            if not ch:c.close();return self.sendj(404,{'error':'not found'})
            try: val=int(d.get('last_read_event_id'))
            except: c.close();return self.sendj(400,{'error':'invalid'})
            c.execute('INSERT OR IGNORE INTO read_states VALUES(?,?,0)',(u['id'],ch['id']))
            c.execute('UPDATE read_states SET last_read_event_id=CASE WHEN last_read_event_id<? THEN ? ELSE last_read_event_id END WHERE user_id=? AND channel_id=?',(val,val,u['id'],ch['id']))
            st=c.execute('SELECT last_read_event_id FROM read_states WHERE user_id=? AND channel_id=?',(u['id'],ch['id'])).fetchone()['last_read_event_id'];c.commit();c.close();return self.sendj(200,{'read_state':{'channel_id':ch['id'],'last_read_event_id':st,'unread_count':0,'mention_count':0}})
        m=re.fullmatch(r'/api/channels/(\d+)/join',p)
        if m:
            ch=c.execute('SELECT * FROM channels WHERE id=?',(int(m.group(1)),)).fetchone()
            if not ch:c.close(); return self.sendj(404,{'error':'not found'})
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
            mem=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            already=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
            if already:c.close();return self.sendj(200,{'channel':ch_obj(ch)})
            if mem and mem['role']=='guest':c.close();return self.sendj(403,{'error':'forbidden'})
            if ch['is_private']:c.close();return self.sendj(403,{'error':'forbidden'})
            if not mem and w['join_mode']!='open':c.close();return self.sendj(403,{'error':'forbidden'})
            c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w['id'],u['id'],'member')); c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id'])); bump(c,ch['id'],'member.joined',{'user_id':u['id']}); c.commit(); c.close(); return self.sendj(200,{'channel':ch_obj(ch)})
        m=re.fullmatch(r'/api/workspaces/([^/]+)/invitations',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:c.close();return self.sendj(404,{'error':'not found'})
            role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            if not role or role['role'] not in ('owner','admin'):c.close();return self.sendj(403,{'error':'forbidden'})
            if not isinstance(d,dict):c.close();return self.sendj(400,{'error':'invalid'})
            try: mx=int(d.get('max_uses',1))
            except: c.close();return self.sendj(400,{'error':'invalid'})
            if mx<1:c.close();return self.sendj(400,{'error':'invalid'})
            target=d.get('invited_user_id'); username=d.get('invited_username')
            if username:
                rr=c.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()
                if rr: target=rr['id']
            if target is not None: mx=1
            exp=None
            if 'expires_in_seconds' in d:
                try: exp=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=int(d['expires_in_seconds']))).isoformat()
                except: c.close();return self.sendj(400,{'error':'invalid'})
            code=secrets.token_urlsafe(18)
            c.execute('INSERT INTO invitations(code,workspace_id,invited_user_id,invited_username,created_by,max_uses,expires_at,revoked) VALUES(?,?,?,?,?,?,?,0)',(code,w['id'],target,username,u['id'],mx,exp));c.commit()
            inv=dict(c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone());c.close();return self.sendj(201,{'invitation':inv})
        m=re.fullmatch(r'/api/invitations/([^/]+)/accept',p)
        if m:
            inv=c.execute('SELECT * FROM invitations WHERE code=?',(m.group(1),)).fetchone()
            if not inv:c.close();return self.sendj(404,{'error':'not found'})
            if (inv['revoked'] or inv['uses']>=inv['max_uses'] or (inv['expires_at'] and inv['expires_at']<=datetime.datetime.now(datetime.timezone.utc).isoformat())):c.close();return self.sendj(404,{'error':'not found'})
            if inv['invited_user_id'] is not None and inv['invited_user_id']!=u['id']:c.close();return self.sendj(403,{'error':'forbidden'})
            if inv['invited_username'] is not None and inv['invited_username']!=u['username']:c.close();return self.sendj(403,{'error':'forbidden'})
            c.execute('BEGIN IMMEDIATE'); z=c.execute('SELECT * FROM invitations WHERE code=?',(m.group(1),)).fetchone()
            if z['revoked'] or z['uses']>=z['max_uses']:c.rollback();c.close();return self.sendj(404,{'error':'not found'})
            c.execute('UPDATE invitations SET uses=uses+1 WHERE code=? AND uses<?',(m.group(1),z['max_uses']));c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(z['workspace_id'],u['id'],'member'));c.commit();c.close();return self.sendj(200,{'workspace_id':z['workspace_id']})
        m=re.fullmatch(r'/api/workspaces/([^/]+)/transfer_ownership',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:c.close();return self.sendj(404,{'error':'not found'})
            cur=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            if not cur or cur['role']!='owner':c.close();return self.sendj(403,{'error':'forbidden'})
            target_id=(d or {}).get('new_owner_id'); target=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],target_id)).fetchone()
            if not target or target['role']!='admin':c.close();return self.sendj(400,{'error':'invalid'})
            c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],u['id']))
            c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],target_id));c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target_id,w['id']));c.commit();c.close();return self.sendj(200,{'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())}) if False else self.sendj(200,{'workspace':{'id':w['id'],'slug':w['slug'],'name':w['name'],'owner_id':target_id,'join_mode':w['join_mode']}})
        m=re.fullmatch(r'/api/workspaces/([^/]+)/channels',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:c.close(); return self.sendj(404,{'error':'not found'})
            member=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
            if not member:c.close();return self.sendj(403,{'error':'forbidden'})
            if member['role']=='guest' or (bool(d.get('is_private')) and member['role'] not in ('owner','admin')):c.close();return self.sendj(403,{'error':'forbidden'})
            name=str(d.get('name','')); topic=d.get('topic','')
            if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],name)).fetchone():c.close();return self.sendj(409,{'error':'duplicate'})
            if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',name) or len(name)>32 or not isinstance(topic,str) or len(topic)>250:c.close();return self.sendj(400,{'error':'invalid'})
            try:
                cc=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],d.get('name',''),int(bool(d.get('is_private'))),d.get('topic',''))); cid=cc.lastrowid; c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id'])); c.execute('INSERT INTO channel_seq VALUES(?,0)',(cid,)); c.commit(); chrow=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); self.sendj(201,{'channel':ch_obj(chrow)})
            except sqlite3.IntegrityError:self.sendj(409,{'error':'duplicate'})
            finally:c.close()
            return
        m=re.fullmatch(r'/api/channels/(\d+)/messages',p)
        if m:
            ch=c.execute('SELECT * FROM channels WHERE id=?',(int(m.group(1)),)).fetchone()
            if not ch:c.close();return self.sendj(404,{'error':'not found'})
            if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone():c.close();return self.sendj(403,{'error':'forbidden'})
            body=str(d.get('body',''))
            if body.startswith('/') and not body.startswith('//'):
                parts=body.split(None,1); cmd=parts[0][1:]; rest=parts[1].strip() if len(parts)>1 else ''
                if cmd=='me':
                    if not rest:c.close();return self.sendj(400,{'error':'invalid'})
                elif cmd=='shrug':
                    body=(rest+' ' if rest else '')+'¯\\_(ツ)_/¯'
                elif cmd in ('topic','archive','unarchive','invite'):
                    w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); owner=w['owner_id']==u['id']
                    if cmd in ('archive','unarchive','topic') and not owner and not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone():c.close();return self.sendj(403,{'error':'forbidden'})
                    if cmd=='topic':
                        if len(rest)>250:c.close();return self.sendj(400,{'error':'invalid'})
                        c.execute('UPDATE channels SET topic=? WHERE id=?',(rest,ch['id']));bump(c,ch['id'],'channel.updated',{'topic':rest});c.commit();out=ch_obj(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone());c.close();return self.sendj(201,{'message':None,'channel':out})
                    if cmd in ('archive','unarchive'):
                        if not owner:c.close();return self.sendj(403,{'error':'forbidden'})
                        val=1 if cmd=='archive' else 0;c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id']));bump(c,ch['id'],'channel.archived' if val else 'channel.unarchived',{'channel_id':ch['id']});c.commit();out=ch_obj(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone());c.close();return self.sendj(201,{'message':None,'channel':out})
                    if not rest.startswith('@'):c.close();return self.sendj(404,{'error':'not found'})
                    name=rest[1:]; target=c.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone()
                    if not target:c.close();return self.sendj(404,{'error':'not found'})
                    if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],target['id'])).fetchone():c.close();return self.sendj(404,{'error':'not found'})
                    c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],target['id']));bump(c,ch['id'],'member.joined',{'user_id':target['id']});c.commit();c.close();return self.sendj(201,{'message':None,'invited_user':user_obj(target)})
                else:
                    c.close();return self.sendj(400,{'error':'unknown command'})
            if ch['is_archived']:c.close();return self.sendj(423,{'error':'archived'})
            if not str(d.get('body','')).strip():c.close();return self.sendj(400,{'error':'empty'})
            fids=d.get('file_ids',[])
            if not isinstance(fids,list):c.close();return self.sendj(400,{'error':'invalid'})
            for fid in fids:
                fr=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
                if not fr or fr['uploader_id']!=u['id'] or c.execute('SELECT 1 FROM message_files mf JOIN messages m ON m.id=mf.message_id WHERE mf.file_id=?',(fid,)).fetchone():c.close();return self.sendj(400,{'error':'invalid file'})

            d['body']=body
            cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch['id'],u['id'],d['body'],d.get('parent_id'),now())); mid=cur.lastrowid
            ev=bump(c,ch['id'],'message.reply' if d.get('parent_id') else 'message.created',{'message_id':mid,'body':d['body']})
            members=c.execute('SELECT u.id,u.username FROM users u JOIN members wm ON wm.user_id=u.id AND wm.workspace_id=? JOIN channel_members cm ON cm.user_id=u.id AND cm.channel_id=?',(ch['workspace_id'],ch['id'])).fetchall()
            ids=[]; body=d['body']
            if re.search(r'(?<![\w@])@channel\b',body): ids += [x['id'] for x in members if x['id']!=u['id']]
            for x in members:
                if x['id']!=u['id'] and re.search(r'(?<![\w@])@'+re.escape(x['username'])+r'\b',body): ids.append(x['id'])
            if re.search(r'(?<![\w@])@here\b',body):
                ids += [x['user_id'] for x in c.execute('SELECT cm.user_id FROM channel_members cm JOIN presence p ON p.user_id=cm.user_id WHERE cm.channel_id=? AND p.last_seen>? AND cm.user_id!=?',(ch['id'],time.time()-1.0,u['id'])).fetchall()]
            for g in c.execute('SELECT * FROM groups WHERE workspace_id=?',(ch['workspace_id'],)).fetchall():
                if re.search(r'(?<![\w@])@'+re.escape(g['handle'])+r'\b',body):
                    ids += [x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=? AND user_id IN (SELECT user_id FROM channel_members WHERE channel_id=?)',(g['id'],ch['id'])).fetchall() if x['user_id']!=u['id']]
            ids=list(dict.fromkeys(ids)); c.execute('UPDATE messages SET event_id=?,mentions_json=? WHERE id=?',(ev,json.dumps(ids),mid)); c.executemany('INSERT INTO message_files VALUES(?,?)',[(mid,x) for x in fids]);c.commit();r=c.execute('SELECT m.*,u.username,u.display_name FROM messages m JOIN users u ON u.id=m.author_id WHERE m.id=?',(mid,)).fetchone();self.sendj(201,{'message':self.msg(r,c)});c.close();return
        c.close();self.sendj(404,{'error':'not found'})
    def msg(self,r,c):
        x=dict(r); x['author']={'id':r['author_id'],'username':r['username'],'display_name':r['display_name']};x['reply_count']=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0];x['files']=[fobj(z) for z in c.execute('SELECT * FROM files WHERE id IN (SELECT file_id FROM message_files WHERE message_id=?)',(r['id'],)).fetchall()] if 'message_files' in [z['name'] for z in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()] else []; rr=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY emoji,user_id',(r['id'],)).fetchall(); groups={}; [groups.setdefault(z['emoji'],[]).append(z['user_id']) for z in rr]; x['reactions']=[{'emoji':e,'count':len(ids),'user_ids':ids} for e,ids in groups.items()];x['mentions']=json.loads(r['mentions_json'] or '[]') if 'mentions_json' in r.keys() else [];return x
    def html(self):
        b='''<!doctype html><html><head><meta charset=utf-8><title>Huddle</title><style>body{font:16px Arial;margin:0;background:#f8f8f8}button{padding:9px;border:0;color:white;background:#007a5a}.app{display:flex;height:100vh}.side{width:260px;background:#3f0e40;color:white;padding:20px}.main{padding:30px;flex:1}input{padding:10px;margin:5px}</style></head><body><div id=app class=app><aside class=side><h2>Huddle</h2><div id=workspace-header>Workspace</div><div id=channel-list data-testid=channel-list></div></aside><main class=main><form id=auth-form data-testid=auth-form><input name=username placeholder=username><input name=password type=password placeholder=password><button data-testid=auth-submit data-button-role=primary>Sign in</button></form><h2 id=channel-title data-testid=channel-title>Welcome</h2><div id=message-list data-testid=message-list></div><input id=message-input data-testid=message-input><button id=send-btn data-testid=send-btn data-button-role=primary>Send</button></main></div></body></html>'''
        z=b.encode();self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(z)));self.end_headers();self.wfile.write(z)
class S(ThreadingHTTPServer): daemon_threads=True
S(('127.0.0.1',PORT),H).serve_forever()
