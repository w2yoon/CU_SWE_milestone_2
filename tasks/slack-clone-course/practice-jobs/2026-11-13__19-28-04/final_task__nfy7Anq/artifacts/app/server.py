import os,json,re,hashlib,secrets,struct,base64,threading,time,urllib.parse,mimetypes,select
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from email.parser import BytesParser
from email.policy import default
from core import *
NODE=int(os.getenv('NODE_ID','0')); PORT=int(os.getenv('PORT','8000'))
def sendws(sock,obj):
 raw=json.dumps(obj,separators=(',',':')).encode(); n=len(raw)
 if n < 126: h=bytes([129,n])
 elif n < 65536: h=bytes([129,126])+struct.pack('>H',n)
 else: h=bytes([129,127])+struct.pack('>Q',n)
 try:sock.sendall(h+raw)
 except:pass
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a):pass
 def token(self):
  x=self.headers.get('Authorization','')
  return x[7:] if x.startswith('Bearer ') else None
 def body(self):
  n=int(self.headers.get('Content-Length','0') or 0)
  try:
   x=json.loads(self.rfile.read(n)); return x if isinstance(x,dict) else None
  except:return None
 def out(self,code,obj=None,headers=None):
  raw=b'' if obj is None else json.dumps(obj,separators=(',',':')).encode()
  self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(raw)))
  for k,v in (headers or {}).items():self.send_header(k,v)
  self.end_headers()
  if raw:self.wfile.write(raw)
 def auth(self,c):
  u=token_user(c,self.token())
  if not u:self.out(401,{'error':'unauthorized'});return None
  return u
 def route(self):return urllib.parse.urlparse(self.path).path.split('/')
 def do_GET(self):
  c=db(); p=self.route(); q=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
  if p==['','api','health']:return self.out(200,{'status':'ok','node_id':NODE})
  if len(p)>=3 and p[1]=='api' and p[2]=='ws': return self.websocket(c)
  u=self.auth(c)
  if not u:return
  try:
   if p[2]=='auth' and p[3]=='me':return self.out(200,{'user':user(u)})
   if p[2]=='users':
    r=c.execute('SELECT * FROM users WHERE id=?',(p[3],)).fetchone()
    return self.out(200,{'user':user(r)}) if r else self.out(404,{'error':'not found'})
   if p[2]=='workspaces':
    if len(p)==3:return self.out(200,{'workspaces':[ws(x) for x in c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],))]})
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
    if len(p)>=5 and p[4]=='groups':
     if not w:return self.out(404,{'error':'not found'})
     if not wsrole(c,w['id'],u['id']):return self.out(403,{'error':'forbidden'})
     return self.out(200,{'groups':[group_obj(c,r) for r in c.execute('SELECT * FROM groups_ WHERE workspace_id=? ORDER BY id',(w['id'],))]})
    if not w:return self.out(404,{'error':'not found'})
    if not wsrole(c,w['id'],u['id']):return self.out(403,{'error':'forbidden'})
    cs=[ch(x) for x in c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_dm=0 AND (is_archived=0 OR ?)',(w['id'],q.get('include_archived',['0'])[0] in ('1','true')))]
    return self.out(200,{'workspace':ws(w),'channels':cs,'read_state':[]})
   if p[2]=='channels':
    cid=int(p[3]); x=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    if not x:return self.out(404,{'error':'not found'})
    if not member(c,cid,u['id']):return self.out(403,{'error':'forbidden'})
    if len(p)>=5 and p[4]=='messages':
     rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 ORDER BY id DESC',(cid,)).fetchall()
     return self.out(200,{'messages':[msg(c,r) for r in rows],'next_cursor':None})
    if len(p)>=5 and p[4]=='members':
     return self.out(200,{'members':[user(c.execute('SELECT * FROM users WHERE id=?',(x['user_id'],)).fetchone()) for x in c.execute('SELECT user_id FROM cmembers WHERE channel_id=?',(cid,))]})
    if len(p)>=5 and p[4]=='pins':return self.out(200,{'pins':[],'next_cursor':None})
   if p[2]=='messages':
    mid=int(p[3]); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    if not r:return self.out(404,{'error':'not found'})
    if not member(c,r['channel_id'],u['id']):return self.out(403,{'error':'forbidden'})
    if len(p)>=5 and p[4]=='replies':
     z=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(mid,)).fetchall()
     return self.out(200,{'replies':[msg(c,x) for x in z],'next_cursor':None})
    if len(p)>=5 and p[4]=='reactions':return self.out(200,{'reactions':reactions(c,mid)})
   if p[2]=='files':
    fid=int(p[3]); r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
    if not r:return self.out(404,{'error':'not found'})
    if len(p)>4 and p[4]=='download':
     data=open(os.path.join(DATA,str(fid)),'rb').read(); return self.out(200,None,{'Content-Type':r['content_type'],'Content-Disposition':'attachment; filename="'+r['filename']+'"','Content-Length':str(len(data))}) if False else self.raw(data,r['content_type'],r['filename'])
    return self.out(200,{'file':fileobj(r)})
   if p[2]=='search':
    term=q.get('q',[''])[0]; rows=c.execute("SELECT m.* FROM messages m JOIN cmembers cm ON cm.channel_id=m.channel_id WHERE cm.user_id=? AND m.body LIKE ? AND m.deleted=0 ORDER BY m.id DESC",(u['id'],'%'+term+'%')).fetchall()
    return self.out(200,{'results':[msg(c,x) for x in rows],'next_cursor':None})
  except Exception as e:self.out(400,{'error':str(e)})
 def raw(self,data,typ,name):
  self.send_response(200);self.send_header('Content-Type',typ);self.send_header('Content-Disposition','attachment; filename="'+name+'"');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def upload_file(self,c,u):
  try:
   n=int(self.headers.get('Content-Length','0') or 0)
   if n>10485760:
    self.rfile.read(n)
    return self.out(413,{'error':'too large'})
   raw=self.rfile.read(n)
   ctype=self.headers.get('Content-Type','')
   if 'multipart/' not in ctype:return self.out(400,{'error':'file required'})
   msgdata=(b'Content-Type: '+ctype.encode()+b'\r\nMIME-Version: 1.0\r\n\r\n'+raw)
   root=BytesParser(policy=default).parsebytes(msgdata)
   part=None
   for x in root.walk():
    if (not x.is_multipart()) and x.get_content_disposition()=='form-data' and x.get_param('name',header='content-disposition')=='file':
     part=x;break
   if part is None:return self.out(400,{'error':'file required'})
   data=part.get_payload(decode=True) or b''
   filename=part.get_filename() or 'file'
   typ=part.get_content_type() or 'application/octet-stream'
   c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],filename,typ,len(data),now()))
   fid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
   with open(os.path.join(DATA,str(fid)),'wb') as f:f.write(data)
   c.commit()
   return self.out(201,{'file':fileobj(c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone())})
  except Exception as e:return self.out(400,{'error':str(e)})
 def slash(self,c,u,x,b):
  if not isinstance(b,str) or not b.startswith('/') or b.startswith('//'): return None
  z=b[1:].split(None,1); name=z[0] if z else ''; rest=z[1] if len(z)>1 else ''
  if name=='me':
   if not rest.strip(): return ('error',400,{'error':'text'})
   return ('body','/'+b[1:])
  if name=='shrug':
   return ('body',(rest.strip()+' ¯\\_(ツ)_/¯').strip())
  w=c.execute('SELECT * FROM workspaces WHERE id=?',(x['workspace_id'],)).fetchone()
  role=wsrole(c,w['id'],u['id'])
  if name=='topic':
   if len(rest)>250:return ('error',400,{'error':'topic'})
   c.execute('UPDATE channels SET topic=? WHERE id=?',(rest,x['id']))
   y=ch(c.execute('SELECT * FROM channels WHERE id=?',(x['id'],)).fetchone()); emit(c,x['id'],'channel.updated',{'channel':y}); c.commit()
   return ('result',201,{'message':None,'channel':y})
  if name=='invite':
   if not rest.startswith('@') or not rest[1:]:return ('error',400,{'error':'user'})
   v=c.execute('SELECT * FROM users WHERE username=?',(rest[1:].strip(),)).fetchone()
   if not v:return ('error',404,{'error':'user'})
   if not wsrole(c,w['id'],v['id']):return ('error',404,{'error':'user'})
   c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(x['id'],v['id']))
   y=user(v);emit(c,x['id'],'member.joined',{'user_id':v['id']});c.commit()
   return ('result',201,{'message':None,'invited_user':y})
  if name in ('archive','unarchive'):
   if role!='owner':return ('error',403,{'error':'forbidden'})
   val=1 if name=='archive' else 0
   c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,x['id']))
   y=ch(c.execute('SELECT * FROM channels WHERE id=?',(x['id'],)).fetchone());emit(c,x['id'],'channel.'+name,{'channel':y});c.commit()
   return ('result',201,{'message':None,'channel':y})
  return ('error',400,{'error':'command'})
 def do_POST(self):
  c=db();p=self.route()
  if p[:3]==['','api','auth']:
   return self.do_POST_auth(c,p,self.body() or {})
  u=self.auth(c) if p!=['','api','health'] else None
  if p==['','api','health']:return self.out(200,{'status':'ok','node_id':NODE})
  if not u:return
  if p[2]=='files': return self.upload_file(c,u)
  try:
   b=self.body() or {}
   if p[2]=='workspaces':
    if len(p)==3:
     if not re.fullmatch(r'[a-z0-9-]{2,32}',b.get('slug','')):return self.out(400,{'error':'bad'})
     if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(b['slug'],)).fetchone():return self.out(409,{'error':'exists'})
     c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b.get('name',''),u['id']));wid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
     c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'));c.execute("INSERT INTO channels(workspace_id,name) VALUES(?, 'general')",(wid,));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,u['id']));c.commit()
     return self.out(201,{'workspace':ws(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':ch(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
    if len(p)>4 and p[4]=='groups':
     w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
     if not w:return self.out(404,{'error':'not found'})
     if not wsrole(c,w['id'],u['id']):return self.out(403,{'error':'forbidden'})
     h=b.get('handle',''); name=b.get('name'); ids=b.get('user_ids',[])
     if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,31}',h) or not isinstance(name,str) or not name or not isinstance(ids,list):return self.out(400,{'error':'invalid'})
     if c.execute('SELECT 1 FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],h)).fetchone():return self.out(409,{'error':'exists'})
     c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],h,name,u['id']));gid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
     for uid in ids:
      if c.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():c.execute('INSERT OR IGNORE INTO group_members VALUES(?,?)',(gid,uid))
     c.commit();return self.out(201,{'group':group_obj(c,c.execute('SELECT * FROM groups_ WHERE id=?',(gid,)).fetchone())})
    if len(p)>4 and p[4]=='channels':
     w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
     if not w:return self.out(404,{'error':'not found'})
     role=wsrole(c,w['id'],u['id'])
     if not role:return self.out(403,{'error':'forbidden'})
     if b.get('is_private') and role not in ('owner','admin'):return self.out(403,{'error':'forbidden'})
     if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],b.get('name'))).fetchone():return self.out(409,{'error':'exists'})
     c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b.get('name',''),int(bool(b.get('is_private'))),b.get('topic','')));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,u['id']));c.commit();return self.out(201,{'channel':ch(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
   if p[2]=='channels':
    cid=int(p[3]);x=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    if not x:return self.out(404,{'error':'not found'})
    if p[4]=='join':
     if x['is_private']:return self.out(403,{'error':'private'})
     w=c.execute('SELECT * FROM workspaces WHERE id=?',(x['workspace_id'],)).fetchone()
     if not wsrole(c,w['id'],u['id']):c.execute('INSERT INTO members VALUES(?,?,?)',(w['id'],u['id'],'member'))
     c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(cid,u['id']));c.commit();return self.out(200,{'channel':ch(x)})
    if p[4]=='leave':c.execute('DELETE FROM cmembers WHERE channel_id=? AND user_id=?',(cid,u['id']));c.commit();return self.out(200,{'channel':ch(x)})
    if not member(c,cid,u['id']):return self.out(403,{'error':'forbidden'})
    if p[4]=='messages':
     body=b.get('body','')
     if not isinstance(body,str) or not body.strip():return self.out(400,{'error':'empty'})
     sr=self.slash(c,u,x,body)
     if sr:
      if sr[0]=='error':return self.out(sr[1],sr[2])
      if sr[0]=='result':return self.out(sr[1],sr[2])
      body=sr[1]
     if x['is_archived']:return self.out(423,{'error':'archived'})
     fids=b.get('file_ids',[])
     if not isinstance(fids,list):return self.out(400,{'error':'file_ids'})
     for fid in fids:
      if not isinstance(fid,int):return self.out(400,{'error':'file_ids'})
      fr=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
      if not fr or fr['uploader_id']!=u['id'] or c.execute('SELECT 1 FROM msgfiles WHERE file_id=?',(fid,)).fetchone():return self.out(400,{'error':'file'})
     if body.startswith('/shrug'):body=(body[6:].strip()+' ¯\\_(ツ)_/¯').strip()
     c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,u['id'],body,b.get('parent_id'),now()));mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
     for fid in fids:c.execute('INSERT INTO msgfiles(message_id,file_id) VALUES(?,?)',(mid,fid))
     r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();n=emit(c,cid,'message.reply' if b.get('parent_id') else 'message.created',{'message':msg(c,r)});c.commit();return self.out(201,{'message':msg(c,r)})
    if p[4] in ('archive','unarchive'):
     c.execute('UPDATE channels SET is_archived=? WHERE id=?',(1 if p[4]=='archive' else 0,cid));n=emit(c,cid,'channel.'+p[4],{'channel':ch(x)});c.commit();return self.out(200,{'channel':ch(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
   if p[2]=='messages':
    mid=int(p[3]);r=c.execute('SELECT m.*,c.is_archived FROM messages m JOIN channels c ON c.id=m.channel_id WHERE m.id=?',(mid,)).fetchone()
    if not r:return self.out(404,{'error':'not found'})
    if not member(c,r['channel_id'],u['id']):return self.out(403,{'error':'forbidden'})
    if r['is_archived']:return self.out(423,{'error':'archived'})
    if p[4]=='reactions':
     e=b.get('emoji')
     if not e:return self.out(400,{'error':'emoji'})
     if not c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],e)).fetchone():
      c.execute('INSERT INTO reactions VALUES(?,?,?)',(mid,u['id'],e));n=emit(c,r['channel_id'],'reaction.added',{'message_id':mid,'emoji':e,'user_id':u['id']});c.commit()
     return self.out(200,{'reactions':reactions(c,mid)})
   if p[2]=='dms':
    if 'user_id' not in b:return self.out(400,{'error':'user_id'})
    try: other=int(b.get('user_id'))
    except: return self.out(400,{'error':'user_id'})
    v=c.execute('SELECT id FROM users WHERE id=?',(other,)).fetchone()
    if not v:return self.out(404,{'error':'user'})
    a,z=sorted((u['id'],other));r=c.execute('SELECT channel_id FROM dms WHERE a=? AND b=?',(a,z)).fetchone()
    if r:return self.out(200,{'channel_id':r['channel_id']})
    c.execute('INSERT INTO channels(is_private,is_dm,name) VALUES(1,1,?)',('dm-'+str(a)+'-'+str(z),));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO dms VALUES(?,?,?)',(a,z,cid));c.execute('INSERT INTO cmembers(channel_id,user_id) VALUES(?,?)',(cid,a));c.execute('INSERT INTO cmembers(channel_id,user_id) VALUES(?,?)',(cid,z));c.commit();return self.out(200,{'channel_id':cid})
  except Exception as e:self.out(400,{'error':str(e)})
 def do_DELETE(self):
  c=db();p=self.route();u=self.auth(c)
  if not u:return
  try:
   if p[2]=='messages' and len(p)>=6 and p[4]=='reactions':
    mid=int(p[3]);r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    if not r:return self.out(404,{'error':'not found'})
    if not member(c,r['channel_id'],u['id']):return self.out(403,{'error':'forbidden'})
    e=urllib.parse.unquote(p[5]);c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],e))
    if c.total_changes:
     emit(c,r['channel_id'],'reaction.removed',{'message_id':mid,'emoji':e,'user_id':u['id']});c.commit()
    return self.out(200,{'reactions':reactions(c,mid)})
   if len(p)>=6 and p[2]=='workspaces' and p[4]=='groups':
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
    if not w:return self.out(404,{'error':'not found'})
    g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],p[5])).fetchone()
    if not g:return self.out(200,{'deleted':True})
    if g['creator_id']!=u['id'] and wsrole(c,w['id'],u['id'])!='owner':return self.out(403,{'error':'forbidden'})
    c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],));c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],));c.commit();return self.out(200,{'deleted':True})
  except: return self.out(404,{'error':'not found'})
 def do_PATCH(self):
  c=db();p=self.route();u=self.auth(c)
  if not u:return
  b=self.body() or {}
  try:
   if len(p)>=6 and p[2]=='workspaces' and p[4]=='groups':
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
    if not w:return self.out(404,{'error':'not found'})
    g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],p[5])).fetchone()
    if not g:return self.out(404,{'error':'not found'})
    if g['creator_id']!=u['id'] and wsrole(c,w['id'],u['id'])!='owner':return self.out(403,{'error':'forbidden'})
    if 'name' in b:
     if not isinstance(b['name'],str) or not b['name']:return self.out(400,{'error':'name'})
     c.execute('UPDATE groups_ SET name=? WHERE id=?',(b['name'],g['id']))
    if 'user_ids' in b:
     if not isinstance(b['user_ids'],list):return self.out(400,{'error':'user_ids'})
     c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
     for uid in b['user_ids']:
      if c.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():c.execute('INSERT OR IGNORE INTO group_members VALUES(?,?)',(g['id'],uid))
    c.commit();return self.out(200,{'group':group_obj(c,c.execute('SELECT * FROM groups_ WHERE id=?',(g['id'],)).fetchone())})
   if p[2]=='users' and p[3]=='me':
    allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}
    if any(k not in allowed or not isinstance(v,str) for k,v in b.items()):return self.out(400,{'error':'invalid'})
    if 'display_name' in b and (not b['display_name'] or len(b['display_name'])>80):return self.out(400,{'error':'display_name'})
    if 'status_text' in b and len(b['status_text'])>140:return self.out(400,{'error':'status_text'})
    if 'status_emoji' in b and len(b['status_emoji'])>64:return self.out(400,{'error':'status_emoji'})
    if 'avatar_url' in b and len(b['avatar_url'])>512:return self.out(400,{'error':'avatar_url'})
    if 'timezone' in b:
     try:
      from zoneinfo import ZoneInfo
      ZoneInfo(b['timezone'])
     except: return self.out(400,{'error':'timezone'})
    if b:
     c.execute("INSERT INTO global_events(payload) VALUES(?)",(json.dumps({'type':'user.updated','user':user(u)}),))
     vals=list(b.values())+[u['id']];c.execute('UPDATE users SET '+','.join(k+'=?' for k in b)+' WHERE id=?',vals); nu=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone(); c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps({'type':'user.updated','user':user(nu)}),)); c.commit()
    return self.out(200,{'user':user(c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone())})
   if p[2]=='channels':
    cid=int(p[3]);r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    if not r:return self.out(404,{'error':'not found'})
    if not member(c,cid,u['id']):return self.out(403,{'error':'forbidden'})
    if 'topic' not in b or not isinstance(b['topic'],str) or len(b['topic'])>250:return self.out(400,{'error':'topic'})
    c.execute('UPDATE channels SET topic=? WHERE id=?',(b['topic'],cid));emit(c,cid,'channel.updated',{});c.commit()
    return self.out(200,{'channel':ch(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
   if p[2]=='messages':
    mid=int(p[3]);r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    if not r:return self.out(404,{'error':'not found'})
    if r['author_id']!=u['id']:return self.out(403,{'error':'forbidden'})
    if not isinstance(b.get('body'),str) or not b['body'].strip():return self.out(400,{'error':'body'})
    c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(b['body'],now(),mid));r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();emit(c,r['channel_id'],'message.updated',{'message':msg(c,r)});c.commit()
    return self.out(200,{'message':msg(c,r)})
  except Exception as e:return self.out(400,{'error':str(e)})
 def do_POST_auth(self,c,p,b):
  if p[3]=='register':
   un=b.get('username','');pw=b.get('password','')
   if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(pw)<8:return self.out(400,{'error':'invalid'})
   if c.execute('SELECT 1 FROM users WHERE username=?',(un,)).fetchone():return self.out(409,{'error':'exists'})
   c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(un,hashlib.sha256(pw.encode()).hexdigest(),b.get('display_name',un)));uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
  elif p[3]=='login':
   r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(b.get('username'),hashlib.sha256(b.get('password','').encode()).hexdigest())).fetchone()
   if not r:return self.out(401,{'error':'invalid'})
   uid=r['id']
  else:return self.out(404,{'error':'not found'})
  tok=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(tok,uid));c.commit()
  return self.out(201 if p[3]=='register' else 200,{'user':user(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()),'token':tok})
 def websocket(self,c):
  t=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('token',[''])[0]
  wu=token_user(c,t)
  if not wu:return self.out(401,{'error':'unauthorized'})
  conn_id=secrets.token_hex(16); c.execute('DELETE FROM presence WHERE node_id=?',(NODE,)); c.execute('INSERT INTO presence VALUES(?,?,?)',(conn_id,wu['id'],NODE)); c.commit()
  global_last=c.execute('SELECT COALESCE(MAX(id),0) FROM global_events').fetchone()[0]
  key=self.headers.get('Sec-WebSocket-Key','');import hashlib as hh
  acc=base64.b64encode(hh.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',acc);self.end_headers();self.close_connection=False
  self.connection.settimeout(0.2); chans=set(); last={}
  def readn(n):
   b=b''
   while len(b)<n:
    x=self.connection.recv(n-len(b))
    if not x: raise OSError('closed')
    b+=x
   return b
  try:
   while True:
    c2=db()
    for gr in c2.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(global_last,)):
     sendws(self.connection,json.loads(gr['payload'])); global_last=gr['id']
    for cid in list(chans):
     for r in c2.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last.get(cid,0))):
      z=json.loads(r['payload_json']);z.update(type=r['type'],event_id=r['event_id'],channel_id=cid);sendws(self.connection,z);last[cid]=r['event_id']
    if not select.select([self.connection],[],[],0.05)[0]:
     continue
    try:
     h=readn(2)
     if not h:break
     n=h[1]&127; masked=h[1]&128
     if n==126:n=struct.unpack('>H',readn(2))[0]
     elif n==127:n=struct.unpack('>Q',readn(8))[0]
     mask=readn(4) if masked else b'';data=readn(n)
     if masked:data=bytes(x^mask[i%4] for i,x in enumerate(data))
     o=json.loads(data)
     if o.get('type')=='subscribe':
      cid=int(o['channel_id']);chans.add(cid);hr=c2.execute('SELECT COALESCE(last_event_id,0) FROM channel_seq WHERE channel_id=?',(cid,)).fetchone();head=(hr[0] if hr else 0);last[cid]=head;sendws(self.connection,{'type':'subscribed','channel_id':cid,'head_event_id':head})
     elif o.get('type')=='resume':
      cid=int(o['channel_id']);chans.add(cid);last[cid]=int(o.get('since_event_id',0));sendws(self.connection,{'type':'resumed','channel_id':cid,'head_event_id':last[cid]})
    except (TimeoutError,BlockingIOError):pass
  except Exception as ex:
   import traceback
   traceback.print_exc()
  finally:
   try:
    c3=db();c3.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,));c3.commit()
   except:pass
if __name__=='__main__':
 init(db());ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
