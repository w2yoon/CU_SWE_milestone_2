import os,sqlite3,json,re,secrets,hashlib,base64,datetime,time,threading,urllib.parse,struct,socket,select,email.parser,email.policy
from zoneinfo import ZoneInfo
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
DB='/app/huddle.db'; NODE=int(os.getenv('NODE_ID','0')); PORT=int(os.getenv('PORT','8000'))
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,timeout=5); c.row_factory=sqlite3.Row; c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=5000'); return c
def init():
 c=db(); c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT "UTC",avatar_url TEXT DEFAULT "",status_text TEXT DEFAULT "",status_emoji TEXT DEFAULT "");
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT "open");
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT "",is_archived INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS seq(channel_id INTEGER PRIMARY KEY,last_event_id INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload TEXT,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,data BLOB);
CREATE TABLE IF NOT EXISTS message_files(message_id INTEGER,file_id INTEGER,PRIMARY KEY(message_id,file_id));
CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,user_ids TEXT,UNIQUE(workspace_id,handle));
CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,node_id INTEGER,last_seen REAL);
CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY AUTOINCREMENT,payload TEXT);
CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,uses INTEGER DEFAULT 0,max_uses INTEGER DEFAULT 1);
'''); c.commit(); c.close()
init()
try:
 c=db(); c.execute('ALTER TABLE presence ADD COLUMN last_seen REAL'); c.commit(); c.close()
except: pass
try:
 c=db(); c.execute('DELETE FROM presence WHERE node_id=?',(NODE,)); c.commit(); c.close()
except: pass
def user(r): return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def fileobj(r): return {k:r[k] for k in ['id','uploader_id','filename','content_type','size','created_at']}
def channelobj(r):
 d=dict(r)
 for k in ('is_private','is_dm','is_archived'): d[k]=bool(d.get(k))
 return d
def groupobj(c,r):
 return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':json.loads(r['user_ids'] or '[]')}
def mention_ids(c,r,author):
 c.execute('DELETE FROM presence WHERE last_seen<?',(time.time()-0.3,)); c.commit()
 text=r['body']; ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
 if not ch:return []
 members={x['user_id'] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(r['channel_id'],))}
 out=set()
 for name in re.findall(r'(?<![A-Za-z0-9_@])@([A-Za-z0-9_-]+)',text):
  if name=='channel': out.update(members)
  elif name=='here':
   out.update(x['user_id'] for x in c.execute('SELECT user_id FROM presence WHERE last_seen>=?',(time.time()-0.3,)))
  else:
   g=c.execute('SELECT user_ids FROM groups_ WHERE workspace_id=? AND handle=?',(ch['workspace_id'],name)).fetchone()
   if g: out.update(json.loads(g['user_ids'] or '[]'))
   else:
    u=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
    if u: out.add(u['id'])
 return sorted((out & members)-{author})
def multipart(handler):
 n=int(handler.headers.get('Content-Length','0'))
 if n>10*1024*1024:return None
 raw=handler.rfile.read(n)
 try:
  msg=email.parser.BytesParser(policy=email.policy.default).parsebytes(('Content-Type: '+handler.headers.get('Content-Type','')+'\r\n\r\n').encode()+raw)
  for part in msg.iter_parts():
   if part.get_param('name',header='content-disposition')=='file':
    return part.get_filename() or 'file',part.get_content_type(),part.get_payload(decode=True)
 except Exception: pass
 return False
def auth(h):
 if not h.startswith('Bearer '): return None
 c=db(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(h[7:],)).fetchone(); c.close(); return r
def emit(c,ch,typ,payload):
 c.execute('INSERT OR IGNORE INTO seq(channel_id,last_event_id) VALUES(?,0)',(ch,))
 n=c.execute('SELECT last_event_id+1 FROM seq WHERE channel_id=?',(ch,)).fetchone()[0]; c.execute('UPDATE seq SET last_event_id=? WHERE channel_id=?',(n,ch))
 payload=dict(payload); payload.update(type=typ,event_id=n,channel_id=ch); c.execute('INSERT INTO events VALUES(?,?,?,?)',(ch,n,typ,json.dumps(payload))); return payload
def reactions(c,mid):
 rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY emoji,user_id',(mid,)).fetchall(); out=[]
 for e in sorted(set(x['emoji'] for x in rows)):
  ids=[x['user_id'] for x in rows if x['emoji']==e]; out.append({'emoji':e,'count':len(ids),'user_ids':ids})
 return out
def msg(c,r):
 a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[fileobj(x) for x in c.execute('SELECT f.* FROM files f JOIN message_files mf ON mf.file_id=f.id WHERE mf.message_id=?',(r['id'],)).fetchall()],'reactions':reactions(c,r['id']),'mentions':mention_ids(c,r,r['author_id'])}
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a): pass
 def send(self,code,obj=None,headers=None):
  b=(json.dumps(obj).encode() if obj is not None else b''); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b)))
  for k,v in (headers or {}).items(): self.send_header(k,v)
  self.end_headers()
  if b:self.wfile.write(b)
 def body(self):
  n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or b'{}')
 def pathparts(self): return urllib.parse.urlparse(self.path).path.split('/')
 def ws_frame(self,obj):
  raw=json.dumps(obj,separators=(',',':')).encode(); n=len(raw)
  if n<126: h=bytes((0x81,n))
  elif n<65536: h=bytes((0x81,126))+struct.pack('>H',n)
  else: h=bytes((0x81,127))+struct.pack('>Q',n)
  self.request.sendall(h+raw)
 def do_ws(self, token):
  a=auth('Bearer '+token)
  if not a:
   self.send(401,{'error':'unauthorized'}); return
  conn_id=secrets.token_urlsafe(16); c0=db(); c0.execute('INSERT INTO presence(conn_id,user_id,node_id,last_seen) VALUES(?,?,?,?)',(conn_id,a['id'],NODE,time.time())); c0.commit(); c0.close()
  key=self.headers.get('Sec-WebSocket-Key','')
  accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101,'Switching Protocols'); self.send_header('Upgrade','websocket'); self.send_header('Connection','Upgrade'); self.send_header('Sec-WebSocket-Accept',accept); self.end_headers()
  self.request.settimeout(.1); channel=None; last=0
  c0=db(); global_last=c0.execute('SELECT COALESCE(MAX(id),0) FROM global_events').fetchone()[0]; c0.close()
  subscribed=False
  try:
   while True:
    if not subscribed:
     try:
      h=self.request.recv(2)
      if not h:
       c=db(); c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,)); c.commit(); c.close(); return
      if h:
       n=h[1]&127; masked=h[1]&128
       if n==126:n=struct.unpack('>H',self.request.recv(2))[0]
       elif n==127:n=struct.unpack('>Q',self.request.recv(8))[0]
       mask=self.request.recv(4) if masked else b''; data=self.request.recv(n)
       if masked:data=bytes(x^mask[i%4] for i,x in enumerate(data))
       if data:
        q=json.loads(data); channel=int(q.get('channel_id',0)); subscribed=True
        c=db(); head=c.execute('SELECT COALESCE(last_event_id,0) FROM seq WHERE channel_id=?',(channel,)).fetchone(); last=(head[0] if head else 0); c.close()
        self.ws_frame({'type':'subscribed','channel_id':channel,'head_event_id':last})
     except socket.timeout: pass
    else:
     try:
      try:
       probe=self.request.recv(1,socket.MSG_PEEK|socket.MSG_DONTWAIT)
       if probe==b'':
        c=db(); c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,)); c.commit(); c.close(); return
      except BlockingIOError:
       pass
      h=self.request.recv(2)
      if not h:
       c=db(); c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,)); c.commit(); c.close(); return
      n=h[1]&127
      if n==126:self.request.recv(2)
      elif n==127:self.request.recv(8)
      if h[1]&128:
       mm=self.request.recv(4); nn=n if n<126 else 0
       if nn:self.request.recv(nn)
     except socket.timeout: pass
    c=db(); c.execute('UPDATE presence SET last_seen=? WHERE conn_id=?',(time.time(),conn_id)); c.commit()
    if subscribed:
     ready,_,_=select.select([self.request],[],[],0)
     if ready:
      try:
       probe=self.request.recv(1,socket.MSG_PEEK|socket.MSG_DONTWAIT)
       if probe==b'':
        c=db(); c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,)); c.commit(); c.close(); return
      except BlockingIOError: pass
    if subscribed:
     rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(channel,last)).fetchall()
     for r in rows:self.ws_frame(json.loads(r['payload'])); last=r['event_id']
    grows=c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(global_last,)).fetchall()
    for r in grows:self.ws_frame(json.loads(r['payload'])); global_last=r['id']
    c.close(); time.sleep(.05)
  except (OSError,ValueError,TypeError,ConnectionError):
   return
  finally:
   try:
    c=db(); c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,)); c.commit(); c.close()
   except: pass
 def do_GET(self):
  p=self.pathparts()
  if self.path.split('?',1)[0]=='/api/ws':
   q=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query); return self.do_ws(q.get('token',[''])[0])
  a=auth(self.headers.get('Authorization',''))
  if p==['','api','health']: return self.send(200,{'status':'ok','node_id':NODE})
  if p==['','api','auth','me']: return self.send(200,{'user':user(a)}) if a else self.send(401,{'error':'unauthorized'})
  if len(p)==4 and p[:3]==['','api','users']:
   if not a:return self.send(401,{'error':'unauthorized'})
   c=db(); r=c.execute('SELECT * FROM users WHERE id=?',(p[3],)).fetchone(); c.close(); return self.send(404) if not r else self.send(200,{'user':user(r)})
  if not a:return self.send(401,{'error':'unauthorized'})
  c=db()
  try:
   if len(p)>=4 and p[:3]==['','api','workspaces'] and (len(p)==5 and p[4]=='groups'):
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
    if not w:return self.send(404)
    if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],a['id'])).fetchone():return self.send(403)
    return self.send(200,{'groups':[groupobj(c,x) for x in c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)).fetchall()]})
   if p[:3]==['','api','workspaces'] and len(p)==3:
    rs=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(a['id'],)).fetchall(); return self.send(200,{'workspaces':[dict(x) for x in rs]})
   if len(p)>=4 and p[:3]==['','api','workspaces']:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
    if not w:return self.send(404)
    if len(p)==5 and p[4]=='members':
     return self.send(200,{'members':[dict(x) for x in c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],))]})
    ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_dm=0',(w['id'],)).fetchall()
    return self.send(200,{'workspace':dict(w),'channels':[dict(x) for x in ch],'read_state':[]})
   if len(p)==4 and p[:3]==['','api','files']:
    try: fid=int(p[3])
    except: return self.send(404)
    r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
    if not r:return self.send(404)
    return self.send(200,{'file':fileobj(r)})
   if len(p)==5 and p[:3]==['','api','files'] and p[4]=='download':
    try: fid=int(p[3])
    except: return self.send(404)
    r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
    if not r:return self.send(404)
    data=r['data']; self.send_response(200); self.send_header('Content-Type',r['content_type']); self.send_header('Content-Disposition','attachment; filename="'+r['filename'].replace('"','')+'"'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
   if len(p)>=4 and p[:3]==['','api','channels']:
    cid=int(p[3]); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    if not ch:return self.send(404)
    if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,a['id'])).fetchone(): return self.send(403)
    if len(p)==5 and p[4]=='members':
     rs=c.execute('SELECT u.* FROM channel_members m JOIN users u ON u.id=m.user_id WHERE m.channel_id=?',(cid,)).fetchall(); return self.send(200,{'members':[user(x) for x in rs]})
    if len(p)==5 and p[4]=='messages':
     rs=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 ORDER BY id DESC',(cid,)).fetchall(); return self.send(200,{'messages':[msg(c,x) for x in rs],'next_cursor':None})
    if len(p)==5 and p[4]=='pins': return self.send(200,{'pins':[],'next_cursor':None})
   if len(p)==5 and p[:3]==['','api','messages'] and p[4]=='replies':
    rs=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(p[3],)).fetchall(); return self.send(200,{'replies':[msg(c,x) for x in rs],'next_cursor':None})
  finally:c.close()
  return self.send(404)
 def do_POST(self):
  p=self.pathparts(); a=auth(self.headers.get('Authorization',''))
  if p==['','api','auth','register']:
   try:
    d=self.body(); u=d['username']; pw=d['password']
    if not re.fullmatch(r'[A-Za-z0-9_]+',u) or len(pw)<8: return self.send(400)
    c=db(); c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(u,hashlib.sha256(pw.encode()).hexdigest(),d.get('display_name') or u)); uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); c.commit(); r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); c.close(); return self.send(201,{'user':user(r),'token':t})
   except sqlite3.IntegrityError:return self.send(409)
   except:return self.send(400)
  if p==['','api','auth','login']:
   d=self.body(); c=db(); r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(d.get('username'),hashlib.sha256(d.get('password','').encode()).hexdigest())).fetchone()
   if not r:c.close(); return self.send(401)
   t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id'])); c.commit(); c.close(); return self.send(200,{'user':user(r),'token':t})
  if not a:return self.send(401)
  if p==['','api','files']:
   n=int(self.headers.get('Content-Length','0'))
   if n>10*1024*1024:
    self.rfile.read(n)
    return self.send(413)
   part=multipart(self)
   if part is None:return self.send(413)
   if not part:return self.send(400)
   filename,ctype,data=part
   c=db(); c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at,data) VALUES(?,?,?,?,?,?)',(a['id'],filename,ctype,len(data),now(),data)); fid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.commit(); r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone(); c.close(); return self.send(201,{'file':fileobj(r)})
  c=db()
  try:
   if len(p)==5 and p[:3]==['','api','workspaces'] and p[4]=='groups':
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
    if not w:return self.send(404)
    if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],a['id'])).fetchone():return self.send(403)
    d=self.body(); h=d.get('handle'); name=d.get('name'); ids=d.get('user_ids',[])
    if c.execute('SELECT 1 FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],h)).fetchone():return self.send(409)
    if not isinstance(h,str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,31}',h) or not isinstance(name,str) or not name or not isinstance(ids,list):return self.send(400)
    try:
     c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id,user_ids) VALUES(?,?,?,?,?)',(w['id'],h,name,a['id'],json.dumps(ids))); c.commit()
    except sqlite3.IntegrityError:return self.send(409)
    return self.send(201,{'group':groupobj(c,c.execute('SELECT * FROM groups_ WHERE id=last_insert_rowid()').fetchone())})
   if p==['','api','workspaces']:
    d=self.body(); c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d['name'],a['id'])); wid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO members VALUES(?,?,?)',(wid,a['id'],'owner')); c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,a['id'])); c.commit(); w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); return self.send(201,{'workspace':dict(w),'general_channel':dict(ch)})
   if len(p)==5 and p[:3]==['','api','workspaces'] and p[4]=='channels':
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
    if not w:return self.send(404)
    d=self.body(); role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],a['id'])).fetchone()
    if not role:return self.send(403)
    if d.get('is_private') and role['role'] not in ('owner','admin'):return self.send(403)
    try:c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],d['name'],int(bool(d.get('is_private'))),d.get('topic',''))); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,a['id'])); c.commit(); return self.send(201,{'channel':channelobj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
    except sqlite3.IntegrityError:return self.send(409)
   if len(p)==5 and p[:3]==['','api','channels'] and p[4]=='join':
    cid=int(p[3]); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    if not ch:return self.send(404)
    c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,a['id'])); c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],a['id'],'member')); c.commit(); return self.send(200,{'channel':dict(ch)})
   if len(p)==5 and p[:3]==['','api','channels'] and p[4]=='leave':
    c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(p[3],a['id'])); c.commit(); return self.send(200,{'channel':channelobj(c.execute('SELECT * FROM channels WHERE id=?',(p[3],)).fetchone())})
   if len(p)==5 and p[:3]==['','api','messages'] and p[4]=='reactions':
    mid=int(p[3]); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    if not r:return self.send(404)
    if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(r['channel_id'],a['id'])).fetchone():return self.send(403)
    ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
    if ch['is_archived']:return self.send(423)
    d=self.body(); emoji=d.get('emoji')
    if not isinstance(emoji,str) or not emoji:return self.send(400)
    cur=c.execute('INSERT OR IGNORE INTO reactions(message_id,user_id,emoji) VALUES(?,?,?)',(mid,a['id'],emoji))
    if cur.rowcount:
     emit(c,r['channel_id'],'reaction.added',{'message_id':mid,'emoji':emoji,'user_id':a['id']})
    c.commit(); out=reactions(c,mid); return self.send(200,{'reactions':out})
   if p==['','api','dms']:
    d=self.body()
    if 'user_id' not in d:return self.send(400)
    other=c.execute('SELECT id FROM users WHERE id=?',(d.get('user_id'),)).fetchone()
    if not other:return self.send(404)
    if other['id']==a['id']:return self.send(400)
    x,y=sorted((a['id'],other['id']))
    r=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(x,y)).fetchone()
    if r:return self.send(200,{'channel_id':r['channel_id']})
    c.execute('INSERT INTO channels(name,is_private,is_dm) VALUES(?,?,1)',(f'dm-{x}-{y}',1))
    cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.execute('INSERT INTO dms VALUES(?,?,?)',(x,y,cid))
    c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,x)); c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,y)); c.commit()
    return self.send(200,{'channel_id':cid})
   if len(p)==5 and p[:3]==['','api','channels'] and p[4] in ('archive','unarchive'):
    cid=int(p[3]); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    if not ch:return self.send(404)
    role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],a['id'])).fetchone()
    if not role or role['role']!='owner':return self.send(403)
    archived=1 if p[4]=='archive' else 0
    c.execute('UPDATE channels SET is_archived=? WHERE id=?',(archived,cid))
    emit(c,cid,'channel.archived' if archived else 'channel.unarchived',{})
    c.commit(); return self.send(200,{'channel':channelobj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
   if len(p)==5 and p[:3]==['','api','channels'] and p[4]=='messages':
    cid=int(p[3]); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); d=self.body()
    if not ch:return self.send(404)
    if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,a['id'])).fetchone():return self.send(403)
    body=str(d.get('body',''))
    if body.startswith('/') and not body.startswith('//'):
     parts=body.split(None,1); cmd=parts[0]; arg=parts[1] if len(parts)>1 else ''
     role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],a['id'])).fetchone()
     if cmd=='/me':
      if not arg.strip():return self.send(400)
     elif cmd=='/shrug':
      body=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
     elif cmd=='/topic':
      if len(arg)>250:return self.send(400)
      c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,cid)); emit(c,cid,'channel.updated',{}); c.commit()
      return self.send(201,{'message':None,'channel':channelobj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
     elif cmd=='/invite':
      if not arg.startswith('@') or not role or role['role'] not in ('owner','admin'):return self.send(403)
      uname=arg[1:].strip(); ur=c.execute('SELECT * FROM users WHERE username=?',(uname,)).fetchone()
      if not ur:return self.send(404)
      c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],ur['id'],'member')); c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,ur['id'])); c.commit()
      return self.send(201,{'message':None,'invited_user':user(ur)})
     elif cmd in ('/archive','/unarchive'):
      if not role or role['role']!='owner':return self.send(403)
      val=1 if cmd=='/archive' else 0; c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,cid)); emit(c,cid,'channel.archived' if val else 'channel.unarchived',{}); c.commit()
      return self.send(201,{'message':None,'channel':channelobj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
     else:return self.send(400)
    if ch['is_archived']:return self.send(423)
    if not body.strip():return self.send(400)
    d['body']=body
    fids=d.get('file_ids',[])
    if not isinstance(fids,list):return self.send(400)
    for fid in fids:
     fr=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
     if not fr or fr['uploader_id']!=a['id'] or c.execute('SELECT 1 FROM message_files WHERE file_id=?',(fid,)).fetchone():return self.send(400)
    c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,a['id'],d['body'],d.get('parent_id'),now())); mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; [c.execute('INSERT INTO message_files VALUES(?,?)',(mid,fid)) for fid in fids]; r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); emit(c,cid,'message.reply' if d.get('parent_id') else 'message.created',{'message':msg(c,r)}); c.commit(); return self.send(201,{'message':msg(c,r)})
  finally:c.close()
  return self.send(404)
 def do_PATCH(self):
  p=self.pathparts(); a=auth(self.headers.get('Authorization',''))
  if not a:return self.send(401)
  if len(p)==6 and p[:3]==['','api','workspaces'] and p[4]=='groups':
   c=db(); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
   if not w:return self.send(404)
   g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],p[5])).fetchone()
   if not g:return self.send(404)
   role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],a['id'])).fetchone()
   if a['id']!=g['creator_id'] and (not role or role['role']!='owner'):return self.send(403)
   d=self.body()
   if 'name' in d and (not isinstance(d['name'],str) or not d['name']):return self.send(400)
   if 'user_ids' in d and not isinstance(d['user_ids'],list):return self.send(400)
   if 'name' in d:c.execute('UPDATE groups_ SET name=? WHERE id=?',(d['name'],g['id']))
   if 'user_ids' in d:c.execute('UPDATE groups_ SET user_ids=? WHERE id=?',(json.dumps(d['user_ids']),g['id']))
   c.commit(); return self.send(200,{'group':groupobj(c,c.execute('SELECT * FROM groups_ WHERE id=?',(g['id'],)).fetchone())})
  if p==['','api','users','me']:
   d=self.body(); allowed={'display_name':80,'timezone':128,'avatar_url':512,'status_text':140,'status_emoji':64}
   if not isinstance(d,dict) or any(k not in allowed or not isinstance(v,str) or (k=='display_name' and not v) or len(v)>lim for k,v in d.items() for lim in [allowed[k]]): return self.send(400)
   if 'timezone' in d:
    try: ZoneInfo(d['timezone'])
    except Exception: return self.send(400)
   c=db()
   for k,v in d.items(): c.execute(f'UPDATE users SET {k}=? WHERE id=?',(v,a['id']))
   c.commit(); r=c.execute('SELECT * FROM users WHERE id=?',(a['id'],)).fetchone()
   c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps({'type':'user.updated','user':user(r)}),)); c.commit(); c.close()
   return self.send(200,{'user':user(r)})
  return self.send(404)
 def do_DELETE(self):
  p=self.pathparts(); a=auth(self.headers.get('Authorization',''))
  if not a:return self.send(401)
  if len(p)==6 and p[:3]==['','api','workspaces'] and p[4]=='groups':
   c=db(); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
   if not w:return self.send(404)
   g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],p[5])).fetchone()
   if not g:return self.send(200,{'deleted':True})
   role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],a['id'])).fetchone()
   if a['id']!=g['creator_id'] and (not role or role['role']!='owner'):return self.send(403)
   c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],)); c.commit(); return self.send(200,{'deleted':True})
  m=re.match(r'^/api/messages/(\d+)/reactions/(.+)$',self.path.split('?',1)[0])
  if m:
   c=db(); r=c.execute('SELECT * FROM messages WHERE id=?',(m.group(1),)).fetchone()
   if not r:return self.send(404)
   if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(r['channel_id'],a['id'])).fetchone():return self.send(403)
   emoji=urllib.parse.unquote(m.group(2)); cur=c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(r['id'],a['id'],emoji));
   if cur.rowcount: emit(c,r['channel_id'],'reaction.removed',{'message_id':r['id'],'emoji':emoji,'user_id':a['id']})
   c.commit(); out=reactions(c,r['id']); c.close(); return self.send(200,{'reactions':out})
  return self.send(404)
def run():
 ThreadingHTTPServer.daemon_threads=True
 s=ThreadingHTTPServer(('127.0.0.1',PORT),H); s.serve_forever()
if __name__=='__main__': run()
