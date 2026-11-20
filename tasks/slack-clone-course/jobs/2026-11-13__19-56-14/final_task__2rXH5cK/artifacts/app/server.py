import sqlite3,os,json,re,hashlib,secrets,datetime,time,urllib.parse,base64,struct,threading,email.parser,email.policy
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
DB='/app/huddle.db'; NODE=int(os.getenv('NODE_ID','0')); PORT=int(os.getenv('PORT','8000'))
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,timeout=10); c.row_factory=sqlite3.Row
 c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=5000')
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT "UTC",avatar_url TEXT DEFAULT "",status_text TEXT DEFAULT "",status_emoji TEXT DEFAULT "");
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT "open");
CREATE TABLE IF NOT EXISTS wm(workspace_id,user_id,role,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT "",is_archived INTEGER DEFAULT 0,dm_a INTEGER,dm_b INTEGER);
CREATE TABLE IF NOT EXISTS cm(channel_id,user_id,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS reactions(message_id,user_id,emoji,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS channel_seq(channel_id INTEGER PRIMARY KEY,last_event_id INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(channel_id,event_id,type,payload_json,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id,filename,content_type,size,created_at);
CREATE TABLE IF NOT EXISTS message_files(message_id INTEGER,file_id INTEGER,UNIQUE(message_id,file_id));
CREATE TABLE IF NOT EXISTS groups_tbl(id INTEGER PRIMARY KEY,workspace_id,handle,name,creator_id,user_ids TEXT,UNIQUE(workspace_id,handle));
CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY AUTOINCREMENT,payload TEXT);
CREATE TABLE IF NOT EXISTS presence(conn_id TEXT,user_id,node_id);
'''); return c
def user(c,uid):
 r=c.execute('select * from users where id=?',(uid,)).fetchone()
 return None if not r else {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def auth(h):
 if not h or not h.startswith('Bearer '): return None
 c=db(); r=c.execute('select user_id from tokens where token=?',(h[7:],)).fetchone(); c.close(); return r['user_id'] if r else None
def reactions(c,mid):
 out=[]
 for r in c.execute('select emoji,user_id from reactions where message_id=? order by emoji,user_id',(mid,)):
  if not out or out[-1]['emoji']!=r['emoji']: out.append({'emoji':r['emoji'],'count':0,'user_ids':[]})
  out[-1]['count']+=1; out[-1]['user_ids'].append(r['user_id'])
 return out
def mentions(c,ch,author,body):
 ids=set()
 for x in re.findall(r'(?<!\w)@([A-Za-z0-9_]+)',body):
  if x in ('channel','here'):
   if x=='channel': ids.update(r['user_id'] for r in c.execute('select user_id from cm where channel_id=?',(ch,)) if r['user_id']!=author)
   else:
    ids.update(r['user_id'] for r in c.execute('select user_id from presence where user_id!=?',(author,)) if c.execute('select 1 from cm where channel_id=? and user_id=?',(ch,r['user_id'])).fetchone())
  else:
   r=c.execute('select id from users where username=?',(x,)).fetchone()
   if r and r['id']!=author and c.execute('select 1 from cm where channel_id=? and user_id=?',(ch,r['id'])).fetchone(): ids.add(r['id'])
   g=c.execute('select user_ids from groups_tbl g join channels x on x.workspace_id=g.workspace_id where x.id=? and g.handle=?',(ch,x)).fetchone()
   if g:
    ids.update(int(z) for z in json.loads(g['user_ids']) if int(z)!=author and c.execute('select 1 from cm where channel_id=? and user_id=?',(ch,int(z))).fetchone())
 return list(ids)
def emit(c,ch,typ,payload):
 c.execute('insert or ignore into channel_seq(channel_id,last_event_id) values(?,0)',(ch,))
 n=c.execute('update channel_seq set last_event_id=last_event_id+1 where channel_id=?',(ch,)).rowcount
 eid=c.execute('select last_event_id from channel_seq where channel_id=?',(ch,)).fetchone()[0]
 p={'type':typ,'event_id':eid,'channel_id':ch,**payload}; c.execute('insert into events values(?,?,?,?)',(ch,eid,typ,json.dumps(p))); return p
def msgobj(c,r):
 fs=[dict(x) for x in c.execute('select f.* from files f join message_files mf on mf.file_id=f.id where mf.message_id=?',(r['id'],))]
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user(c,r['author_id']),'body':r['body'],'parent_id':r['parent_id'],'reply_count':c.execute('select count(*) from messages where parent_id=? and deleted=0',(r['id'],)).fetchone()[0],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':fs,'reactions':reactions(c,r['id']),'mentions':mentions(c,r['channel_id'],r['author_id'],r['body'])}
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def sendj(self,obj,status=200):
  b=json.dumps(obj,separators=(',',':')).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def err(self,s,m='error'): self.sendj({'error':m},s)
 def read(self):
  n=int(self.headers.get('Content-Length','0')); return self.rfile.read(n)
 def body(self):
  try: x=json.loads(self.read() or b'{}'); return x if isinstance(x,dict) else None
  except: return None
 def do_GET(self):
  if self.path=='/api/health': return self.sendj({'status':'ok','node_id':NODE})
  if self.path.startswith('/api/ws'): return self.websocket()
  uid=auth(self.headers.get('Authorization')); p=urllib.parse.urlparse(self.path); a=p.path.split('/')
  if a[:3]==['','api','auth'] and len(a)>3 and a[3]=='me':
   if not uid:return self.err(401)
   c=db(); self.sendj({'user':user(c,uid)});c.close();return
  if a[:3]==['','api','users'] and len(a)==4:
   if not uid:return self.err(401)
   c=db(); r=user(c,int(a[3])) if a[3].isdigit() else None;c.close()
   return self.sendj({'user':r}) if r else self.err(404)
  if not uid:return self.err(401)
  c=db()
  try:
   if a[2]=='workspaces' and len(a)==3:
    return self.sendj({'workspaces':[dict(r) for r in c.execute('select * from workspaces where id in (select workspace_id from wm where user_id=?)',(uid,))]})
   if a[2]=='workspaces' and len(a)>=5 and a[4]=='groups':
    w=c.execute('select * from workspaces where slug=?',(a[3],)).fetchone()
    if not w:return self.err(404)
    if not c.execute('select 1 from wm where workspace_id=? and user_id=?',(w['id'],uid)).fetchone():return self.err(403)
    rs=c.execute('select * from groups_tbl where workspace_id=?',(w['id'],))
    return self.sendj({'groups':[{'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':json.loads(r['user_ids'])} for r in rs]})
   if a[2]=='workspaces' and len(a)>=4:
    w=c.execute('select * from workspaces where slug=?',(a[3],)).fetchone()
    if not w:return self.err(404)
    if len(a)==5 and a[4]=='members': return self.sendj({'members':[dict(r) for r in c.execute('select user_id,role from wm where workspace_id=?',(w['id'],))]})
    chans=[dict(r) for r in c.execute('select * from channels where workspace_id=? and is_dm=0',(w['id'],))]
    return self.sendj({'workspace':dict(w),'channels':chans,'read_state':[]})
   if a[2]=='channels' and len(a)>=4:
    ch=int(a[3]) if a[3].isdigit() else -1; r=c.execute('select * from channels where id=?',(ch,)).fetchone()
    if not r:return self.err(404)
    if a[4]=='members': return self.sendj({'members':[user(c,x['user_id']) for x in c.execute('select user_id from cm where channel_id=?',(ch,))]})
    if a[4]=='messages':
     if not c.execute('select 1 from cm where channel_id=? and user_id=?',(ch,uid)).fetchone(): return self.err(403)
     rs=c.execute('select * from messages where channel_id=? and parent_id is null and deleted=0 order by id desc',(ch,)); return self.sendj({'messages':[msgobj(c,x) for x in rs],'next_cursor':None})
   if a[2]=='messages' and len(a)>=4:
    mid=int(a[3]); r=c.execute('select * from messages where id=? and deleted=0',(mid,)).fetchone()
    if not r:return self.err(404)
    if len(a)>4 and a[4]=='replies': return self.sendj({'replies':[msgobj(c,x) for x in c.execute('select * from messages where parent_id=? and deleted=0 order by id',(mid,))],'next_cursor':None})
    if len(a)>4 and a[4]=='reactions': return self.sendj({'reactions':reactions(c,mid)})
   if a[2]=='files' and len(a)==4:
    r=c.execute('select * from files where id=?',(a[3],)).fetchone()
    if not r:return self.err(404)
    return self.sendj({'file':dict(r)})
   if a[2]=='files' and len(a)==5 and a[4]=='download':
    r=c.execute('select * from files where id=?',(a[3],)).fetchone()
    if not r:return self.err(404)
    b=open('/app/data/files/'+a[3],'rb').read();self.send_response(200);self.send_header('Content-Type',r['content_type']);self.send_header('Content-Disposition','attachment; filename="'+r['filename']+'"');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
   if a[2]=='workspaces' and len(a)>=5 and a[4]=='groups':
    w=c.execute('select * from workspaces where slug=?',(a[3],)).fetchone()
    if not w:return self.err(404)
    rs=c.execute('select * from groups_tbl where workspace_id=?',(w['id'],));return self.sendj({'groups':[{'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':json.loads(r['user_ids'])} for r in rs]})
  finally:c.close()
  self.err(404)
 def do_POST(self):
  uid=auth(self.headers.get('Authorization')); p=urllib.parse.urlparse(self.path);a=p.path.split('/')
  if a[2]=='auth':
   x=self.body()
   if a[3]=='register':
    if not x or not re.fullmatch(r'[A-Za-z0-9_]+',x.get('username','')) or len(x.get('password',''))<8:return self.err(400)
    c=db()
    try:c.execute('insert into users(username,password,display_name) values(?,?,?)',(x['username'],x['password'],x.get('display_name') or x['username']));c.commit()
    except sqlite3.IntegrityError:c.close();return self.err(409)
    u=c.execute('select * from users where username=?',(x['username'],)).fetchone();t=secrets.token_urlsafe(24);c.execute('insert into tokens values(?,?)',(t,u['id']));c.commit();self.sendj({'user':user(c,u['id']),'token':t},201);c.close();return
   if a[3]=='login':
    c=db();r=c.execute('select * from users where username=? and password=?',(x.get('username'),x.get('password'))).fetchone()
    if not r:c.close();return self.err(401)
    t=secrets.token_urlsafe(24);c.execute('insert into tokens values(?,?)',(t,r['id']));c.commit();self.sendj({'user':user(c,r['id']),'token':t});c.close();return
  if not uid:return self.err(401)
  if a[2]=='files':
   if int(self.headers.get('Content-Length','0'))>10485760:
    self.read(); return self.err(413)
   raw=self.read();ct=self.headers.get('Content-Type','')
   try:
    em=email.parser.BytesParser(policy=email.policy.default).parsebytes(b'Content-Type: '+ct.encode()+b'\r\nMIME-Version: 1.0\r\n\r\n'+raw)
    part=next((z for z in em.walk() if not z.is_multipart() and re.search(r'(?i)name=["\\\']file["\\\']',z.get('Content-Disposition',''))),None)
   except Exception: part=None
   if not part:return self.err(400)
   data=part.get_payload(decode=True) or b''
   if data.endswith(b'\r\n'): data=data[:-2]
   fn=part.get_filename() or 'file'; typ=(part.get('Content-Type') or 'application/octet-stream').split(';',1)[0].strip()
   os.makedirs('/app/data/files',exist_ok=True);c=db()
   c.execute('insert into files(uploader_id,filename,content_type,size,created_at) values(?,?,?,?,?)',(uid,fn,typ,len(data),now()))
   fid=c.execute('select last_insert_rowid()').fetchone()[0];open('/app/data/files/'+str(fid),'wb').write(data);c.commit()
   out=dict(c.execute('select * from files where id=?',(fid,)).fetchone());c.close();return self.sendj({'file':out},201)
  x=self.body();c=db()
  try:
   if a[2]=='workspaces' and len(a)==3:
    if not x or not re.fullmatch(r'[a-z0-9-]{2,32}',x.get('slug','')):return self.err(400)
    try:c.execute('insert into workspaces(slug,name,owner_id) values(?,?,?)',(x['slug'],x.get('name',''),uid));wid=c.execute('select last_insert_rowid()').fetchone()[0];c.execute('insert into wm values(?,?,?)',(wid,uid,'owner'));c.execute('insert into channels(workspace_id,name) values(?,?)',(wid,'general'));cid=c.execute('select last_insert_rowid()').fetchone()[0];c.execute('insert into cm values(?,?)',(cid,uid));c.commit();return self.sendj({'workspace':dict(c.execute('select * from workspaces where id=?',(wid,)).fetchone()),'general_channel':dict(c.execute('select * from channels where id=?',(cid,)).fetchone())},201)
    except sqlite3.IntegrityError:return self.err(409)
   if a[2]=='workspaces' and len(a)>=5 and a[4]=='channels':
    w=c.execute('select * from workspaces where slug=?',(a[3],)).fetchone();m=c.execute('select role from wm where workspace_id=? and user_id=?',(w['id'],uid)).fetchone() if w else None
    if not w:return self.err(404)
    if not m:return self.err(403)
    if not x or not re.fullmatch(r'[a-z0-9-]{1,32}',x.get('name','')):return self.err(400)
    if x.get('is_private') and m['role'] not in ('owner','admin'):return self.err(403)
    try:c.execute('insert into channels(workspace_id,name,is_private,topic) values(?,?,?,?)',(w['id'],x['name'],int(bool(x.get('is_private'))),x.get('topic','')));ch=c.execute('select last_insert_rowid()').fetchone()[0];c.execute('insert into cm values(?,?)',(ch,uid));c.commit();return self.sendj({'channel':dict(c.execute('select * from channels where id=?',(ch,)).fetchone())},201)
    except sqlite3.IntegrityError:return self.err(409)
   if a[2]=='channels' and len(a)>=5 and a[4]=='leave':
    ch=int(a[3]) if a[3].isdigit() else -1; r=c.execute('select * from channels where id=?',(ch,)).fetchone()
    if not r:return self.err(404)
    c.execute('delete from cm where channel_id=? and user_id=?',(ch,uid)); c.commit()
    return self.sendj({'channel':dict(r)})
   if a[2]=='channels' and len(a)>=5 and a[4]=='join':
    ch=int(a[3]) if a[3].isdigit() else -1; r=c.execute('select * from channels where id=?',(ch,)).fetchone()
    if not r:return self.err(404)
    if r['is_private'] and not c.execute('select 1 from cm where channel_id=? and user_id=?',(ch,uid)).fetchone(): return self.err(403)
    c.execute('insert or ignore into wm(workspace_id,user_id,role) values(?,?,?)',(r['workspace_id'],uid,'member'))
    c.execute('insert or ignore into cm(channel_id,user_id) values(?,?)',(ch,uid)); c.commit()
    return self.sendj({'channel':dict(c.execute('select * from channels where id=?',(ch,)).fetchone())})
   if a[2]=='channels' and len(a)>=5 and a[4] in ('archive','unarchive'):
    ch=int(a[3]) if a[3].isdigit() else -1; r=c.execute('select * from channels where id=?',(ch,)).fetchone()
    if not r:return self.err(404)
    owner=c.execute('select 1 from wm where workspace_id=? and user_id=? and role="owner"',(r['workspace_id'],uid)).fetchone()
    if not owner:return self.err(403)
    val=1 if a[4]=='archive' else 0
    c.execute('update channels set is_archived=? where id=?',(val,ch)); emit(c,ch,'channel.archived' if val else 'channel.unarchived',{'channel':dict(c.execute('select * from channels where id=?',(ch,)).fetchone())}); c.commit()
    return self.sendj({'channel':dict(c.execute('select * from channels where id=?',(ch,)).fetchone())})
   if a[2]=='channels' and len(a)==4 and a[3].isdigit():
    ch=int(a[3]) if a[3].isdigit() else -1;r=c.execute('select * from channels where id=?',(ch,)).fetchone()
    if not r:return self.err(404)
    if r['is_private'] and not c.execute('select 1 from cm where channel_id=? and user_id=?',(ch,uid)).fetchone():return self.err(403)
    w=c.execute('select * from workspaces where id=?',(r['workspace_id'],)).fetchone()
    c.execute('insert or ignore into wm values(?,?,?)',(w['id'],uid,'member'));c.execute('insert or ignore into cm values(?,?)',(ch,uid));c.commit();return self.sendj({'channel':dict(r)})
   if a[2]=='channels' and len(a)==4 and a[3]=='': pass
   if a[2]=='channels' and len(a)>=5 and a[4]=='messages':
    ch=int(a[3]) if a[3].isdigit() else -1;r=c.execute('select * from channels where id=?',(ch,)).fetchone()
    if not r:return self.err(404)
    if not c.execute('select 1 from cm where channel_id=? and user_id=?',(ch,uid)).fetchone():return self.err(403)
    body=x.get('body','') if x else ''
    if r['is_archived'] and not body.startswith('/'):return self.err(423)
    if not body.strip():return self.err(400)
    if body.startswith('/') and not body.startswith('//'):
     parts=body.split(' ',1);cmd=parts[0];arg=parts[1] if len(parts)>1 else ''
     if cmd=='/shrug':body=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
     elif cmd=='/me' and not arg.strip():return self.err(400)
     elif cmd=='/invite':
      if not arg.startswith('@') or not arg[1:]:return self.err(400)
      target=c.execute('select * from users where username=?',(arg[1:].strip(),)).fetchone()
      if not target:return self.err(404)
      if not c.execute('select 1 from wm where workspace_id=? and user_id=?',(r['workspace_id'],target['id'])).fetchone():return self.err(404)
      c.execute('insert or ignore into cm values(?,?)',(ch,target['id']));c.commit()
      return self.sendj({'message':None,'invited_user':user(c,target['id'])},201)
     elif cmd in ('/topic','/archive','/unarchive'):
      if not c.execute('select 1 from wm where workspace_id=? and user_id=? and role="owner"',(r['workspace_id'],uid)).fetchone():return self.err(403)
      if cmd=='/topic':
       if len(arg)>250:return self.err(400)
       c.execute('update channels set topic=? where id=?',(arg,ch))
      elif cmd in ('/archive','/unarchive'):
       c.execute('update channels set is_archived=? where id=?',(cmd=='/archive',ch))
      c.commit()
      channel=dict(c.execute('select * from channels where id=?',(ch,)).fetchone())
      channel['is_archived']=bool(channel.get('is_archived'))
      return self.sendj({'message':None,'channel':channel},201)
     elif cmd not in ('/me',):return self.err(400)
    fids=x.get('file_ids',[]) if x else []
    if not isinstance(fids,list): return self.err(400)
    for fid in fids:
     if not isinstance(fid,int): return self.err(400)
     fr=c.execute('select * from files where id=?',(fid,)).fetchone()
     if not fr or fr['uploader_id']!=uid or c.execute('select 1 from message_files where file_id=?',(fid,)).fetchone(): return self.err(400)
    c.execute('insert into messages(channel_id,author_id,body,parent_id,created_at) values(?,?,?,?,?)',(ch,uid,body,x.get('parent_id') if x else None,now()));mid=c.execute('select last_insert_rowid()').fetchone()[0]
    for fid in fids:c.execute('insert into message_files values(?,?)',(mid,fid))
    o=msgobj(c,c.execute('select * from messages where id=?',(mid,)).fetchone());ev=emit(c,ch,'message.reply' if o['parent_id'] else 'message.created',{'message':o});c.commit();return self.sendj({'message':o},201)
   if a[2]=='messages' and len(a)>=5 and a[4]=='reactions':
    mid=int(a[3]);r=c.execute('select * from messages where id=?',(mid,)).fetchone()
    if not r:return self.err(404)
    if not c.execute('select 1 from cm where channel_id=? and user_id=?',(r['channel_id'],uid)).fetchone():return self.err(403)
    chrow=c.execute('select is_archived from channels where id=?',(r['channel_id'],)).fetchone()
    if chrow and chrow['is_archived']: return self.err(423)
    e=x.get('emoji','') if x else ''
    if not e:return self.err(400)
    if not c.execute('select 1 from reactions where message_id=? and user_id=? and emoji=?',(mid,uid,e)).fetchone():
     c.execute('insert into reactions values(?,?,?)',(mid,uid,e));emit(c,r['channel_id'],'reaction.added',{'message_id':mid,'emoji':e,'user_id':uid});c.commit()
    return self.sendj({'reactions':reactions(c,mid)})
   if a[2]=='dms':
    other=x.get('user_id') if x else None
    if not other or not c.execute('select id from users where id=?',(other,)).fetchone():return self.err(404 if other else 400)
    aa,bb=sorted((uid,other));r=c.execute('select id from channels where is_dm=1 and dm_a=? and dm_b=?',(aa,bb)).fetchone()
    if not r:
     c.execute('insert into channels(is_private,is_dm,name,dm_a,dm_b) values(?,?,?,?,?)',(1,1,'dm',aa,bb));ch=c.execute('select last_insert_rowid()').fetchone()[0];c.execute('insert into cm values(?,?),(?,?)',(ch,aa,ch,bb));c.commit();return self.sendj({'channel_id':ch})
    return self.sendj({'channel_id':r['id']})
   if a[2]=='workspaces' and len(a)>=5 and a[4]=='groups':
    w=c.execute('select * from workspaces where slug=?',(a[3],)).fetchone()
    if not w:return self.err(404)
    if not c.execute('select 1 from wm where workspace_id=? and user_id=?',(w['id'],uid)).fetchone():return self.err(403)
    h=x.get('handle','');n=x.get('name','')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,31}',h) or not n:return self.err(400)
    try:c.execute('insert into groups_tbl(workspace_id,handle,name,creator_id,user_ids) values(?,?,?,?,?)',(w['id'],h,n,uid,json.dumps(x.get('user_ids',[]))));c.commit();r=c.execute('select * from groups_tbl where id=last_insert_rowid()').fetchone();return self.sendj({'group':{'id':r['id'],'handle':h,'name':n,'member_user_ids':json.loads(r['user_ids'])}},201)
    except sqlite3.IntegrityError:return self.err(409)
  finally:c.close()
 def do_DELETE(self):
  uid=auth(self.headers.get('Authorization'));a=self.path.split('/')
  if not uid:return self.err(401)
  if len(a)>=6 and a[2]=='workspaces' and a[4]=='groups':
   c=db()
   try:
    w=c.execute('select * from workspaces where slug=?',(a[3],)).fetchone()
    if not w:return self.err(404)
    g=c.execute('select * from groups_tbl where workspace_id=? and handle=?',(w['id'],a[5])).fetchone()
    if not g:return self.sendj({'deleted':True})
    role=c.execute('select role from wm where workspace_id=? and user_id=?',(w['id'],uid)).fetchone()
    if not role or (g['creator_id']!=uid and role['role']!='owner'):return self.err(403)
    c.execute('delete from groups_tbl where id=?',(g['id'],));c.commit();return self.sendj({'deleted':True})
   finally:c.close()
  if len(a)>=6 and a[2]=='messages' and a[4]=='reactions':
   c=db();mid=int(a[3]);r=c.execute('select * from messages where id=?',(mid,)).fetchone()
   if not r:return self.err(404)
   e=urllib.parse.unquote(a[5]);c.execute('delete from reactions where message_id=? and user_id=? and emoji=?',(mid,uid,e))
   if c.total_changes:emit(c,r['channel_id'],'reaction.removed',{'message_id':mid,'emoji':e,'user_id':uid});c.commit()
   z=reactions(c,mid);c.close();return self.sendj({'reactions':z})
  self.err(404)
 def do_PATCH(self):
  uid=auth(self.headers.get('Authorization'))
  if not uid:return self.err(401)
  a=self.path.split('/');x=self.body();c=db()
  try:
   if a[2]=='users' and a[3]=='me':
    allowed={'display_name':80,'status_text':140,'status_emoji':64,'avatar_url':512,'timezone':100}
    for k,v in x.items():
     if k not in allowed or not isinstance(v,str) or not v or len(v)>allowed[k]:return self.err(400)
    if 'timezone' in x:
     try:__import__('zoneinfo').ZoneInfo(x['timezone'])
     except:return self.err(400)
    c.execute('update users set '+','.join(k+'=?' for k in x)+' where id=?',(*x.values(),uid));c.execute('insert into global_events(payload) values(?)', (json.dumps({'type':'user.updated','user':user(c,uid)}),));c.commit();return self.sendj({'user':user(c,uid)})
   if a[2]=='workspaces' and len(a)>=6 and a[4]=='groups':
    w=c.execute('select * from workspaces where slug=?',(a[3],)).fetchone()
    if not w:return self.err(404)
    g=c.execute('select * from groups_tbl where workspace_id=? and handle=?',(w['id'],a[5])).fetchone()
    if not g:return self.err(404)
    role=c.execute('select role from wm where workspace_id=? and user_id=?',(w['id'],uid)).fetchone()
    if not role or (g['creator_id']!=uid and role['role']!='owner'):return self.err(403)
    if not isinstance(x,dict):return self.err(400)
    c.execute('update groups_tbl set name=?,user_ids=? where id=?',(x.get('name',g['name']),json.dumps(x.get('user_ids',json.loads(g['user_ids']))),g['id']));c.commit()
    q=c.execute('select * from groups_tbl where id=?',(g['id'],)).fetchone()
    return self.sendj({'group':{'id':q['id'],'handle':q['handle'],'name':q['name'],'member_user_ids':json.loads(q['user_ids'])}})
   if a[2]=='channels':
    ch=int(a[3]) if a[3].isdigit() else -1;r=c.execute('select * from channels where id=?',(ch,)).fetchone()
    if not r:return self.err(404)
    if not c.execute('select 1 from cm where channel_id=? and user_id=?',(ch,uid)).fetchone():return self.err(403)
    if 'topic' not in x or len(x['topic'])>250:return self.err(400)
    c.execute('update channels set topic=? where id=?',(x['topic'],ch));c.commit();return self.sendj({'channel':dict(c.execute('select * from channels where id=?',(ch,)).fetchone())})
  finally:c.close()
 def websocket(self):
  uid=auth('Bearer '+urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('token',[''])[0])
  if not uid:return self.send_response(401) or self.end_headers()
  key=self.headers.get('Sec-WebSocket-Key');acc=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',acc);self.end_headers()
  self.connection.settimeout(.2); sub={};last={};seen=int(db().execute('select coalesce(max(id),0) from global_events').fetchone()[0])
  conn_id=secrets.token_hex(12); pc=db(); pc.execute('insert into presence values(?,?,?)',(conn_id,uid,NODE)); pc.commit(); pc.close()
  while True:
   try:
    h=self.connection.recv(2)
    if not h: break
    if h:
     ln=h[1]&127
     if ln==126:ln=struct.unpack('>H',self.connection.recv(2))[0]
     elif ln==127:ln=struct.unpack('>Q',self.connection.recv(8))[0]
     mk=self.connection.recv(4);z=bytes(b^mk[i%4] for i,b in enumerate(self.connection.recv(ln)));q=json.loads(z);ch=q.get('channel_id')
     if q.get('type') in ('subscribe','resume'):
      sub[ch]=1
      headrow=db().execute('select coalesce(last_event_id,0) from channel_seq where channel_id=?',(ch,)).fetchone()
      head=headrow[0] if headrow else 0
      last[ch]=q.get('since_event_id',0) if q.get('type')=='resume' else head
      sendws(self.connection,{'type':'subscribed' if q['type']=='subscribe' else 'resumed','channel_id':ch,'head_event_id':db().execute('select coalesce(last_event_id,0) from channel_seq where channel_id=?',(ch,)).fetchone()[0] if db().execute('select 1 from channel_seq where channel_id=?',(ch,)).fetchone() else 0})
   except TimeoutError: pass
   except Exception: break
   c=db()
   for ch in list(sub):
    for r in c.execute('select payload_json from events where channel_id=? and event_id>? order by event_id',(ch,last.get(ch,0))):
     sendws(self.connection,json.loads(r[0]));last[ch]=json.loads(r[0])['event_id']
   for r in c.execute('select id,payload from global_events where id>? order by id',(seen,)):
    sendws(self.connection,json.loads(r['payload']));seen=r['id']
   c.close()
  try:
   pc=db();pc.execute('delete from presence where conn_id=?',(conn_id,));pc.commit();pc.close()
  except: pass
def sendws(s,o):
 b=json.dumps(o,separators=(',',':')).encode();n=len(b);h=bytes([129,n]) if n<126 else bytes([129,126])+struct.pack('>H',n);s.sendall(h+b)
if __name__=='__main__':
 os.makedirs('/app/data',exist_ok=True);db().close()
 ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
