import os,json,re,base64,hashlib,struct,socket,threading,urllib.parse, mimetypes,select, email.parser,uuid
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from core import *
init(); NODE=int(os.getenv('NODE_ID','0')); PORT=int(os.getenv('PORT','8000'))
def body(h):
 try:
  n=int(h.headers.get('Content-Length','0')); return json.loads(h.rfile.read(n) or b'{}')
 except: return {}
def out(h,code,obj,headers=None):
 b=json.dumps(obj,ensure_ascii=False).encode(); h.send_response(code); h.send_header('Content-Type','application/json'); h.send_header('Content-Length',str(len(b))); 
 for k,v in (headers or {}).items():h.send_header(k,v)
 h.end_headers(); h.wfile.write(b)
def tok(h): return (h.headers.get('Authorization','').split(' ',1)+[None])[1]
class H(BaseHTTPRequestHandler):
 def log_message(self,*a): pass
 def do_GET(self): self.route('GET')
 def do_POST(self): self.route('POST')
 def do_PATCH(self): self.route('PATCH')
 def do_DELETE(self): self.route('DELETE')
 def route(self,method):
  p=urllib.parse.urlparse(self.path); path=p.path; q=urllib.parse.parse_qs(p.query); c=db()
  if path=='/api/health': return out(self,200,{'status':'ok','node_id':NODE})
  t=tok(self) or urllib.parse.parse_qs(p.query).get('token',[None])[0]; u=auth(c,t)
  public=path in ('/api/auth/register','/api/auth/login') or path=='/api/health'
  if not u and not public:
   if path.startswith('/api/ws'): return self.send_error(401)
   return out(self,401,{'error':'unauthorized'})
  try:
   if path=='/api/auth/register' and method=='POST':
    x=body(self); un=x.get('username',''); pw=x.get('password','')
    if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(pw)<8:return out(self,400,{'error':'invalid'})
    try:c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(un,hashlib.sha256(pw.encode()).hexdigest(),x.get('display_name') or un))
    except sqlite3.IntegrityError:return out(self,409,{'error':'duplicate'})
    uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; token=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(token,uid));c.commit()
    return out(self,201,{'user':user(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()),'token':token})
   if path=='/api/auth/login' and method=='POST':
    x=body(self); r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(x.get('username'),hashlib.sha256(x.get('password','').encode()).hexdigest())).fetchone()
    if not r:return out(self,401,{'error':'invalid'})
    token=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(token,r['id']));c.commit();return out(self,200,{'user':user(r),'token':token})
   if path=='/api/auth/me':return out(self,200,{'user':user(u)})
   if path=='/api/users/me' and method=='PATCH':
    x=body(self); allowed={'display_name':80,'status_text':140,'status_emoji':64,'avatar_url':512}
    for k,v in x.items():
     if k=='timezone':
      try: __import__('zoneinfo').ZoneInfo(v)
      except: return out(self,400,{'error':'timezone'})
     elif k in allowed and (not isinstance(v,str) or (k=='display_name' and not v) or len(v)>allowed[k]): return out(self,400,{'error':'invalid'})
     elif k not in allowed: return out(self,400,{'error':'invalid'})
    if x:
     sets=','.join(k+'=?' for k in x); c.execute('UPDATE users SET '+sets+' WHERE id=?',(*x.values(),u['id']))
    r=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
    c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps({'type':'user.updated','user':user(r)}),)); c.commit()
    return out(self,200,{'user':user(r)})
   m=re.fullmatch(r'/api/users/(\d+)',path)
   if m:
    r=c.execute('SELECT * FROM users WHERE id=?',(m.group(1),)).fetchone()
    if not r:return out(self,404,{'error':'not found'})
    if method=='PATCH' and int(m.group(1))==u['id']:
     x=body(self); allowed={'display_name':80,'status_text':140,'status_emoji':64,'avatar_url':512}
     for k,v in x.items():
      if k=='timezone':
       try: __import__('zoneinfo').ZoneInfo(v)
       except:return out(self,400,{'error':'timezone'})
      elif k in allowed and (not isinstance(v,str) or not v or len(v)>allowed[k]):return out(self,400,{'error':'invalid'})
      elif k not in allowed:return out(self,400,{'error':'invalid'})
     sets=','.join(k+'=?' for k in x);c.execute('UPDATE users SET '+sets+' WHERE id=?',(*x.values(),u['id']));c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps({'type':'user.updated','user':user(c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone())}),));c.commit();r=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
    return out(self,200,{'user':user(r)})
   m=re.fullmatch(r'/api/workspaces/([^/]+)(?:/(.*))?',path)
   if m:
    slug,rest=m.group(1),m.group(2); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
    if not w:return out(self,404,{'error':'not found'})
    wm=wmember(c,w['id'],u['id'])
    if method=='GET' and not rest:
     chans=[chobj(x) for x in c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_dm=0 AND is_archived=0',(w['id'],)) if member(c,x['id'],u['id'])]
     return out(self,200,{'workspace':dict(w),'channels':chans,'read_state':[]})
    if rest=='members' and method=='GET':return out(self,200,{'members':[dict(x) for x in c.execute('SELECT user_id,role FROM wm WHERE workspace_id=?',(w['id'],))]})
    if rest=='groups':
     if method=='GET':return out(self,200,{'groups':[group(c,x) for x in c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],))]})
     if method=='POST':
      x=body(self)
      if not wm:return out(self,403,{'error':'forbidden'})
      if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,31}',x.get('handle','')) or not x.get('name'):return out(self,400,{'error':'invalid'})
      try:c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],x['handle'],x['name'],u['id']))
      except sqlite3.IntegrityError:return out(self,409,{'error':'duplicate'})
      gid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
      c.execute('DELETE FROM group_users WHERE group_id=?',(gid,))
      for z in x.get('user_ids',[]):c.execute('INSERT OR IGNORE INTO group_users VALUES(?,?)',(gid,z))
      c.commit();return out(self,201,{'group':group(c,c.execute('SELECT * FROM groups_ WHERE id=?',(gid,)).fetchone())})
    if rest.startswith('groups/') :
     h=rest.split('/')[1];g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],h)).fetchone()
     if method=='DELETE':
      if g and g['creator_id']!=u['id'] and u['id']!=w['owner_id']:return out(self,403,{'error':'forbidden'})
      if g:
       c.execute('DELETE FROM group_users WHERE group_id=?',(g['id'],)); c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],)); c.commit()
      return out(self,200,{'deleted':True})
     if not g:return out(self,404,{'error':'not found'})
     if g['creator_id']!=u['id'] and u['id']!=w['owner_id']:return out(self,403,{'error':'forbidden'})
     x=body(self);c.execute('UPDATE groups_ SET name=? WHERE id=?',(x.get('name',g['name']),g['id']))
     if 'user_ids' in x:c.execute('DELETE FROM group_users WHERE group_id=?',(g['id'],));[c.execute('INSERT INTO group_users VALUES(?,?)',(g['id'],z)) for z in x['user_ids']]
     c.commit();return out(self,200,{'group':group(c,c.execute('SELECT * FROM groups_ WHERE id=?',(g['id'],)).fetchone())})
    if rest=='channels' and method=='POST':
     x=body(self); role=wm['role'] if wm else None
     if not wm or (x.get('is_private') and role not in ('owner','admin')):return out(self,403,{'error':'forbidden'})
     if not re.fullmatch(r'[a-z0-9-]{1,32}',x.get('name','')):return out(self,400,{'error':'invalid'})
     try:c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],x['name'],int(bool(x.get('is_private'))),x.get('topic','')))
     except sqlite3.IntegrityError:return out(self,409,{'error':'duplicate'})
     ch=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO cm VALUES(?,?)',(ch,u['id']));c.execute('INSERT INTO channel_seq VALUES(?,0)',(ch,));c.commit();return out(self,201,{'channel':chobj(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())})
   if path=='/api/workspaces' and method=='GET':return out(self,200,{'workspaces':[dict(x) for x in c.execute('SELECT w.* FROM workspaces w JOIN wm ON wm.workspace_id=w.id WHERE wm.user_id=?',(u['id'],))]})
   if path=='/api/workspaces' and method=='POST':
    x=body(self)
    if not re.fullmatch(r'[a-z0-9-]{2,32}',x.get('slug','')):return out(self,400,{'error':'invalid'})
    try:c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(x['slug'],x['name'],u['id']))
    except sqlite3.IntegrityError:return out(self,409,{'error':'duplicate'})
    wid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO wm VALUES(?,?,?)',(wid,u['id'],'owner'));c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));ch=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO cm VALUES(?,?)',(ch,u['id']));c.execute('INSERT INTO channel_seq VALUES(?,0)',(ch,));c.commit();return out(self,201,{'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':chobj(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())})
   self.api_channel(c,u,method,path,q)
  except Exception as e: out(self,500,{'error':str(e)})
 def api_channel(self,c,u,method,path,q):
  fm=re.fullmatch(r'/api/files/(\d+)(?:/(download))?',path)
  if path=='/api/files' and method=='POST':
   n=int(self.headers.get('Content-Length','0'))
   if n>10485760: self.rfile.read(n); return out(self,413,{'error':'large'})
   raw=self.rfile.read(n); ct=self.headers.get('Content-Type','')
   if 'multipart/form-data' not in ct:return out(self,400,{'error':'file required'})
   mime=email.parser.BytesParser().parsebytes(('Content-Type: '+ct+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+raw)
   part=next((x for x in mime.walk() if x is not mime and x.get_content_disposition()=='form-data' and 'name="file"' in x.get('Content-Disposition','') and x.get_filename()),None)
   if not part:return out(self,400,{'error':'file required'})
   data=part.get_payload(decode=True) or b''; fid=secrets.randbelow(2000000000)+1
   while c.execute('SELECT 1 FROM files WHERE id=?',(fid,)).fetchone(): fid=secrets.randbelow(2000000000)+1
   fn=part.get_filename(); typ=part.get_content_type()
   open('/app/data/files/'+str(fid),'wb').write(data)
   c.execute('INSERT INTO files VALUES(?,?,?,?,?,?)',(fid,u['id'],fn,typ,len(data),now()));c.commit()
   return out(self,201,{'file':dict(c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone())})
  if fm:
   r=c.execute('SELECT * FROM files WHERE id=?',(fm.group(1),)).fetchone()
   if not r:return out(self,404,{'error':'not found'})
   if fm.group(2):
    data=open('/app/data/files/'+str(r['id']),'rb').read()
    self.send_response(200); self.send_header('Content-Type',r['content_type']); self.send_header('Content-Disposition','attachment; filename="'+r['filename']+'"'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
   return out(self,200,{'file':dict(r)})
  m=re.fullmatch(r'/api/channels/(\d+)(?:/(.*))?',path); mid=re.fullmatch(r'/api/messages/(\d+)(?:/(.*))?',path)
  if mid:
   r=c.execute('SELECT * FROM messages WHERE id=?',(mid.group(1),)).fetchone()
   if not r:return out(self,404,{'error':'not found'})
   ch=channel(c,r['channel_id'])
   if not member(c,ch['id'],u['id']):return out(self,403,{'error':'forbidden'})
   tail=mid.group(2)
   if tail=='reactions' and method=='POST':
    x=body(self)
    if not x.get('emoji'):return out(self,400,{'error':'invalid'})
    if ch['is_archived']:return out(self,423,{'error':'archived'})
    before=c.total_changes; c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(r['id'],u['id'],x['emoji'])); changed=c.total_changes>before; c.commit()
    if changed: emit(c,ch['id'],'reaction.added',{'message_id':r['id'],'emoji':x['emoji'],'user_id':u['id']})
    return out(self,200,{'reactions':reactions(c,r['id'])})
   if tail and tail.startswith('reactions/') and method=='DELETE':
    emoji=urllib.parse.unquote(tail[10:]); before=c.total_changes; c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(r['id'],u['id'],emoji)); changed=c.total_changes>before; c.commit()
    if changed: emit(c,ch['id'],'reaction.removed',{'message_id':r['id'],'emoji':emoji,'user_id':u['id']})
    return out(self,200,{'reactions':reactions(c,r['id'])})
   if tail=='replies':return out(self,200,{'replies':[msg(c,x) for x in c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(r['id'],))],'next_cursor':None})
  if m:
   ch=channel(c,m.group(1))
   if not ch:return out(self,404,{'error':'not found'})
   tail=m.group(2)
   if tail=='messages' and method=='GET':
    if not member(c,ch['id'],u['id']):return out(self,403,{'error':'forbidden'})
    return out(self,200,{'messages':[msg(c,x) for x in c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 ORDER BY id DESC',(ch['id'],))],'next_cursor':None})
   if tail=='messages' and method=='POST':
    if not member(c,ch['id'],u['id']):return out(self,403,{'error':'forbidden'})
    x=body(self); b=x.get('body','')
    if not b.strip():return out(self,400,{'error':'empty'})
    if b.startswith('/') and not b.startswith('//'):
     parts=b.split(None,1); cmd=parts[0][1:]; arg=parts[1].strip() if len(parts)>1 else ''
     if cmd=='me':
      if not arg:return out(self,400,{'error':'invalid'})
     elif cmd=='shrug':
      b=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
     elif cmd=='topic':
      if not arg or len(arg)>250:return out(self,400,{'error':'invalid'})
      c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,ch['id']));c.commit()
      return out(self,201,{'message':None,'channel':chobj(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone())})
     elif cmd=='invite':
      target=arg[1:] if arg.startswith('@') else arg
      ruser=c.execute('SELECT * FROM users WHERE username=?',(target,)).fetchone()
      if not ruser:return out(self,404,{'error':'not found'})
      c.execute('INSERT OR IGNORE INTO cm VALUES(?,?)',(ch['id'],ruser['id']));c.commit()
      return out(self,201,{'message':None,'invited_user':user(ruser)})
     elif cmd in ('archive','unarchive'):
      w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
      if w['owner_id']!=u['id']:return out(self,403,{'error':'forbidden'})
      v=cmd=='archive';c.execute('UPDATE channels SET is_archived=? WHERE id=?',(v,ch['id']));c.commit()
      return out(self,201,{'message':None,'channel':chobj(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone())})
     else:return out(self,400,{'error':'unknown command'})
    if ch['is_archived']:return out(self,423,{'error':'archived'})
    fids=x.get('file_ids',[])
    if not isinstance(fids,list):return out(self,400,{'error':'file_ids'})
    for fid in fids:
     if not isinstance(fid,int):return out(self,400,{'error':'file_ids'})
     fr=c.execute('SELECT * FROM files WHERE id=? AND uploader_id=?',(fid,u['id'])).fetchone()
     if not fr or c.execute('SELECT 1 FROM message_files WHERE file_id=?',(fid,)).fetchone():return out(self,400,{'error':'file'})
    c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch['id'],u['id'],b,x.get('parent_id'),now()));i=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    for fid in fids:c.execute('INSERT INTO message_files VALUES(?,?)',(i,fid))
    c.commit();r=c.execute('SELECT * FROM messages WHERE id=?',(i,)).fetchone(); emit(c,ch['id'],'message.reply' if x.get('parent_id') else 'message.created',{'message':msg(c,r)});return out(self,201,{'message':msg(c,r)})
   if tail=='join' and method=='POST':
    c.execute('INSERT OR IGNORE INTO cm VALUES(?,?)',(ch['id'],u['id']));c.execute('INSERT OR IGNORE INTO wm SELECT workspace_id,?,\'member\' FROM channels WHERE id=?',(u['id'],ch['id']));c.commit();return out(self,200,{'channel':chobj(ch)})
   if tail=='leave' and method=='POST':c.execute('DELETE FROM cm WHERE channel_id=? AND user_id=?',(ch['id'],u['id']));c.commit();return out(self,200,{'channel':chobj(ch)})
   if tail=='members' and method=='GET':return out(self,200,{'members':[user(x) for x in c.execute('SELECT u.* FROM users u JOIN cm ON cm.user_id=u.id WHERE cm.channel_id=?',(ch['id'],))]})
   if tail in ('archive','unarchive') and method=='POST':
    w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
    if w['owner_id']!=u['id']:return out(self,403,{'error':'forbidden'})
    v=tail=='archive';c.execute('UPDATE channels SET is_archived=? WHERE id=?',(v,ch['id']));c.commit();return out(self,200,{'channel':chobj(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone())})
   if method=='PATCH':
    x=body(self);c.execute('UPDATE channels SET topic=? WHERE id=?',(x.get('topic',''),ch['id']));c.commit();return out(self,200,{'channel':chobj(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone())})
  if path=='/api/dms' and method=='POST':
   x=body(self); b=x.get('user_id'); z=c.execute('SELECT id FROM users WHERE id=?',(b,)).fetchone()
   if not b:return out(self,400,{'error':'invalid'})
   if not z:return out(self,404,{'error':'not found'})
   a,bb=sorted((u['id'],b));r=c.execute('SELECT channel_id FROM dm WHERE a=? AND b=?',(a,bb)).fetchone()
   if r:return out(self,200,{'channel_id':r[0]})
   c.execute('INSERT INTO channels(is_private,is_dm,name) VALUES(1,1,?)',(f'dm-{a}-{bb}',));i=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO dm VALUES(?,?,?)',(a,bb,i));c.execute('INSERT INTO cm VALUES(?,?),(?,?)',(i,a,i,bb));c.execute('INSERT INTO channel_seq VALUES(?,0)',(i,));c.commit();return out(self,200,{'channel_id':i})
  if path=='/api/files' and method=='POST':
   n=int(self.headers.get('Content-Length','0'))
   if n>10485760: self.rfile.read(n); return out(self,413,{'error':'large'})
   data=self.rfile.read(n); ct=self.headers.get('Content-Type',''); fn='upload'
   z=re.search(r'filename="([^"]+)',ct); 
   fid=secrets.randbelow(2**31);open(f'/app/data/files/{fid}','wb').write(data);c.execute('INSERT INTO files VALUES(?,?,?,?,?,?)',(fid,u['id'],fn,self.headers.get('Content-Type','application/octet-stream'),len(data),now()));c.commit();return out(self,201,{'file':dict(c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone())})
  if path=='/api/ws':return self.websocket(c,u)
  return out(self,404,{'error':'not found'})
 def websocket(self,c,u):
  key=self.headers.get('Sec-WebSocket-Key')
  self.send_response(101); self.send_header('Upgrade','websocket'); self.send_header('Connection','websocket'); self.send_header('Sec-WebSocket-Accept',base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()); self.end_headers()
  conn_id=uuid.uuid4().hex; c.execute('INSERT OR REPLACE INTO presence(conn_id,user_id,node_id) VALUES(?,?,?)',(conn_id,u['id'],NODE)); c.commit()
  subscribed={}; global_last=c.execute('SELECT COALESCE(MAX(id),0) FROM global_events').fetchone()[0]; self.connection.setblocking(False)
  while True:
   try:
    ready,_,_=select.select([self.connection],[],[],0.05)
    if ready:
     h=self.connection.recv(2)
     if not h: break
     n=h[1]&127
     if n==126: n=struct.unpack('>H',self.connection.recv(2))[0]
     elif n==127: n=struct.unpack('>Q',self.connection.recv(8))[0]
     mask=self.connection.recv(4); data=self.connection.recv(n)
     x=json.loads(bytes(v^mask[i%4] for i,v in enumerate(data)))
     ch=x.get('channel_id')
     if x.get('type')=='subscribe':
      row=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(ch,)).fetchone(); head=row[0] if row else 0
      subscribed[ch]=head; self.ws_send({'type':'subscribed','channel_id':ch,'head_event_id':head})
     elif x.get('type')=='resume':
      since=x.get('since_event_id',0)
      for row in c.execute('SELECT payload_json FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since)): self.ws_send(json.loads(row[0]))
      row=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(ch,)).fetchone(); head=row[0] if row else 0
      subscribed[ch]=head; self.ws_send({'type':'resumed','channel_id':ch,'head_event_id':head})
    for ch,last in list(subscribed.items()):
     for row in c.execute('SELECT event_id,payload_json FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,last)):
      self.ws_send(json.loads(row[1])); subscribed[ch]=row[0]
    for row in c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(global_last,)):
     self.ws_send(json.loads(row[1])); global_last=row[0]
   except (BlockingIOError,TimeoutError): pass
   except Exception: break
  c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,)); c.commit()
 def ws_send(self,x):
  data=json.dumps(x,ensure_ascii=False).encode(); n=len(data)
  if n<126: head=bytes([129,n])
  elif n<65536: head=bytes([129,126])+struct.pack('>H',n)
  else: head=bytes([129,127])+struct.pack('>Q',n)
  self.connection.sendall(head+data)

ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
