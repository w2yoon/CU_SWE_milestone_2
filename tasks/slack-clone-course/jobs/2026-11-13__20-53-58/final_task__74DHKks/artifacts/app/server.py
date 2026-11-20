import os,json,base64,hashlib,struct,socket,threading,time,re
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
from core import *
NODE=int(os.environ.get('NODE_ID','0')); PORT=int(os.environ.get('PORT','8000'))
def send(h,code,obj):
    b=json.dumps(obj).encode();h.send_response(code);h.send_header('Content-Type','application/json');h.send_header('Content-Length',str(len(b)));h.end_headers();h.wfile.write(b)
def body(h):
    try:
        n=int(h.headers.get('Content-Length','0')); x=json.loads(h.rfile.read(n) or b'{}'); return x if isinstance(x,dict) else None
    except: return None
def token(h):
    x=h.headers.get('Authorization','')
    return x[7:] if x.startswith('Bearer ') else ''
def ws_frame(x):
    b=json.dumps(x,separators=(',',':')).encode();n=len(b)
    if n<126:return b'\x81'+bytes([n])+b
    if n<65536:return b'\x81\x7e'+struct.pack('>H',n)+b
    return b'\x81\x7f'+struct.pack('>Q',n)+b
def ws_read(s):
    h=s.recv(2)
    if len(h)<2:return None
    n=h[1]&127
    if n==126:n=struct.unpack('>H',s.recv(2))[0]
    elif n==127:n=struct.unpack('>Q',s.recv(8))[0]
    mask=s.recv(4); b=s.recv(n); return json.loads(bytes(b[i]^mask[i%4] for i in range(n)))
def ws_handler(h,u):
    if not auth(parse_qs(urlparse(u).query).get('token',[''])[0]): send(h,401,{'error':'unauthorized'});return
    key=h.headers.get('Sec-WebSocket-Key',''); acc=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
    h.send_response(101,'Switching Protocols');h.send_header('Upgrade','websocket');h.send_header('Connection','Upgrade');h.send_header('Sec-WebSocket-Accept',acc);h.end_headers()
    s=h.connection;s.settimeout(.15); subs={}
    try:
        while True:
            try:
                q=ws_read(s)
                if q:
                    typ=q.get('type'); ch=int(q.get('channel_id',0))
                    c=db(); head=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(ch,)).fetchone(); head=head[0] if head else 0
                    if typ=='subscribe': subs[ch]=head;s.sendall(ws_frame({'type':'subscribed','channel_id':ch,'head_event_id':head}))
                    elif typ=='resume':
                        since=int(q.get('since_event_id',0)); rows=c.execute('SELECT event_id,type,payload_json FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since,)).fetchall()
                        for r in rows:
                            p=json.loads(r['payload_json']);p.update(type=r['type'],event_id=r['event_id'],channel_id=ch);s.sendall(ws_frame(p))
                        subs[ch]=head;s.sendall(ws_frame({'type':'resumed','channel_id':ch,'head_event_id':head}))
                    c.close()
            except (socket.timeout,ValueError): pass
            c=db()
            for ch,last in list(subs.items()):
                for r in c.execute('SELECT event_id,type,payload_json FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,last)):
                    p=json.loads(r['payload_json']);p.update(type=r['type'],event_id=r['event_id'],channel_id=ch);s.sendall(ws_frame(p));subs[ch]=r['event_id']
            c.close()
    except (OSError,ConnectionError): pass
class H(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def do_GET(self):
        u=urlparse(self.path); p=u.path
        if p=='/api/ws': return ws_handler(self,self.path)
        if p=='/api/health':return send(self,200,{'status':'ok','node_id':NODE})
        uid=auth(token(self))
        if p=='/api/auth/me':
            if not uid:return send(self,401,{'error':'unauthorized'})
            return send(self,200,{'user':user_obj(uid)})
        if p=='/api/workspaces':
            if not uid:return send(self,401,{'error':'unauthorized'})
            c=db(); rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(uid['id'],)).fetchall();c.close();return send(self,200,{'workspaces':[workspace_obj(x) for x in rows]})
        m=re.fullmatch(r'/api/workspaces/([^/]+)',p)
        if m:
            if not uid:return send(self,401,{'error':'unauthorized'})
            c=db();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:c.close();return send(self,404,{'error':'not found'})
            cs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall();c.close();return send(self,200,{'workspace':workspace_obj(w),'channels':[channel_obj(x) for x in cs],'read_state':{}})
        m=re.fullmatch(r'/api/channels/(\d+)/messages',p)
        if m:
            if not uid:return send(self,401,{'error':'unauthorized'})
            c=db();ch=c.execute('SELECT * FROM channels WHERE id=?',(int(m.group(1)),)).fetchone()
            if not ch:c.close();return send(self,404,{'error':'not found'})
            if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],uid['id'])).fetchone():c.close();return send(self,403,{'error':'forbidden'})
            rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 ORDER BY id DESC',(ch['id'],)).fetchall();out=[msg_obj(c,x) for x in rows];c.close();return send(self,200,{'messages':out,'next_cursor':None})
        m=re.fullmatch(r'/api/messages/(\d+)/replies',p)
        if m:
            if not uid:return send(self,401,{'error':'unauthorized'})
            c=db();r=c.execute('SELECT * FROM messages WHERE id=?',(int(m.group(1)),)).fetchone()
            if not r:c.close();return send(self,404,{'error':'not found'})
            rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(r['id'],)).fetchall();out=[msg_obj(c,x) for x in rows];c.close();return send(self,200,{'replies':out,'next_cursor':None})
        m=re.fullmatch(r'/api/workspaces/([^/]+)/members',p)
        if m:
            if not uid:return send(self,401,{'error':'unauthorized'})
            c=db();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:c.close();return send(self,404,{'error':'not found'})
            rows=c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],)).fetchall();c.close()
            return send(self,200,{'members':[dict(r) for r in rows]})
        m=re.fullmatch(r'/api/channels/(\\d+)/members',p)
        if m:
            if not uid:return send(self,401,{'error':'unauthorized'})
            c=db();ch=c.execute('SELECT * FROM channels WHERE id=?',(int(m.group(1)),)).fetchone()
            if not ch:c.close();return send(self,404,{'error':'not found'})
            rows=c.execute('SELECT u.* FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?',(ch['id'],)).fetchall();c.close()
            return send(self,200,{'members':[user_obj(r) for r in rows]})
        return send(self,404,{'error':'not found'})
    def do_POST(self):
        p=urlparse(self.path).path;x=body(self);uid=auth(token(self))
        if p=='/api/auth/register':
            if not x:return send(self,400,{'error':'invalid'})
            try:r,t=register(x.get('username'),x.get('password'),x.get('display_name',''));return send(self,201,{'user':user_obj(r),'token':t})
            except RuntimeError:return send(self,409,{'error':'duplicate'})
            except:return send(self,400,{'error':'invalid'})
        if p=='/api/auth/login':
            r=login((x or {}).get('username'),(x or {}).get('password'))
            return send(self,200,{'user':user_obj(r[0]),'token':r[1]}) if r else send(self,401,{'error':'invalid'})
        if not uid:return send(self,401,{'error':'unauthorized'})
        if p=='/api/workspaces':
            if not x or not re.fullmatch(r'[a-z0-9-]{2,32}',x.get('slug','')) or not x.get('name'): return send(self,400,{'error':'invalid'})
            c=db()
            try:
                c.execute('BEGIN IMMEDIATE'); c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(x['slug'],x['name'],uid['id']))
                wid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO members VALUES(?,?,?)',(wid,uid['id'],'owner'))
                c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
                c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,uid['id'])); c.execute('INSERT INTO channel_seq VALUES(?,0)',(cid,)); c.commit()
                w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close()
                return send(self,201,{'workspace':workspace_obj(w),'general_channel':channel_obj(ch)})
            except sqlite3.IntegrityError:
                c.rollback(); c.close(); return send(self,409,{'error':'duplicate'})
        m=re.fullmatch(r'/api/workspaces/([^/]+)/channels',p)
        if m:
            c=db();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:c.close();return send(self,404,{'error':'not found'})
            if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],uid['id'])).fetchone():c.close();return send(self,403,{'error':'forbidden'})
            name=(x or {}).get('name','')
            if not re.fullmatch(r'[a-z0-9-]{1,32}',name):c.close();return send(self,400,{'error':'invalid'})
            try:
                c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],name,bool((x or {}).get('is_private')),((x or {}).get('topic') or '')[:250]));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,uid['id']));c.execute('INSERT INTO channel_seq VALUES(?,0)',(cid,));c.commit();r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone();c.close();return send(self,201,{'channel':channel_obj(r)})
            except sqlite3.IntegrityError:c.rollback();c.close();return send(self,409,{'error':'duplicate'})
        m=re.fullmatch(r'/api/channels/(\d+)/join',p)
        if m:
            c=db();ch=c.execute('SELECT * FROM channels WHERE id=?',(int(m.group(1)),)).fetchone()
            if not ch:c.close();return send(self,404,{'error':'not found'})
            c.execute('INSERT OR IGNORE INTO members SELECT workspace_id,?,\'member\' FROM channels WHERE id=?',(uid['id'],ch['id']));c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],uid['id']));c.commit();c.close();return send(self,200,{'channel':channel_obj(ch)})
        m=re.fullmatch(r'/api/channels/(\d+)/messages',p)
        if m:
            try:return send(self,201,{'message':create_message(uid['id'],int(m.group(1)),(x or {}).get('body',''),(x or {}).get('parent_id'))})
            except LookupError:return send(self,404,{'error':'not found'})
            except PermissionError as e:return send(self,423 if str(e)=='archived' else 403,{'error':'forbidden'})
            except:return send(self,400,{'error':'invalid'})
        return send(self,404,{'error':'not found'})
class S(ThreadingHTTPServer): daemon_threads=True
S(('127.0.0.1',PORT),H).serve_forever()
