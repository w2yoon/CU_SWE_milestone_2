import os,sys,json,re,sqlite3,threading,time,base64,hashlib,struct,socket,urllib.parse,email.parser,email.policy
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
import core
core.init()
NODE=int(os.environ.get('NODE_ID','0')); PORT=int(os.environ.get('PORT','8000'))
def send(h,code,obj,headers=None):
 b=json.dumps(obj,separators=(',',':')).encode(); h.send_response(code);h.send_header('Content-Type','application/json');h.send_header('Content-Length',str(len(b)))
 for k,v in (headers or {}).items():h.send_header(k,v)
 h.end_headers();h.wfile.write(b)
def err(h,c,s):send(h,c,{'error':s})
def body(h):
 n=int(h.headers.get('Content-Length','0'))
 try:
  x=json.loads(h.rfile.read(n) or b'{}'); return x if isinstance(x,dict) else None
 except:return None
def tok(h):
 x=h.headers.get('Authorization','')
 return x[7:] if x.startswith('Bearer ') else None
def iso():return core.now()
def wsobj(r):return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def chobj(r):return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'],'is_archived':bool(r['is_archived'])}
def wsock_frame(x):
 b=json.dumps(x,separators=(',',':')).encode(); n=len(b)
 if n<126:return b'\x81'+bytes([n])+b
 if n<65536:return b'\x81\x7e'+struct.pack('>H',n)+b
 return b'\x81\x7f'+struct.pack('>Q',n)+b
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a):pass
 def do_OPTIONS(self):self.send_response(204);self.end_headers()
 def do_GET(self):self.route()
 def do_POST(self):self.route()
 def do_PATCH(self):self.route()
 def do_DELETE(self):self.route()
 def route(self):
  p=urllib.parse.urlparse(self.path); path=p.path; q=urllib.parse.parse_qs(p.query)
  if path=='/api/health':return send(self,200,{'status':'ok','node_id':NODE})
  if path=='/api/ws':return self.websocket(q)
  u=core.auth(tok(self))
  if path=='/api/auth/register':
   d=body(self)
   if not d or not re.fullmatch(r'[A-Za-z0-9_]+',str(d.get('username',''))) or len(str(d.get('password','')))<8:return err(self,400,'invalid')
   c=core.conn()
   try:
    c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(d['username'],hashlib.sha256(d['password'].encode()).hexdigest(),d.get('display_name') or d['username']));c.commit()
   except sqlite3.IntegrityError:c.close();return err(self,409,'duplicate')
   uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone();t=base64.urlsafe_b64encode(os.urandom(24)).decode();c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid));c.commit();c.close();return send(self,201,{'user':core.user(r),'token':t})
  if path=='/api/auth/login':
   d=body(self) or {};c=core.conn();r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(d.get('username'),hashlib.sha256(str(d.get('password','')).encode()).hexdigest())).fetchone()
   if not r:c.close();return err(self,401,'invalid')
   t=base64.urlsafe_b64encode(os.urandom(24)).decode();c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id']));c.commit();c.close();return send(self,200,{'user':core.user(r),'token':t})
  if not u:return err(self,401,'unauthorized')
  uid=u['id'];c=core.conn()
  try:
   if path=='/api/auth/me':return send(self,200,{'user':u})
   if path=='/api/users/me' and self.command=='PATCH':
    d=body(self) or {}; allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}
    if 'timezone' in d and d['timezone'] not in ('UTC','Europe/Paris','America/New_York','Europe/London','Asia/Tokyo'):return err(self,400,'timezone')
    limits={'display_name':80,'status_text':140,'status_emoji':64,'avatar_url':512}
    if any(k not in allowed or not isinstance(v,str) or len(v)==0 or (k in limits and len(v)>limits[k]) for k,v in d.items()):return err(self,400,'invalid')
    for k,v in d.items():c.execute('UPDATE users SET '+k+'=? WHERE id=?',(v,uid))
    c.commit(); ur=core.user(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()); c.execute('INSERT INTO user_events(user_id,payload,created_at) VALUES(?,?,?)',(uid,json.dumps(ur),time.time())); c.commit(); return send(self,200,{'user':ur})
   m=re.fullmatch(r'/api/users/(\d+)',path)
   if m:
    r=c.execute('SELECT * FROM users WHERE id=?',(int(m.group(1)),)).fetchone();return send(self,200,{'user':core.user(r)}) if r else err(self,404,'not found')
   if path=='/api/workspaces' and self.command=='GET':
    rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(uid,)).fetchall();return send(self,200,{'workspaces':[wsobj(x) for x in rows]})
   if path=='/api/workspaces' and self.command=='POST':
    d=body(self) or {};slug=str(d.get('slug',''))
    if not re.fullmatch(r'[a-z0-9-]{2,32}',slug) or not d.get('name'):return err(self,400,'invalid')
    try:
     c.execute('BEGIN IMMEDIATE');c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(slug,d['name'],uid));wid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO members VALUES(?,?,?)',(wid,uid,'owner'));c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,uid));c.commit()
    except sqlite3.IntegrityError:c.rollback();return err(self,409,'duplicate')
    w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone();return send(self,201,{'workspace':wsobj(w),'general_channel':chobj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
   m=re.fullmatch(r'/api/workspaces/([^/]+)(.*)',path)
   if m:
    slug,tail=m.group(1),m.group(2);w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
    if not w:return err(self,404,'workspace')
    mem=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],uid)).fetchone()
    if tail=='':
     if not mem:return err(self,403,'forbidden')
     cs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall();return send(self,200,{'workspace':wsobj(w),'channels':[chobj(x) for x in cs],'read_state':{}})
    if tail=='/members' and self.command=='GET':return send(self,200,{'members':[dict(x) for x in c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],))]})
    if tail=='/channels' and self.command=='POST':
     if not mem:return err(self,403,'forbidden')
     d=body(self) or {}; name=str(d.get('name',''))
     if not re.fullmatch(r'[a-z0-9-]{1,32}',name) or (d.get('is_private') and mem['role'] not in ('owner','admin')):return err(self,400 if not name else 403,'invalid')
     if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],name)).fetchone():return err(self,409,'duplicate')
     c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],name,int(bool(d.get('is_private'))),d.get('topic','')));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,uid));c.commit();return send(self,201,{'channel':chobj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
    gm=re.fullmatch(r'/groups(?:/([^/]+))?',tail)
    if gm:
     if not mem:return err(self,403,'forbidden')
     handle=gm.group(1)
     if self.command=='GET':return send(self,200,{'groups':[{'id':x['id'],'handle':x['handle'],'name':x['name'],'member_user_ids':json.loads(x['members'])} for x in c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],))]})
     d=body(self) or {}
     if self.command=='POST':
      if not re.fullmatch(r'[a-z0-9_-]+',str(d.get('handle',''))) or not d.get('name'):return err(self,400,'invalid')
      try:c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id,members) VALUES(?,?,?,?,?)',(w['id'],d['handle'],d['name'],uid,json.dumps(d.get('user_ids',[]))));c.commit()
      except sqlite3.IntegrityError:return err(self,409,'duplicate')
      x=c.execute('SELECT * FROM groups_ WHERE id=last_insert_rowid()').fetchone();return send(self,201,{'group':{'id':x['id'],'handle':x['handle'],'name':x['name'],'member_user_ids':d.get('user_ids',[])}})
     x=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],handle)).fetchone()
     if self.command=='DELETE':
      if x and x['creator_id']!=uid and w['owner_id']!=uid:return err(self,403,'forbidden')
      if x:c.execute('DELETE FROM groups_ WHERE id=?',(x['id'],));c.commit()
      return send(self,200,{'deleted':True})
     if not x:return err(self,404,'group')
     if x['creator_id']!=uid and w['owner_id']!=uid:return err(self,403,'forbidden')
     c.execute('UPDATE groups_ SET name=?,members=? WHERE id=?',(d.get('name',x['name']),json.dumps(d.get('user_ids',json.loads(x['members']))),x['id']));c.commit();x=c.execute('SELECT * FROM groups_ WHERE id=?',(x['id'],)).fetchone();return send(self,200,{'group':{'id':x['id'],'handle':x['handle'],'name':x['name'],'member_user_ids':json.loads(x['members'])}})
   m=re.fullmatch(r'/api/channels/(\d+)(.*)',path)
   if m:
    cid=int(m.group(1));tail=m.group(2);ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    if not ch:return err(self,404,'channel')
    ismem=core.accessible(c,uid,cid); w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
    if tail=='/join' and self.command=='POST':
     if ch['is_private']:return err(self,403,'private')
     c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w['id'],uid,'member'));c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(cid,uid));c.commit();return send(self,200,{'channel':chobj(ch)})
    if tail=='/leave' and self.command=='POST':c.execute('DELETE FROM cmembers WHERE channel_id=? AND user_id=?',(cid,uid));c.commit();return send(self,200,{'channel':chobj(ch)})
    if not ismem:return err(self,403,'forbidden')
    if tail=='/members':return send(self,200,{'members':[core.user(x) for x in c.execute('SELECT u.* FROM users u JOIN cmembers m ON m.user_id=u.id WHERE m.channel_id=?',(cid,))]})
    if tail in ('/archive','/unarchive') and self.command=='POST':
     if w['owner_id']!=uid:return err(self,403,'owner')
     v=tail=='/archive';c.execute('UPDATE channels SET is_archived=? WHERE id=?',(v,cid));c.commit();return send(self,200,{'channel':chobj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
    if tail=='' and self.command=='PATCH':
     d=body(self) or {};c.execute('UPDATE channels SET topic=? WHERE id=?',(d.get('topic',''),cid));c.commit();return send(self,200,{'channel':chobj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
    if tail=='/messages' and self.command=='GET':
     rs=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC',(cid,)).fetchall();return send(self,200,{'messages':[core.msg(c,x) for x in rs],'next_cursor':None})
    if tail=='/messages' and self.command=='POST':
     d=body(self) or {};b=str(d.get('body',''))
     if not b.strip():return err(self,400,'empty')
     if b.startswith('/') and not b.startswith('//'):
      z=b.split(' ',1);cmd=z[0];arg=z[1] if len(z)>1 else ''
      if cmd=='/shrug':b=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
      elif cmd=='/me' and not arg.strip():return err(self,400,'text')
      elif cmd in ('/topic','/archive','/unarchive'):
       if cmd=='/topic':
        if len(arg)>250:return err(self,400,'topic')
        c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,cid));c.commit();return send(self,201,{'message':None,'channel':chobj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
       if w['owner_id']!=uid:return err(self,403,'owner')
       v=cmd=='/archive';c.execute('UPDATE channels SET is_archived=? WHERE id=?',(v,cid));c.commit();return send(self,201,{'message':None,'channel':chobj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
      elif cmd=='/invite':
       nm=arg.lstrip('@');x=c.execute('SELECT * FROM users WHERE username=?',(nm,)).fetchone()
       if not x:return err(self,404,'user')
       c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w['id'],x['id'],'member'));c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(cid,x['id']));c.commit();return send(self,201,{'message':None,'invited_user':core.user(x)})
      elif cmd not in ('/me',):return err(self,400,'command')
     if ch['is_archived']:return err(self,423,'archived')
     parent=d.get('parent_id')
     fids=d.get('file_ids',[])
     if 'file_ids' in d and (not isinstance(fids,list) or any(not isinstance(x,int) for x in fids)): return err(self,400,'file_ids')
     if fids:
      for fid in fids:
       fr=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
       if not fr or fr['uploader_id']!=uid or c.execute('SELECT 1 FROM message_files WHERE file_id=?',(fid,)).fetchone(): return err(self,400,'file')
     mm=core.mentions(c,cid,uid,b)
     c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(cid,uid,b,parent,iso(),json.dumps(mm)))
     mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
     for fid in fids: c.execute('INSERT INTO message_files VALUES(?,?)',(mid,fid))
     r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
     ev=core.emit(c,cid,'message.reply' if parent else 'message.created',{'message':core.msg(c,r),'channel_id':cid})
     c.commit();return send(self,201,{'message':core.msg(c,r)})
   mm=re.fullmatch(r'/api/messages/(\d+)/replies',path)
   if mm and self.command=='GET':
    mid=int(mm.group(1));mr=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    if not mr:return err(self,404,'message')
    if not core.accessible(c,uid,mr['channel_id']):return err(self,403,'forbidden')
    rs=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(mid,)).fetchall()
    return send(self,200,{'replies':[core.msg(c,x) for x in rs],'next_cursor':None})
   mm=re.fullmatch(r'/api/messages/(\d+)/reactions(?:/(.*))?',path)
   if mm:
    mid=int(mm.group(1));mr=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    if not mr:return err(self,404,'message')
    if not core.accessible(c,uid,mr['channel_id']):return err(self,403,'forbidden')
    cc=channel=c.execute('SELECT * FROM channels WHERE id=?',(mr['channel_id'],)).fetchone()
    if cc['is_archived']:return err(self,423,'archived')
    emoji=urllib.parse.unquote(mm.group(2) or '')
    if self.command=='POST':
     d=body(self) or {};emoji=d.get('emoji','')
     if not emoji:return err(self,400,'emoji')
     c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,uid,emoji)).fetchone()
     if not old:
      c.execute('INSERT INTO reactions VALUES(?,?,?)',(mid,uid,emoji));core.emit(c,mr['channel_id'],'reaction.added',{'message_id':mid,'emoji':emoji,'user_id':uid})
     c.commit();return send(self,200,{'reactions':core.reactions(c,mid)})
    if self.command=='DELETE':
     c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,uid,emoji)).fetchone()
     if old:
      c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,uid,emoji));core.emit(c,mr['channel_id'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':uid})
     c.commit();return send(self,200,{'reactions':core.reactions(c,mid)})
   if path=='/api/dms' and self.command=='POST':
    d=body(self) or {};other=d.get('user_id')
    if other is None:return err(self,400,'user_id')
    if not c.execute('SELECT 1 FROM users WHERE id=?',(other,)).fetchone():return err(self,404,'user')
    a,b=sorted((uid,int(other)));r=c.execute('SELECT id FROM channels WHERE is_dm=1 AND dm_a=? AND dm_b=?',(a,b)).fetchone()
    if not r:
     c.execute('BEGIN IMMEDIATE');c.execute('INSERT INTO channels(name,is_private,is_dm,dm_a,dm_b) VALUES(?,?,?,?,?)',(f'dm-{a}-{b}',1,1,a,b));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,a));c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,b));c.commit()
    else:cid=r['id']
    return send(self,200,{'channel_id':cid})
   fm=re.fullmatch(r'/api/files/(\d+)(/download)?',path)
   if fm:
    r=c.execute('SELECT * FROM files WHERE id=?',(int(fm.group(1)),)).fetchone()
    if not r:return err(self,404,'file')
    if fm.group(2):
     dat=open('/app/data/files/'+str(r['id']),'rb').read();self.send_response(200);self.send_header('Content-Type',r['content_type']);self.send_header('Content-Disposition','attachment; filename="'+r['filename']+'"');self.send_header('Content-Length',str(len(dat)));self.end_headers();self.wfile.write(dat);return
    return send(self,200,{'file':core.fileobj(r)})
   if path=='/api/files' and self.command=='POST':
    n=int(self.headers.get('Content-Length','0'))
    if n>10485760:
     self.rfile.read(n)
     return err(self,413,'too large')
    raw=self.rfile.read(n);ct=self.headers.get('Content-Type','');m=re.search(r'boundary=([^;]+)',ct)
    if not m:return err(self,400,'file')
    part=raw.split(('--'+m.group(1)).encode())
    filename=None;ctype='application/octet-stream';dat=None
    for z in part:
     if b'filename=' in z and re.search(br'name="file"',z.split(b'\r\n\r\n',1)[0]):
      hh,dat=z.split(b'\r\n\r\n',1);dat=dat.rsplit(b'\r\n',1)[0]
      fm2=re.search(br'filename="([^"]*)"',hh); filename=fm2.group(1).decode() if fm2 else 'file'
      cm=re.search(br'Content-Type:\s*([^\r\n]+)',hh,re.I);ctype=cm.group(1).decode() if cm else ctype
    if dat is None:return err(self,400,'file')
    c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(uid,filename,ctype,len(dat),core.now()));fid=c.execute('SELECT last_insert_rowid()').fetchone()[0];open('/app/data/files/'+str(fid),'wb').write(dat);c.commit()
    return send(self,201,{'file':core.fileobj(c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone())})
  except Exception as e:
   c.rollback()
   return err(self,500,str(e))
 def websocket(self,q):
  t=q.get('token',[''])[0];u=core.auth(t)
  if not u:return err(self,401,'unauthorized')
  key=self.headers.get('Sec-WebSocket-Key')
  if not key:return err(self,400,'websocket')
  import hashlib,base64
  acc=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101);self.send_header('Upgrade','WebSocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',acc);self.end_headers()
  self.connection.settimeout(0.05); last={}; global_last=0
  pc=core.conn(); pc.execute('INSERT OR REPLACE INTO presence(user_id,last_seen) VALUES(?,?)',(u['id'],time.time())); pc.commit(); pc.close()
  while True:
   try:
    for cid,since in list(last.items()):
     c=core.conn()
     for r in c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)):
      z=json.loads(r['payload_json']);z.update(type=r['type'],event_id=r['event_id'],channel_id=cid);self.connection.sendall(wsock_frame(z));last[cid]=r['event_id']
     c.close()
    h=self.connection.recv(2)
    if not h:break
    n=h[1]&127
    if n==126:n=struct.unpack('>H',self.connection.recv(2))[0]
    elif n==127:n=struct.unpack('>Q',self.connection.recv(8))[0]
    mask=self.connection.recv(4);data=self.connection.recv(n)
    if h[0]&15==8:break
    if h[0]&15==1:
     x=json.loads(bytes(v^mask[i%4] for i,v in enumerate(data)));typ=x.get('type');cid=int(x.get('channel_id',0));c=core.conn()
     if typ=='subscribe':
      rr=c.execute('SELECT COALESCE(last_event_id,0) n FROM channel_seq WHERE channel_id=?',(cid,)).fetchone();head=rr['n'] if rr else 0;last[cid]=head;self.connection.sendall(wsock_frame({'type':'subscribed','channel_id':cid,'head_event_id':head}))
     elif typ=='resume':
      since=int(x.get('since_event_id',0));rows=c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall()
      for r in rows:
       z=json.loads(r['payload_json']);z.update(type=r['type'],event_id=r['event_id'],channel_id=cid);self.connection.sendall(wsock_frame(z))
      rr=c.execute('SELECT COALESCE(last_event_id,0) n FROM channel_seq WHERE channel_id=?',(cid,)).fetchone();head=rr['n'] if rr else 0;last[cid]=head;self.connection.sendall(wsock_frame({'type':'resumed','channel_id':cid,'head_event_id':head}))
     c.close()
   except socket.timeout: pass
   except Exception: break
   pc=core.conn()
   try:
    for z in pc.execute('SELECT id,payload FROM user_events WHERE id>? ORDER BY id',(global_last,)):
     self.connection.sendall(wsock_frame({'type':'user.updated','user':json.loads(z['payload'])})); global_last=z['id']
   except sqlite3.OperationalError:
    pass
   pc.close()
   try:
    for cid,since in list(last.items()):
     c=core.conn()
     for r in c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)):
      z=json.loads(r['payload_json']);z.update(type=r['type'],event_id=r['event_id'],channel_id=cid);self.connection.sendall(wsock_frame(z));last[cid]=r['event_id']
     c.close()
   except Exception:
    pass
  pc=core.conn(); pc.execute('DELETE FROM presence WHERE user_id=?',(u['id'],)); pc.commit(); pc.close()
class S(ThreadingHTTPServer):
 daemon_threads=True
if __name__=='__main__':
 S(('127.0.0.1',PORT),H).serve_forever()
