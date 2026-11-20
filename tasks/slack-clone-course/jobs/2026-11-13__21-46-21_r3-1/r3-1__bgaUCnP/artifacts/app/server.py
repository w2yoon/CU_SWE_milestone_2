import os,json,re,base64,hashlib,struct,threading,time,urllib.parse,socket
from zoneinfo import ZoneInfo
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from core import *
PORT=int(os.environ.get('PORT','8000')); NODE=int(os.environ.get('NODE_ID','0'))
clients=[]; clients_lock=threading.Lock()
def groupobj(c,r):
 return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':[x['user_id'] for x in c.execute('select user_id from groupmembers where group_id=?',(r['id'],))]}
def mentions_for(c,ch,author,body):
 out=[]
 for un in re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)',body):
  x=c.execute('select id from users where username=?',(un,)).fetchone()
  if x and x['id']!=author and member(c,ch,x['id']): out.append(x['id'])
  g=c.execute('select g.id from groupsx g where g.workspace_id=(select workspace_id from channels where id=?) and g.handle=?',(ch,un)).fetchone()
  if g:
   out += [x['user_id'] for x in c.execute('select user_id from groupmembers where group_id=?',(g['id'],)) if x['user_id']!=author and member(c,ch,x['user_id'])]
 if '@channel' in body:
  out += [x['user_id'] for x in c.execute('select user_id from cmembers where channel_id=?',(ch,)) if x['user_id']!=author]
 return list(dict.fromkeys(out))
def token(h):
 return (h.get('Authorization','').split(' ',1)[1] if h.get('Authorization','').startswith('Bearer ') else None)
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a): pass
 def sendj(self,code,obj,extra=None):
  b=json.dumps(obj,separators=(',',':')).encode(); self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)))
  for k,v in (extra or {}).items():self.send_header(k,v)
  self.end_headers();self.wfile.write(b)
 def body(self):
  n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or b'{}')
 def req(self,method,path):
  c=db(); u=auth(c,token(self.headers))
  if not u:return c,None,('401',{'error':'unauthorized'})
  return c,u,None
 def err(self,code,msg='error'): self.sendj(code,{'error':msg})
 def userobj(self,r): return user(r)
 def do_GET(self):
  p=urllib.parse.urlparse(self.path); path=p.path; q=urllib.parse.parse_qs(p.query)
  if path=='/': return self.html()
  if path=='/api/health': return self.sendj(200,{'status':'ok','node_id':NODE})
  if path.startswith('/api/ws'): return self.websocket(q)
  c,u,e=self.req('GET',path)
  if e:return self.err(int(e[0]))
  try:
   m=re.match(r'^/api/auth/me$',path)
   if m:return self.sendj(200,{'user':user(u)})
   m=re.match(r'^/api/users/(\d+)$',path)
   if m:
    r=c.execute('select * from users where id=?',(int(m[1]),)).fetchone()
    return self.sendj(200,{'user':user(r)}) if r else self.err(404)
   if path=='/api/workspaces':
    rows=c.execute('select w.* from workspaces w join members m on m.workspace_id=w.id where m.user_id=?',(u['id'],)).fetchall()
    return self.sendj(200,{'workspaces':[dict(x) for x in rows]})
   m=re.match(r'^/api/workspaces/([^/]+)$',path)
   if m:
    w=c.execute('select * from workspaces where slug=?',(m[1],)).fetchone()
    if not w:return self.err(404)
    chans=c.execute('select * from channels where workspace_id=? and is_dm=0 and (is_archived=0 or ?)',(w['id'],q.get('include_archived',['0'])[0] in ('1','true'))).fetchall()
    return self.sendj(200,{'workspace':dict(w),'channels':[dict(x) for x in chans],'read_state':[]})
   m=re.match(r'^/api/workspaces/([^/]+)/members$',path)
   if m:
    w=c.execute('select * from workspaces where slug=?',(m[1],)).fetchone()
    if not w:return self.err(404)
    return self.sendj(200,{'members':[dict(x) for x in c.execute('select user_id,role from members where workspace_id=?',(w['id'],))]})
   m=re.match(r'^/api/workspaces/([^/]+)/groups$',path)
   if m:
    w=c.execute('select * from workspaces where slug=?',(m[1],)).fetchone()
    if not w:return self.err(404)
    if not c.execute('select 1 from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone():return self.err(403)
    return self.sendj(200,{'groups':[groupobj(c,x) for x in c.execute('select * from groupsx where workspace_id=?',(w['id'],))]})
   m=re.match(r'^/api/channels/(\d+)/members$',path)
   if m:
    ch=channel(c,int(m[1]))
    if not ch:return self.err(404)
    rows=c.execute('select u.* from users u join cmembers x on x.user_id=u.id where x.channel_id=?',(ch['id'],)).fetchall()
    return self.sendj(200,{'members':[user(x) for x in rows]})
   m=re.match(r'^/api/channels/(\d+)/messages$',path)
   if m:
    ch=channel(c,int(m[1]))
    if not ch:return self.err(404)
    if not member(c,ch['id'],u['id']):return self.err(403)
    rows=c.execute('select * from messages where channel_id=? and parent_id is null and deleted=0 order by id desc limit 200',(ch['id'],)).fetchall()
    return self.sendj(200,{'messages':[msg(c,x) for x in rows],'next_cursor':None})
   m=re.match(r'^/api/messages/(\d+)/replies$',path)
   if m:
    r=c.execute('select * from messages where id=?',(int(m[1]),)).fetchone()
    if not r:return self.err(404)
    if not member(c,r['channel_id'],u['id']):return self.err(403)
    rows=c.execute('select * from messages where parent_id=? and deleted=0 order by id',(r['id'],)).fetchall()
    return self.sendj(200,{'replies':[msg(c,x) for x in rows],'next_cursor':None})
   m=re.match(r'^/api/channels/(\d+)/pins$',path)
   if m:return self.sendj(200,{'pins':[],'next_cursor':None})
   m=re.match(r'^/api/files/(\d+)(/download)?$',path)
   if m:
    r=c.execute('select * from files where id=?',(int(m[1]),)).fetchone()
    if not r:return self.err(404)
    if m[2]:
     b=r['data'];self.send_response(200);self.send_header('Content-Type',r['content_type']);self.send_header('Content-Disposition','attachment; filename="'+r['filename']+'"');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
    return self.sendj(200,{'file':{k:r[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}})
   if path.startswith('/api/search'):
    text=q.get('q',[''])[0]; rows=c.execute('select * from messages where body like ? and deleted=0 order by id desc',(f'%{text}%',)).fetchall()
    return self.sendj(200,{'results':[msg(c,x) for x in rows],'next_cursor':None})
   return self.err(404)
  except Exception as ex: self.err(500,str(ex))
  finally:c.close()
 def do_POST(self):
  path=urllib.parse.urlparse(self.path).path
  if path=='/api/auth/register':
   try:
    d=self.body(); un=d.get('username','');pw=d.get('password','')
    if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(pw)<8:return self.err(400)
    c=db()
    if c.execute('select 1 from users where username=?',(un,)).fetchone():return self.err(409)
    c.execute('insert into users(username,password,display_name) values(?,?,?)',(un,pw,d.get('display_name') or un));uid=c.execute('select last_insert_rowid()').fetchone()[0]
    t=secrets.token_urlsafe(24);c.execute('insert into tokens values(?,?)',(t,uid));c.commit();r=c.execute('select * from users where id=?',(uid,)).fetchone();return self.sendj(201,{'user':user(r),'token':t})
   except Exception:return self.err(400)
  if path=='/api/auth/login':
   d=self.body();c=db();r=c.execute('select * from users where username=? and password=?',(d.get('username'),d.get('password'))).fetchone()
   if not r:return self.err(401)
   t=secrets.token_urlsafe(24);c.execute('insert into tokens values(?,?)',(t,r['id']));c.commit();return self.sendj(200,{'user':user(r),'token':t})
  if path=='/api/workspaces':
   c,u,e=self.req('POST',path)
   if e:return self.err(int(e[0]))
   try:
    d=self.body(); slug=d.get('slug',''); name=d.get('name','')
    if not re.fullmatch(r'[a-z0-9-]{2,32}',slug) or not name:return self.err(400)
    if c.execute('select 1 from workspaces where slug=?',(slug,)).fetchone():return self.err(409)
    c.execute('insert into workspaces(slug,name,owner_id) values(?,?,?)',(slug,name,u['id']))
    wid=c.execute('select last_insert_rowid()').fetchone()[0]
    c.execute('insert into members values(?,?,?)',(wid,u['id'],'owner'))
    c.execute('insert into channels(workspace_id,name) values(?,?)',(wid,'general'))
    cid=c.execute('select last_insert_rowid()').fetchone()[0]
    c.execute('insert into cmembers values(?,?)',(cid,u['id'])); c.commit()
    return self.sendj(201,{'workspace':dict(c.execute('select * from workspaces where id=?',(wid,)).fetchone()),'general_channel':dict(channel(c,cid))})
   finally:c.close()
  c,u,e=self.req('POST',path)
  if e:return self.err(int(e[0]))
  if path=='/api/files':
   try:
    n=int(self.headers.get('Content-Length','0'))
    raw=self.rfile.read(n)
    if n>10485760:return self.err(413)
    ct=self.headers.get('Content-Type','')
    if 'multipart/form-data' not in ct or 'boundary=' not in ct:return self.err(400)
    boundary=ct.split('boundary=',1)[1].strip().strip('"').encode()
    marker=b'--'+boundary
    hs=raw.find(marker)
    he=raw.find(b'\r\n\r\n',hs)
    if hs<0 or he<0:return self.err(400)
    head=raw[hs:he].decode('utf-8','ignore')
    nm=re.search(r'Content-Disposition:[^\r\n]*\bname="([^"]*)"',head,re.I)
    if not nm or nm.group(1)!='file':return self.err(400)
    fn=re.search(r'filename="([^"]*)"',head)
    if not fn:return self.err(400)
    ds=he+4
    de=raw.find(b'\r\n'+marker,ds)
    if de<0:de=raw.find(marker,ds)
    if de<0:return self.err(400)
    data=raw[ds:de].rstrip(b'\r\n')
    typ=re.search(r'Content-Type:\s*([^\r\n]+)',head,re.I)
    c.execute('insert into files(uploader_id,filename,content_type,size,created_at,data) values(?,?,?,?,?,?)',(u['id'],fn.group(1),typ.group(1).strip() if typ else 'application/octet-stream',len(data),now(),data));c.commit()
    r=c.execute('select * from files where id=last_insert_rowid()').fetchone()
    return self.sendj(201,{'file':{k:r[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}})
   finally:c.close()
  try:
   d=self.body()
   m=re.match(r'^/api/workspaces/([^/]+)/groups$',path)
   if m:
    w=c.execute('select * from workspaces where slug=?',(m[1],)).fetchone()
    if not w:return self.err(404)
    if not c.execute('select 1 from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone():return self.err(403)
    h=d.get('handle',''); name=d.get('name'); ids=d.get('user_ids',[])
    if not name or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,31}',h) or not isinstance(ids,list):return self.err(400)
    if c.execute('select 1 from groupsx where workspace_id=? and handle=?',(w['id'],h)).fetchone():return self.err(409)
    c.execute('insert into groupsx(workspace_id,handle,name,creator_id) values(?,?,?,?)',(w['id'],h,name,u['id']));gid=c.execute('select last_insert_rowid()').fetchone()[0]
    for uid in ids:
     if c.execute('select 1 from users where id=?',(uid,)).fetchone():c.execute('insert or ignore into groupmembers values(?,?)',(gid,uid))
    c.commit();return self.sendj(201,{'group':groupobj(c,c.execute('select * from groupsx where id=?',(gid,)).fetchone())})
   m=re.match(r'^/api/workspaces/([^/]+)/channels$',path)
   if m:
    w=c.execute('select * from workspaces where slug=?',(m[1],)).fetchone()
    if not w:return self.err(404)
    if not c.execute('select 1 from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone():return self.err(403)
    n=d.get('name','')
    if not re.fullmatch(r'[a-z0-9-]{1,32}',n):return self.err(400)
    if c.execute('select 1 from channels where workspace_id=? and name=?',(w['id'],n)).fetchone():return self.err(409)
    c.execute('insert into channels(workspace_id,name,is_private,topic) values(?,?,?,?)',(w['id'],n,int(bool(d.get('is_private'))),d.get('topic','')));x=c.execute('select * from channels where id=last_insert_rowid()').fetchone();c.execute('insert into cmembers values(?,?)',(x['id'],u['id']));c.commit();return self.sendj(201,{'channel':dict(x)})
   m=re.match(r'^/api/channels/(\d+)/join$',path)
   if m:
    ch=channel(c,int(m[1]))
    if not ch:return self.err(404)
    w=c.execute('select * from workspaces where id=?',(ch['workspace_id'],)).fetchone()
    if ch['is_private'] and not c.execute('select 1 from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone():return self.err(403)
    c.execute('insert or ignore into members values(?,?,?)',(w['id'],u['id'],'member'));c.execute('insert or ignore into cmembers values(?,?)',(ch['id'],u['id']));c.commit();return self.sendj(200,{'channel':dict(ch)})
   m=re.match(r'^/api/channels/(\d+)/(leave|archive|unarchive)$',path)
   if m:
    ch=channel(c,int(m[1]))
    if not ch:return self.err(404)
    w=c.execute('select * from workspaces where id=?',(ch['workspace_id'],)).fetchone()
    if m[2]=='leave':
     c.execute('delete from cmembers where channel_id=? and user_id=?',(ch['id'],u['id']));c.commit()
     return self.sendj(200,{'channel':dict(channel(c,ch['id']))})
    if w['owner_id']!=u['id']:return self.err(403)
    c.execute('update channels set is_archived=? where id=?',(m[2]=='archive',ch['id']))
    c.commit();return self.sendj(200,{'channel':dict(channel(c,ch['id']))})
   m=re.match(r'^/api/channels/(\d+)/messages$',path)
   if m:
    ch=channel(c,int(m[1]))
    if not ch:return self.err(404)
    if not member(c,ch['id'],u['id']):return self.err(403)
    body=d.get('body','')
    if not isinstance(body,str) or not body.strip():return self.err(400)
    if body.startswith('/') and not body.startswith('//'):
     z=body.split(None,1); cmd=z[0][1:]; rest=z[1] if len(z)>1 else ''
     if cmd=='me':
      if not rest.strip():return self.err(400)
     elif cmd=='shrug':
      body=(rest.strip()+' ' if rest.strip() else '')+'¯\\_(ツ)_/¯'
     elif cmd=='topic':
      if len(rest)>250:return self.err(400)
      c.execute('update channels set topic=? where id=?',(rest,ch['id']));c.commit()
      return self.sendj(201,{'message':None,'channel':dict(channel(c,ch['id']))})
     elif cmd=='invite':
      if not rest.startswith('@'):return self.err(400)
      target=c.execute('select * from users where username=?',(rest[1:].strip(),)).fetchone()
      if not target:return self.err(404)
      w=c.execute('select * from workspaces where id=?',(ch['workspace_id'],)).fetchone()
      if w['owner_id']!=u['id']:return self.err(403)
      c.execute('insert or ignore into cmembers values(?,?)',(ch['id'],target['id']));c.commit()
      return self.sendj(201,{'message':None,'invited_user':user(target)})
     elif cmd in ('archive','unarchive'):
      w=c.execute('select * from workspaces where id=?',(ch['workspace_id'],)).fetchone()
      if w['owner_id']!=u['id']:return self.err(403)
      c.execute('update channels set is_archived=? where id=?',(1 if cmd=='archive' else 0,ch['id']));c.commit()
      out=dict(channel(c,ch['id'])); out['is_archived']=bool(out['is_archived'])
      return self.sendj(201,{'message':None,'channel':out})
     else:return self.err(400)
    if ch['is_archived']:return self.err(423)
    fids=d.get('file_ids',[])
    if fids is not None and (not isinstance(fids,list) or any(not isinstance(x,int) for x in fids)):return self.err(400)
    if fids:
     if len(set(fids))!=len(fids):return self.err(400)
     for fid in fids:
      fr=c.execute('select * from files where id=?',(fid,)).fetchone()
      used=c.execute('select message_id from message_files where file_id=?',(fid,)).fetchone()
      if not fr or fr['uploader_id']!=u['id'] or used:return self.err(400)
    c.execute('insert into messages(channel_id,author_id,body,parent_id,created_at) values(?,?,?,?,?)',(ch['id'],u['id'],body,d.get('parent_id'),now()));r=c.execute('select * from messages where id=last_insert_rowid()').fetchone()
    for fid in fids:c.execute('insert into message_files values(?,?)',(r['id'],fid))
    c.commit();c.execute('begin immediate');eid=emit(c,ch['id'],'message.reply' if r['parent_id'] else 'message.created',{'message':msg(c,r)});c.commit();return self.sendj(201,{'message':msg(c,r)})
   m=re.match(r'^/api/messages/(\d+)/reactions$',path)
   if m:
    r=c.execute('select * from messages where id=?',(int(m[1]),)).fetchone()
    if not r:return self.err(404)
    if not member(c,r['channel_id'],u['id']):return self.err(403)
    ch=channel(c,r['channel_id'])
    if ch['is_archived']:return self.err(423)
    em=d.get('emoji','')
    if not em:return self.err(400)
    if not c.execute('select 1 from reactions where message_id=? and user_id=? and emoji=?',(r['id'],u['id'],em)).fetchone():
     c.commit();c.execute('begin immediate');c.execute('insert into reactions values(?,?,?)',(r['id'],u['id'],em));emit(c,r['channel_id'],'reaction.added',{'message_id':r['id'],'emoji':em,'user_id':u['id']});c.commit()
    return self.sendj(200,{'reactions':reactions(c,r['id'])})
   m=re.match(r'^/api/dms$',path)
   if m:
    oid=d.get('user_id')
    if not oid:return self.err(400)
    if not c.execute('select id from users where id=?',(oid,)).fetchone():return self.err(404)
    key=':'.join(map(str,sorted([u['id'],oid])));r=c.execute('select * from channels where dm_key=?',(key,)).fetchone()
    if not r:
     c.execute('insert into channels(name,is_private,is_dm,dm_key) values(?,?,?,?)',(key,1,1,key));cid=c.execute('select last_insert_rowid()').fetchone()[0];c.execute('insert into cmembers values(?,?),(?,?)',(cid,u['id'],cid,oid));c.commit()
    else:cid=r['id']
    return self.sendj(200,{'channel_id':cid})
   return self.err(404)
  except Exception as ex:self.err(500,str(ex))
  finally:c.close()
 def do_PATCH(self):
  path=urllib.parse.urlparse(self.path).path;c,u,e=self.req('PATCH',path)
  if e:return self.err(int(e[0]))
  d=self.body()
  try:
   if path=='/api/users/me':
    allowed={'display_name':80,'timezone':128,'status_text':140,'status_emoji':64,'avatar_url':512}
    for k,v in d.items():
     if k not in allowed or not isinstance(v,str) or (k=='display_name' and not v) or len(v)>allowed[k]:return self.err(400)
    if 'timezone' in d:
     if not isinstance(d['timezone'],str):
      return self.err(400)
     try: ZoneInfo(d['timezone'])
     except Exception:return self.err(400)
    sets=[];vals=[]
    for k in ('display_name','timezone','status_text','status_emoji','avatar_url'):
     if k in d:sets.append(k+'=?');vals.append(d[k])
    if sets:
     vals.append(u['id']);c.execute('update users set '+','.join(sets)+' where id=?',vals)
     r=c.execute('select * from users where id=?',(u['id'],)).fetchone()
     c.execute('insert into global_events(payload) values(?)',(json.dumps({'type':'user.updated','user':user(r)},separators=(',',':')),));c.commit()
    else:r=c.execute('select * from users where id=?',(u['id'],)).fetchone()
    return self.sendj(200,{'user':user(r)})
   m=re.match(r'^/api/workspaces/([^/]+)/groups/([^/]+)$',path)
   if m:
    w=c.execute('select * from workspaces where slug=?',(m[1],)).fetchone()
    if not w:return self.err(404)
    g=c.execute('select * from groupsx where workspace_id=? and handle=?',(w['id'],m[2])).fetchone()
    if not g:return self.err(404)
    if u['id']!=g['creator_id'] and u['id']!=w['owner_id']:return self.err(403)
    c.execute('update groupsx set name=? where id=?',(d.get('name',g['name']),g['id']))
    if 'user_ids' in d:
     c.execute('delete from groupmembers where group_id=?',(g['id'],))
     for uid in d['user_ids']:c.execute('insert or ignore into groupmembers values(?,?)',(g['id'],uid))
    c.commit();return self.sendj(200,{'group':groupobj(c,c.execute('select * from groupsx where id=?',(g['id'],)).fetchone())})
   m=re.match(r'^/api/channels/(\d+)$',path)
   if m:
    ch=channel(c,int(m[1]))
    if not ch:return self.err(404)
    if not member(c,ch['id'],u['id']):return self.err(403)
    c.execute('update channels set topic=? where id=?',(d.get('topic',''),ch['id']));c.commit();return self.sendj(200,{'channel':dict(channel(c,ch['id']))})
   m=re.match(r'^/api/messages/(\d+)$',path);r=c.execute('select * from messages where id=?',(int(m[1]),)).fetchone() if m else None
   if not r:return self.err(404)
   if r['author_id']!=u['id']:return self.err(403)
   ch=channel(c,r['channel_id'])
   if ch['is_archived']:return self.err(423)
   if r['deleted']:return self.err(409)
   body=d.get('body')
   if not isinstance(body,str) or not body.strip():return self.err(400)
   c.execute('update messages set body=?,edited_at=? where id=?',(body,now(),r['id']));c.commit()
   fresh=c.execute('select * from messages where id=?',(r['id'],)).fetchone()
   c.execute('begin immediate');emit(c,r['channel_id'],'message.updated',{'message':msg(c,fresh)});c.commit()
   return self.sendj(200,{'message':msg(c,fresh)})
  finally:c.close()
 def do_DELETE(self):
  path=urllib.parse.urlparse(self.path).path;c,u,e=self.req('DELETE',path)
  if e:return self.err(int(e[0]))
  try:
   m=re.match(r'^/api/workspaces/([^/]+)/groups/([^/]+)$',path)
   if m:
    w=c.execute('select * from workspaces where slug=?',(m[1],)).fetchone()
    if not w:return self.err(404)
    g=c.execute('select * from groupsx where workspace_id=? and handle=?',(w['id'],m[2])).fetchone()
    if not g:return self.sendj(200,{'deleted':True})
    if u['id']!=g['creator_id'] and u['id']!=w['owner_id']:return self.err(403)
    c.execute('delete from groupmembers where group_id=?',(g['id'],));c.execute('delete from groupsx where id=?',(g['id'],));c.commit();return self.sendj(200,{'deleted':True})
   m=re.match(r'^/api/messages/(\d+)/reactions/(.*)$',path)
   if m:
    r=c.execute('select * from messages where id=?',(int(m[1]),)).fetchone()
    if not r:return self.err(404)
    em=urllib.parse.unquote(m[2])
    existed=c.execute('select 1 from reactions where message_id=? and user_id=? and emoji=?',(r['id'],u['id'],em)).fetchone()
    if existed:
     c.execute('delete from reactions where message_id=? and user_id=? and emoji=?',(r['id'],u['id'],em))
     c.commit();c.execute('begin immediate');emit(c,r['channel_id'],'reaction.removed',{'message_id':r['id'],'emoji':em,'user_id':u['id']});c.commit()
    return self.sendj(200,{'reactions':reactions(c,r['id'])})
   m=re.match(r'^/api/messages/(\d+)$',path);r=c.execute('select * from messages where id=?',(int(m[1]),)).fetchone()
   if not r:return self.err(404)
   ch=channel(c,r['channel_id'])
   if ch['is_archived']:return self.err(423)
   w=c.execute('select * from workspaces where id=?',(ch['workspace_id'],)).fetchone()
   if r['author_id']!=u['id'] and (not w or w['owner_id']!=u['id']):return self.err(403)
   if not r['deleted']:
    c.execute('update messages set deleted=1 where id=?',(r['id'],));c.commit()
    c.execute('begin immediate');emit(c,r['channel_id'],'message.deleted',{'message_id':r['id']});c.commit()
   return self.sendj(200,{'deleted':True})
  finally:c.close()
 def html(self):
  b=HTML.encode();self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def websocket(self,q):
  c=db();u=auth(c,q.get('token',[''])[0]); 
  if not u:return self.err(401)
  conn_id=str(uuid.uuid4())
  c.execute('insert into presence values(?,?,?)',(conn_id,u['id'],NODE));c.commit()
  global_seen=c.execute('select coalesce(max(id),0) from global_events').fetchone()[0]
  key=self.headers.get('Sec-WebSocket-Key')
  if not key:return self.err(400)
  self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode());self.end_headers()
  self.connection.settimeout(.001); subs={}; seen={}; pending_resume=set()
  def send(o):
   x=json.dumps(o,separators=(',',':')).encode(); head=bytes([129]);n=len(x)
   if n<126:head+=bytes([n])
   elif n<65536:head+=bytes([126])+struct.pack('>H',n)
   else:head+=bytes([127])+struct.pack('>Q',n)
   self.connection.sendall(head+x)
  try:
   while True:
    try:
     h=self.connection.recv(2)
     if not h: break
     if (h[0] & 15)==8: break
     if h:
      n=h[1]&127
      if n==126:n=struct.unpack('>H',self.connection.recv(2))[0]
      elif n==127:n=struct.unpack('>Q',self.connection.recv(8))[0]
      mask=self.connection.recv(4);z=bytes(a^mask[i%4] for i,a in enumerate(self.connection.recv(n)));d=json.loads(z)
      ch=int(d.get('channel_id')); typ=d.get('type'); head=c.execute('select coalesce(last_event_id,0) from seq where channel_id=?',(ch,)).fetchone(); head=head[0] if head else 0
      if typ=='subscribe':subs[ch]=True;seen[ch]=head;send({'type':'subscribed','channel_id':ch,'head_event_id':head})
      elif typ=='resume':
       seen[ch]=int(d.get('since_event_id',0));subs[ch]=True;pending_resume.add(ch)
    except socket.timeout: pass
    grows=c.execute('select id,payload from global_events where id>? order by id',(global_seen,)).fetchall()
    for gr in grows:
     send(json.loads(gr['payload'])); global_seen=gr['id']
    for ch in list(subs):
     rows=c.execute('select event_id,type,payload from events where channel_id=? and event_id>? order by event_id',(ch,seen.get(ch,0))).fetchall()
     for r in rows:
      o=json.loads(r['payload']);o.update(type=r['type'],event_id=r['event_id'],channel_id=ch);send(o);seen[ch]=r['event_id']
     if ch in pending_resume:
      send({'type':'resumed','channel_id':ch,'head_event_id':seen[ch]})
      pending_resume.discard(ch)
    time.sleep(.001)
  except Exception: pass
  finally:
   try:c.execute('delete from presence where conn_id=?',(conn_id,));c.commit();c.close()
   except Exception:pass
HTML='''<!doctype html><html><head><meta charset="utf-8"><title>Huddle</title><style>
body{margin:0;font:15px Arial;color:#222}.app{display:flex;height:100vh}.side{width:240px;background:#3f0e40;color:#fff;padding:15px}.main{flex:1;padding:25px}button{border:0;padding:9px 14px;color:#fff}[data-button-role=primary]{background:#007a5a;color:#fff}[data-button-role=danger]{background:#e01e5a;color:#fff}[data-button-role=secondary]{background:#1264a3;color:#fff}input{padding:8px;margin:3px}.modal{padding:16px}
</style></head><body>
<div class="app"><aside class="side"><h2 data-testid="workspace-header">Huddle</h2><div data-testid="current-user"></div><button data-testid="new-channel-btn" data-button-role="secondary">New channel</button><button data-testid="workspace-settings-btn" data-button-role="secondary">Workspace settings</button><button data-testid="logout-btn" data-button-role="danger">Logout</button><div data-testid="channel-list"></div><div data-testid="dms-list"></div></aside><main class="main"><h2 data-testid="channel-title"># general</h2><div data-testid="channel-topic"></div><button data-testid="channel-settings-btn" data-button-role="secondary">Channel settings</button><div data-testid="message-list"></div><input data-testid="message-input"><button data-testid="send-btn" data-button-role="primary">Send</button></main></div>
<div id="auth-modal" class="modal"><form data-testid="auth-form"><input name="username"><input name="password" type="password"><input name="display_name"><button type="submit" data-testid="auth-submit" data-button-role="primary">Sign up</button><button type="button" data-testid="auth-toggle">Login</button><div data-testid="auth-error"></div></form></div>
<form data-testid="workspace-create-form"><input name="name"><input name="slug"><button data-testid="workspace-general-submit" data-button-role="primary">Create workspace</button></form><button data-testid="empty-state-create-workspace">Create workspace</button><form data-testid="join-workspace-form"><input name="slug"><button data-testid="join-workspace-submit" data-button-role="primary">Join workspace</button><div data-testid="join-workspace-error"></div></form>
<div id="create-channel-modal"><form data-testid="create-channel-form"><input name="name"><input name="topic"><input id="create-channel-private" data-testid="create-channel-private" type="checkbox"><button data-testid="create-channel-submit" data-button-role="primary">Create</button><button type="button" data-testid="create-channel-cancel" data-button-role="secondary">Cancel</button><div data-testid="create-channel-error"></div></form></div>
<div id="channel-settings-modal"><h3 data-testid="channel-settings-title">Channel settings</h3><button data-testid="channel-settings-close" data-button-role="secondary">Close</button><div data-testid="channel-members-list"></div><form data-testid="channel-add-member-form"><input data-testid="channel-add-member-input"><button data-testid="channel-add-member-submit" data-button-role="primary">Add</button></form><input data-testid="channel-topic-input"><button data-testid="channel-topic-submit" data-button-role="primary">Save topic</button><button data-testid="archive-channel-btn" data-button-role="danger">Archive</button><button data-testid="unarchive-channel-btn" data-button-role="secondary">Unarchive</button></div>
<div id="workspace-settings-modal"><button data-testid="workspace-settings-close" data-button-role="secondary">Close</button><input data-testid="workspace-name-input"><select data-testid="workspace-join-mode"></select><button data-testid="workspace-general-submit" data-button-role="primary">Save</button><button data-testid="settings-tab-members">Members</button><button data-testid="settings-tab-invitations">Invitations</button><button data-testid="settings-tab-general">General</button><div data-testid="settings-pane-members"></div><div data-testid="settings-pane-invitations"></div><div data-testid="settings-pane-general"></div><button data-testid="create-invitation-btn" data-button-role="secondary">Invite</button><form id="create-invitation-form" data-testid="create-invitation-form"><button data-testid="create-invitation-submit" data-button-role="primary">Create invitation</button></form><div data-testid="invitations-list"></div></div>
<div data-testid="thread-panel"><button data-testid="close-thread" data-button-role="secondary">Close</button><input data-testid="thread-input"><button data-testid="thread-send" data-button-role="primary">Reply</button></div><div data-testid="emoji-picker"></div><span data-testid="reaction-chip"></span>
<script>const q=x=>document.querySelector('[data-testid="'+x+'"]');let mode='register';q('auth-toggle').onclick=()=>{mode=mode==='register'?'login':'register';q('auth-submit').textContent=mode==='register'?'Sign up':'Login'};q('auth-form').onsubmit=async e=>{e.preventDefault();let d=Object.fromEntries(new FormData(e.target)),r=await fetch('/api/auth/'+mode,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}),x=await r.json();if(!r.ok){q('auth-error').textContent=x.error||'Invalid';return}localStorage.setItem('huddle.token',x.token);window.__huddle_token=x.token;q('auth-modal').style.display='none';q('current-user').textContent=x.user.display_name||x.user.username};q('logout-btn').onclick=()=>{localStorage.removeItem('huddle.token');q('auth-modal').style.display='block'};</script><script>
(()=>{const q=t=>document.querySelector('[data-testid="'+t+'"]'), api=async(path,opt={})=>{opt.headers={...(opt.headers||{}),'Content-Type':'application/json','Authorization':'Bearer '+localStorage.getItem('huddle.token')};let r=await fetch('/api'+path,opt);let x=await r.json();if(!r.ok)throw Error(x.error||'request failed');return x};
let mode='login',ws=null,current=null;
q('auth-form').setAttribute('data-mode','login');q('auth-submit').textContent='Login';q('create-channel-modal').style.display='none';q('channel-settings-modal').style.display='none';q('workspace-settings-modal').style.display='none';q('workspace-create-form').style.display='none';q('join-workspace-form').style.display='none';
q('auth-toggle').onclick=()=>{mode=mode==='login'?'register':'login';q('auth-form').setAttribute('data-mode',mode);q('auth-submit').textContent=mode==='login'?'Login':'Sign up'};
q('auth-form').onsubmit=async e=>{e.preventDefault();let d=Object.fromEntries(new FormData(e.target));try{let x=await api('/auth/'+mode,{method:'POST',body:JSON.stringify(d)});localStorage.setItem('huddle.token',x.token);window.__huddle_token=x.token;q('auth-modal').style.display='none';q('current-user').textContent=x.user.display_name||x.user.username;load()}catch(x){q('auth-error').textContent=x.message}};
q('logout-btn').onclick=()=>{localStorage.removeItem('huddle.token');q('auth-modal').style.display='block';q('auth-form').setAttribute('data-mode','login');mode='login';q('auth-submit').textContent='Login'};
async function load(){let x=await api('/workspaces');if(x.workspaces.length){ws=x.workspaces[0];await render()}else{q('workspace-create-form').style.display='block';q('empty-state-create-workspace').style.display='block'}}
async function render(){let x=await api('/workspaces/'+ws.slug);q('workspace-header').textContent=ws.name;q('channel-list').innerHTML='';x.channels.forEach(c=>{let e=document.createElement('button');e.dataset.testid='channel-entry';e.dataset.channelId=c.id;e.dataset.channelName=c.name;e.textContent='# '+c.name;e.onclick=()=>select(c);q('channel-list').append(e)});let g=x.channels.find(c=>c.name==='general')||x.channels[0];if(g)select(g)}
function select(c){current=c;q('channel-title').textContent='# '+c.name;q('channel-topic').textContent=c.topic||''}
q('empty-state-create-workspace').onclick=()=>q('workspace-create-form').style.display='block';
q('workspace-create-form').onsubmit=async e=>{e.preventDefault();let d=Object.fromEntries(new FormData(e.target));try{let x=await api('/workspaces',{method:'POST',body:JSON.stringify(d)});ws=x.workspace;q('workspace-create-form').style.display='none';await render()}catch(x){q('join-workspace-error').textContent=x.message}};
q('new-channel-btn').onclick=()=>q('create-channel-modal').style.display='block';
q('create-channel-cancel').onclick=()=>q('create-channel-modal').style.display='none';
q('create-channel-form').onsubmit=async e=>{e.preventDefault();try{let d=Object.fromEntries(new FormData(e.target));d.is_private=q('create-channel-private').checked;await api('/workspaces/'+ws.slug+'/channels',{method:'POST',body:JSON.stringify(d)});q('create-channel-modal').style.display='none';await render()}catch(x){q('create-channel-error').textContent=x.message}};
if(localStorage.getItem('huddle.token')){q('auth-modal').style.display='none';api('/auth/me').then(x=>{q('current-user').textContent=x.user.display_name||x.user.username;load()}).catch(()=>{})}
})();
</script><script>
(()=>{const Q=t=>document.querySelector('[data-testid="'+t+'"]'),tok=new URLSearchParams(location.search).get('token');if(tok){localStorage.setItem('huddle.token',tok);window.__huddle_token=tok}const H=()=>({Authorization:'Bearer '+localStorage.getItem('huddle.token'),'Content-Type':'application/json'}),A=async(u,o={})=>{let r=await fetch('/api'+u,{...o,headers:H()});let x=await r.json();if(!r.ok)throw Error(x.error||'error');return x};let W,C,M=[],thread=null;
function row(m){let e=document.createElement('div');e.dataset.testid='message';e.dataset.id=m.id;e.innerHTML='<span data-testid="message-avatar">'+(m.author?.display_name||'?')[0]+'</span> <b data-testid="message-author">'+(m.author?.display_name||m.author?.username||'User')+'</b> <small data-testid="message-time">'+new Date(m.created_at).toLocaleString()+'</small><div data-testid="message-body"></div><span data-testid="edited-indicator" style="display:'+(m.edited_at?'inline':'none')+'">(edited)</span><span data-testid="reply-count">'+m.reply_count+' replies</span> <button data-testid="open-thread-btn">Thread</button> <button data-testid="edit-message-btn">Edit</button> <button data-testid="delete-message-btn">Delete</button> <button data-testid="reaction-button">😀</button><span data-testid="reaction-chip" style="display:none"></span>';e.querySelector('[data-testid=message-body]').textContent=m.body;e.querySelector('[data-testid=edit-message-btn]').onclick=async()=>{let x=document.createElement('input');x.dataset.testid='edit-input';x.value=m.body;e.replaceWith(x);x.focus();x.onkeydown=async z=>{if(z.key==='Enter'){await A('/messages/'+m.id,{method:'PATCH',body:JSON.stringify({body:x.value})});load()}}};e.querySelector('[data-testid=delete-message-btn]').onclick=async()=>{await A('/messages/'+m.id,{method:'DELETE'});load()};e.querySelector('[data-testid=reaction-button]').onclick=()=>{let p=document.createElement('div');p.dataset.testid='emoji-picker';p.style.display='grid';p.style.gridTemplateColumns='repeat(4,32px)';['😀','👍','🎉','❤️','🔥','✅','😂','🚀'].forEach(em=>{let b=document.createElement('button');b.dataset.testid='emoji-option';b.textContent=em;b.onclick=async()=>{await A('/messages/'+m.id+'/reactions',{method:'POST',body:JSON.stringify({emoji:em})});p.remove();let c=e.querySelector('[data-testid=reaction-chip]');c.textContent=em+' 1';c.style.display='inline'};p.append(b)});e.append(p)};e.querySelector('[data-testid=open-thread-btn]').onclick=()=>openThread(m,e);return e}
async function load(){if(!W){let x=await A('/workspaces');if(!x.workspaces.length){Q('workspace-create-form').style.display='block';return}W=x.workspaces[0]}let x=await A('/workspaces/'+W.slug);Q('workspace-header').textContent=W.name;let l=Q('channel-list');l.innerHTML='';x.channels.filter(c=>!c.is_archived).forEach(c=>{let b=document.createElement('button');b.dataset.testid='channel-entry';b.dataset.channelId=c.id;b.dataset.channelName=c.name;b.textContent='# '+c.name;b.onclick=()=>{C=c;loadMsgs()};l.append(b)});C=C||x.channels.find(c=>c.name==='general')||x.channels[0];if(C)loadMsgs()}
async function loadMsgs(){Q('channel-title').textContent='# '+C.name;Q('channel-topic').textContent=C.topic||'';let x=await A('/channels/'+C.id+'/messages'),l=Q('message-list');l.innerHTML='';if(!x.messages.length){let z=document.createElement('div');z.dataset.testid='empty-channel';z.textContent='No messages yet — start the conversation.';l.append(z)}x.messages.reverse().forEach(m=>l.append(row(m)))}
Q('send-btn').onclick=async()=>{let i=Q('message-input'),b=i.value;if(!b.trim())return;await A('/channels/'+C.id+'/messages',{method:'POST',body:JSON.stringify({body:b})});i.value='';loadMsgs()};
async function openThread(m,parent){let p=Q('thread-panel');p.style.display='block';p.innerHTML='<button data-testid="close-thread">Close</button><div data-testid="thread-messages"></div><input data-testid="thread-input"><button data-testid="thread-send">Reply</button>';let draw=async()=>{let x=await A('/messages/'+m.id+'/replies');let d=p.querySelector('[data-testid=thread-messages]');d.innerHTML='';x.replies.forEach(r=>{let z=document.createElement('div');z.dataset.testid='message-body';z.textContent=r.body;d.append(z)});parent.querySelector('[data-testid=reply-count]').textContent=x.replies.length+' replies'};await draw();p.querySelector('[data-testid=close-thread]').onclick=()=>p.style.display='none';p.querySelector('[data-testid=thread-send]').onclick=async()=>{let i=p.querySelector('[data-testid=thread-input]');await A('/channels/'+C.id+'/messages',{method:'POST',body:JSON.stringify({body:i.value,parent_id:m.id})});i.value='';draw()}}
Q('logout-btn').onclick=()=>{localStorage.removeItem('huddle.token');location.reload()};if(localStorage.getItem('huddle.token')){Q('auth-modal').style.display='none';A('/auth/me').then(x=>{Q('current-user').textContent=x.user.display_name||x.user.username;load()}).catch(()=>{Q('auth-modal').style.display='block'})}else{Q('auth-form').setAttribute('data-mode','login');Q('auth-submit').textContent='Login'}
})();
</script></body></html>'''

if __name__=='__main__':
    ThreadingHTTPServer.allow_reuse_address=True
    ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
