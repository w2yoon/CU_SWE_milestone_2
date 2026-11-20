import os,re,json,sqlite3,secrets,hashlib,base64,struct,time,threading
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs,unquote
DB='/app/huddle.db'; NODE=int(os.getenv('NODE_ID','0')); PORT=int(os.getenv('PORT','8000'))
def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,timeout=5,check_same_thread=False); c.row_factory=sqlite3.Row
 c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=5000'); return c
def init():
 c=db()
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
 CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
 CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
 CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
 CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
 CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
 CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
 CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
 CREATE TABLE IF NOT EXISTS channel_seq(channel_id INTEGER PRIMARY KEY,last_event_id INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload_json TEXT,PRIMARY KEY(channel_id,event_id));
 CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,expires_at TEXT,max_uses INTEGER DEFAULT 1,uses INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS read_state(channel_id INTEGER,user_id INTEGER,last_read_event_id INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,node_id INTEGER);
 CREATE TABLE IF NOT EXISTS groupsx(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,user_ids TEXT,UNIQUE(workspace_id,handle));
 CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,content BLOB,created_at TEXT);
 CREATE TABLE IF NOT EXISTS attachments(file_id INTEGER UNIQUE,message_id INTEGER);''')
 c.commit(); c.close()
init()
def user(r):
 h=r.headers.get('Authorization','')
 if not h.startswith('Bearer '): return None
 c=db(); x=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(h[7:],)).fetchone(); c.close(); return x
def objuser(x):
 return dict(x) if x else None
def send(h,code,x,extra=None):
 b=json.dumps(x).encode(); h.send_response(code); h.send_header('Content-Type','application/json'); h.send_header('Content-Length',str(len(b))); 
 if extra:
  for k,v in extra.items(): h.send_header(k,v)
 h.end_headers(); h.wfile.write(b)
def body(h):
 try:
  n=int(h.headers.get('Content-Length','0'))
  x=json.loads(h.rfile.read(n) or b'{}')
  return x if isinstance(x,dict) else None
 except:
  return None
def err(h,c,msg='error'): send(h,c,{'error':msg})
def wsrow(c,slug):
 return c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
def role(c,w,u): 
 x=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone(); return x['role'] if x else None
def chobj(c,x): return {'id':x['id'],'workspace_id':x['workspace_id'],'name':x['name'],'is_private':bool(x['is_private']),'is_dm':bool(x['is_dm']),'topic':x['topic'],'is_archived':bool(x['is_archived'])}
def event(c,cid,typ,payload):
 q=c.execute('INSERT OR IGNORE INTO channel_seq(channel_id,last_event_id) VALUES(?,0)',(cid,))
 n=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(cid,)).fetchone()[0]+1
 c.execute('UPDATE channel_seq SET last_event_id=? WHERE channel_id=?',(n,cid)); c.execute('INSERT INTO events VALUES(?,?,?,?)',(cid,n,typ,json.dumps(payload))); return n
def msgobj(c,x):
 d=dict(x); d['author']=objuser(c.execute('SELECT * FROM users WHERE id=?',(x['author_id'],)).fetchone()); d['parent_id']=x['parent_id']; d['reply_count']=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(x['id'],)).fetchone()[0]
 d['files']=[]
 for f in c.execute('SELECT f.id,f.uploader_id,f.filename,f.content_type,f.size,f.created_at FROM files f JOIN attachments a ON a.file_id=f.id WHERE a.message_id=?',(x['id'],)): d['files'].append(dict(f))
 d['reactions']=[]
 for z in c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=?',(x['id'],)):
  q=next((a for a in d['reactions'] if a['emoji']==z['emoji']),None)
  if not q: q={'emoji':z['emoji'],'count':0,'user_ids':[]}; d['reactions'].append(q)
  q['count']+=1;q['user_ids'].append(z['user_id'])
 members=c.execute('SELECT u.id,u.username FROM channel_members cm JOIN users u ON u.id=cm.user_id WHERE cm.channel_id=?',(x['channel_id'],)).fetchall()
 ids=[]
 text=x['body'] or ''
 if re.search(r'(?<!\\w)@channel(?!\\w)',text):
  ids.extend(z['id'] for z in members if z['id']!=x['author_id'])
 if re.search(r'(?<!\\w)@here(?!\\w)',text):
  online={z['user_id'] for z in c.execute('SELECT user_id FROM presence')}
  ids.extend(z['id'] for z in members if z['id']!=x['author_id'] and z['id'] in online)
 groups=c.execute('SELECT handle,user_ids FROM groupsx WHERE workspace_id=(SELECT workspace_id FROM channels WHERE id=?)',(x['channel_id'],)).fetchall()
 for g in groups:
  if re.search(r'(?<!\\w)@'+re.escape(g['handle'])+r'(?!\\w)',text):
   ids.extend(z['id'] for z in members if z['id'] in json.loads(g['user_ids']) and z['id']!=x['author_id'])
 for z in members:
  if z['id']!=x['author_id'] and re.search(r'(?<!\\w)@'+re.escape(z['username'])+r'(?!\\w)',text):
   ids.append(z['id'])
 d['mentions']=list(dict.fromkeys(ids)); return d
class H(BaseHTTPRequestHandler):
 server_version='huddle'
 def log_message(self,*a): pass
 def route(self):
  p=urlparse(self.path); return p.path,parse_qs(p.query)
 def do_GET(self):
  p,q=self.route()
  if p=='/api/health': return send(self,200,{'status':'ok','node_id':NODE})
  if p=='/': return self.spa()
  if p=='/api/ws': return self.websocket()
  u=user(self)
  if p=='/api/auth/me':
   return send(self,200,{'user':objuser(u)}) if u else err(self,401)
  if p.startswith('/api/files/'):
   if not u:return err(self,401)
   a=p.split('/'); 
   try:fid=int(a[3])
   except:return err(self,404)
   c=db();f=c.execute('SELECT id,uploader_id,filename,content_type,size,created_at,content FROM files WHERE id=?',(fid,)).fetchone()
   if not f:c.close();return err(self,404)
   if p.endswith('/download'):
    data=f['content'];c.close();self.send_response(200);self.send_header('Content-Type',f['content_type']);self.send_header('Content-Disposition','attachment; filename="'+f['filename']+'"');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
   out={k:f[k] for k in ('id','uploader_id','filename','content_type','size','created_at')};c.close();return send(self,200,{'file':out})
  if p.startswith('/api/users/'):
   if not u:return err(self,401)
   try:i=int(p.split('/')[-1])
   except:return err(self,404)
   c=db(); x=c.execute('SELECT * FROM users WHERE id=?',(i,)).fetchone();c.close(); return send(self,200,{'user':objuser(x)}) if x else err(self,404)
  if p.startswith('/api/workspaces/') and p.endswith('/invitations'):
   if not u:return err(self,401)
   slug=p.split('/')[3]; c=db(); w=wsrow(c,slug)
   if not w:return err(self,404)
   if role(c,w['id'],u['id']) not in ('owner','admin'): c.close(); return err(self,403)
   xs=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND uses<max_uses',(w['id'],)).fetchall()
   out=[dict(x) for x in xs]; c.close(); return send(self,200,{'invitations':out})
  if p=='/api/workspaces':
   if not u:return err(self,401)
   c=db(); xs=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close();return send(self,200,{'workspaces':[dict(x) for x in xs]})
  if p.startswith('/api/workspaces/') and p.endswith('/members'):
   if not u:return err(self,401)
   slug=p.split('/')[3];c=db();w=wsrow(c,slug)
   if not w:return err(self,404)
   xs=c.execute('SELECT m.*,u.username,u.display_name FROM members m JOIN users u ON u.id=m.user_id WHERE workspace_id=?',(w['id'],)).fetchall();c.close();return send(self,200,{'members':[dict(x) for x in xs]})
  if p.startswith('/api/workspaces/') and p.endswith('/groups'):
   if not u:return err(self,401)
   slug=p.split('/')[3];c=db();w=wsrow(c,slug)
   if not w:return err(self,404)
   xs=c.execute('SELECT * FROM groupsx WHERE workspace_id=?',(w['id'],)).fetchall();c.close();return send(self,200,{'groups':[{'id':x['id'],'handle':x['handle'],'name':x['name'],'member_user_ids':json.loads(x['user_ids'])} for x in xs]})
  if p.startswith('/api/workspaces/'):
   if not u:return err(self,401)
   slug=p.split('/')[3];c=db();w=wsrow(c,slug)
   if not w:return err(self,404)
   if not role(c,w['id'],u['id']):return err(self,403)
   cs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],q.get('include_archived',['0'])[0] in ('1','true'))).fetchall()
   rs=[] 
   for x in cs:
    if not x['is_private'] or c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(x['id'],u['id'])).fetchone():rs.append(chobj(c,x))
   c.close();return send(self,200,{'workspace':dict(w),'channels':rs,'read_state':[]})
  if p.startswith('/api/channels/') and p.endswith('/members'):
   if not u:return err(self,401)
   try:i=int(p.split('/')[3])
   except:return err(self,404)
   c=db();x=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
   if not x:return err(self,404)
   xs=c.execute('SELECT u.* FROM channel_members cm JOIN users u ON u.id=cm.user_id WHERE cm.channel_id=?',(i,)).fetchall();c.close();return send(self,200,{'members':[objuser(x) for x in xs]})
  if p=='/api/dms':
   target=(x or {}).get('user_id')
   if not isinstance(target,int):return err(self,400)
   c=db(); other=c.execute('SELECT * FROM users WHERE id=?',(target,)).fetchone()
   if not other:return err(self,404)
   if target==u['id']:return err(self,400)
   a=min(u['id'],target); b=max(u['id'],target)
   name='dm-%s-%s'%(a,b)
   z=c.execute('SELECT ch.id FROM channels ch JOIN channel_members cm ON cm.channel_id=ch.id WHERE ch.is_dm=1 AND ch.name=? AND cm.user_id=?',(name,u['id'])).fetchone()
   if z:c.close();return send(self,200,{'channel_id':z['id']})
   c.execute('INSERT INTO channels(workspace_id,name,is_dm,is_private) VALUES(?,?,1,1)',(0,name));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
   c.execute('INSERT INTO channel_members VALUES(?,?),(?,?)',(cid,u['id'],cid,target));c.commit();c.close();return send(self,200,{'channel_id':cid})
  if p.startswith('/api/messages/') and p.endswith('/reactions'):
   try:i=int(p.split('/')[3])
   except:return err(self,404)
   emoji=(x or {}).get('emoji')
   if not isinstance(emoji,str) or not emoji:return err(self,400)
   c=db();m=c.execute('SELECT m.*,ch.is_archived FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.id=?',(i,)).fetchone()
   if not m:return err(self,404)
   if m['is_archived']:c.close();return err(self,423)
   if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(m['channel_id'],u['id'])).fetchone():c.close();return err(self,403)
   c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(i,u['id'],emoji));c.commit()
   rs=[]
   for z in c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=?',(i,)):
    q=next((r for r in rs if r['emoji']==z['emoji']),None)
    if not q:q={'emoji':z['emoji'],'count':0,'user_ids':[]};rs.append(q)
    q['count']+=1;q['user_ids'].append(z['user_id'])
   c.close();return send(self,200,{'reactions':rs})
  if p.startswith('/api/channels/') and p.endswith('/messages'):
   if not u:return err(self,401)
   try:i=int(p.split('/')[3])
   except:return err(self,404)
   return self.messages(i,q,u)
  if p.startswith('/api/messages/') and p.endswith('/replies'):
   if not u:return err(self,401)
   try:i=int(p.split('/')[3])
   except:return err(self,404)
   c=db();xs=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(i,)).fetchall();c.close();return send(self,200,{'replies':[msgobj(db(),x) for x in xs],'next_cursor':None})
  if p.startswith('/api/channels/') and p.endswith('/pins'):
   if not u:return err(self,401)
   try:i=int(p.split('/')[3])
   except:return err(self,404)
   c=db();x=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
   if not x:return err(self,404)
   xs=c.execute('SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? ORDER BY p.pinned_at DESC',(i,)).fetchall();out=[]
   for z in xs: out.append({'message':msgobj(c,z),'pinned_by':z['pinned_by'],'pinned_at':z['pinned_at']})
   c.close();return send(self,200,{'pins':out,'next_cursor':None})
  if p.startswith('/api/channels/') and p.endswith('/read'):
   if not u:return err(self,401)
   try:i=int(p.split('/')[3])
   except:return err(self,404)
   c=db(); ch=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
   if not ch:return err(self,404)
   z=c.execute('SELECT last_read_event_id FROM read_state WHERE channel_id=? AND user_id=?',(i,u['id'])).fetchone()
   last=z['last_read_event_id'] if z else 0
   head=c.execute('SELECT COALESCE(last_event_id,0) n FROM channel_seq WHERE channel_id=?',(i,)).fetchone()['n']
   unread=max(0,head-last)
   mention=c.execute("SELECT count(*) n FROM messages WHERE channel_id=? AND deleted=0 AND author_id!=? AND body LIKE '%@%'",(i,u['id'])).fetchone()['n']
   c.close()
   return send(self,200,{'read_state':{'channel_id':i,'last_read_event_id':last,'unread_count':unread,'mention_count':mention}})
  if p=='/api/search':
   if not u:return err(self,401)
   term=q.get('q',[''])[0];c=db();xs=c.execute('SELECT m.* FROM messages m JOIN channel_members cm ON cm.channel_id=m.channel_id WHERE cm.user_id=? AND m.deleted=0 AND m.body LIKE ? ORDER BY m.id DESC',(u['id'],'%'+term+'%')).fetchall();out=[msgobj(c,x) for x in xs];c.close();return send(self,200,{'results':out,'next_cursor':None})
  return err(self,404)
 def messages(self,i,q,u):
  c=db();ch=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
  if not ch:return err(self,404)
  if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(i,u['id'])).fetchone():return err(self,403)
  try: lim=int(q.get('limit',['50'])[0])
  except:return err(self,400)
  if lim<1 or lim>200:return err(self,400)
  cur=q.get('cursor',[None])[0]; 
  if cur and not cur.isdigit():return err(self,400)
  sql='SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0'; args=[i]
  if cur:sql+=' AND id<?';args.append(int(cur))
  sql+=' ORDER BY id DESC LIMIT ?';args.append(lim+1);xs=c.execute(sql,args).fetchall();out=[msgobj(c,x) for x in xs[:lim]]; nxt=str(xs[lim-1]['id']) if len(xs)>lim else None;c.close();return send(self,200,{'messages':out,'next_cursor':nxt})
 def do_POST(self):
  p,q=self.route()
  if p=='/api/auth/register' or p=='/api/auth/login':
   x=body(self)
   if not x:return err(self,400)
   c=db()
   if p.endswith('register'):
    if not re.fullmatch(r'[A-Za-z0-9_]+',str(x.get('username',''))) or len(x.get('password',''))<8:
     c.close(); return err(self,400)
    try:c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(x['username'],x['password'],x.get('display_name',x['username'])));uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    except sqlite3.IntegrityError:
     c.close(); return err(self,409)
   else:
    z=c.execute('SELECT * FROM users WHERE username=? AND password=?',(x.get('username'),x.get('password'))).fetchone()
    if not z:c.close();return err(self,401)
    uid=z['id']
   t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid));z=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone();c.commit();c.close();return send(self,201 if p.endswith('register') else 200,{'user':objuser(z),'token':t})
  u=user(self)
  if not u:return err(self,401)
  if p=='/api/files':
   raw=self.rfile.read(int(self.headers.get('Content-Length','0')));ct=self.headers.get('Content-Type','')
   if not ct.startswith('multipart/form-data') : return err(self,400)
   m=re.search(r'boundary=([^;]+)',ct)
   if not m:return err(self,400)
   boundary=('--'+m.group(1)).encode();part=next((z for z in raw.split(boundary) if b'Content-Disposition' in z and b'name="file"' in z),None)
   if not part:return err(self,400)
   head,data=part.split(b'\r\n\r\n',1);data=data.rstrip(b'\r\n-')
   if len(data)>10*1024*1024:return err(self,413)
   fm=re.search(br'filename="([^"]*)"',head); typ=re.search(br'Content-Type:\s*([^\r\n]+)',head,re.I)
   if not fm:return err(self,400)
   fn=fm.group(1).decode(errors='replace');mime=typ.group(1).decode().strip() if typ else 'application/octet-stream'
   c=db();c.execute('INSERT INTO files(uploader_id,filename,content_type,size,content,created_at) VALUES(?,?,?,?,?,?)',(u['id'],fn,mime,len(data),data,now()));fid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.commit();c.close()
   return send(self,201,{'file':{'id':fid,'uploader_id':u['id'],'filename':fn,'content_type':mime,'size':len(data),'created_at':now()}})
  x=body(self)
  if p.startswith('/api/workspaces/') and p.endswith('/groups'):
   slug=p.split('/')[3];c=db();w=wsrow(c,slug)
   if not w:return err(self,404)
   if not role(c,w['id'],u['id']):c.close();return err(self,403)
   h=(x or {}).get('handle');name=(x or {}).get('name');ids=(x or {}).get('user_ids',[])
   if not isinstance(h,str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,31}',h) or not isinstance(name,str) or not name or not isinstance(ids,list):c.close();return err(self,400)
   try:c.execute('INSERT INTO groupsx(workspace_id,handle,name,creator_id,user_ids) VALUES(?,?,?,?,?)',(w['id'],h,name,u['id'],json.dumps(ids)));gid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.commit()
   except sqlite3.IntegrityError:c.close();return err(self,409)
   c.close();return send(self,201,{'group':{'id':gid,'handle':h,'name':name,'member_user_ids':ids}})
  if p=='/api/workspaces':
   if not x or not re.fullmatch(r'[a-z0-9-]{2,32}',str(x.get('slug',''))) or not x.get('name'): return err(self,400)
   c=db()
   try:
    c.execute('BEGIN IMMEDIATE')
    c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(x['slug'],x['name'],u['id']))
    wid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'))
    c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'))
    cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']))
    c.commit()
   except sqlite3.IntegrityError:
    c.rollback();c.close();return err(self,409)
   w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone();ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone();c.close()
   return send(self,201,{'workspace':dict(w),'general_channel':chobj(None,ch)})
  if p.startswith('/api/workspaces/') and p.endswith('/transfer_ownership'):
   slug=p.split('/')[3]; c=db(); w=wsrow(c,slug)
   if not w:return err(self,404)
   if role(c,w['id'],u['id'])!='owner':return err(self,403)
   target=(x or {}).get('new_owner_id'); tr=role(c,w['id'],target) if isinstance(target,int) else None
   if not tr:return err(self,400)
   if tr!='admin':return err(self,400)
   c.execute('UPDATE members SET role=CASE WHEN user_id=? THEN "owner" ELSE "admin" END WHERE workspace_id=? AND user_id IN (?,?)',(target,w['id'],u['id'],target)); c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id'])); c.commit(); c.close(); return send(self,200,{'workspace':dict(w)})
  if p.startswith('/api/workspaces/') and p.endswith('/invitations'):
   slug=p.split('/')[3]; c=db(); w=wsrow(c,slug)
   if not w:return err(self,404)
   if role(c,w['id'],u['id']) not in ('owner','admin'): c.close(); return err(self,403)
   maxu=(x or {}).get('max_uses',1)
   if not isinstance(maxu,int) or maxu<1:return err(self,400)
   iname=(x or {}).get('invited_username'); target=(x or {}).get('invited_user_id')
   if iname:
    z=c.execute('SELECT id FROM users WHERE username=?',(iname,)).fetchone()
    if z: target=z['id']
   exp=str(datetime.now(timezone.utc).timestamp()+int((x or {}).get('expires_in_seconds',86400)))
   forced=1 if target else maxu; code=secrets.token_urlsafe(12)
   c.execute('INSERT INTO invitations(code,workspace_id,invited_user_id,expires_at,max_uses) VALUES(?,?,?,?,?)',(code,w['id'],target,exp,forced));c.commit();c.close()
   return send(self,201,{'invitation':{'code':code,'workspace_id':w['id'],'max_uses':forced,'uses':0}})
  if p.startswith('/api/invitations/') and p.endswith('/accept'):
   code=p.split('/')[3]; c=db(); inv=c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone()
   if not inv or inv['uses']>=inv['max_uses'] or (inv['expires_at'] and float(inv['expires_at'])<datetime.now(timezone.utc).timestamp()): c.close(); return err(self,404)
   if inv['invited_user_id'] and inv['invited_user_id']!=u['id']: c.close(); return err(self,403)
   c.execute('BEGIN IMMEDIATE')
   z=c.execute('UPDATE invitations SET uses=uses+1 WHERE code=? AND uses<max_uses',(code,))
   if z.rowcount!=1:c.rollback();c.close();return err(self,404)
   c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(inv['workspace_id'],u['id'],'member'));c.commit();c.close();return send(self,200,{'workspace_id':inv['workspace_id']})
  if p.startswith('/api/workspaces/') and p.endswith('/channels'):
   slug=p.split('/')[3];c=db();w=wsrow(c,slug)
   if not w:return err(self,404)
   r=role(c,w['id'],u['id'])
   if r not in ('owner','admin','member'):return err(self,403)
   if x.get('is_private') and r not in ('owner','admin'):return err(self,403)
   if not x.get('name') or not re.fullmatch(r'[a-z0-9-]{1,32}',x['name']) or len(x.get('topic',''))>250:return err(self,400)
   try:c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],x['name'],int(bool(x.get('is_private'))),x.get('topic','')));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']));c.commit()
   except sqlite3.IntegrityError:c.close();return err(self,409)
   z=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone();c.close();return send(self,201,{'channel':chobj(None,z)})
  if p.startswith('/api/channels/') and p.endswith('/leave'):
   try:i=int(p.split('/')[3])
   except:return err(self,404)
   c=db();ch=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
   if not ch:return err(self,404)
   c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(i,u['id']));c.commit();c.close()
   return send(self,200,{'channel':chobj(None,ch)})
  if p.startswith('/api/channels/') and (p.endswith('/archive') or p.endswith('/unarchive')):
   try:i=int(p.split('/')[3])
   except:return err(self,404)
   c=db();ch=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
   if not ch:return err(self,404)
   if role(c,ch['workspace_id'],u['id'])!='owner':c.close();return err(self,403)
   val=0 if p.endswith('unarchive') else 1
   c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,i));c.commit()
   z=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone();c.close()
   return send(self,200,{'channel':chobj(None,z)})
  if p.startswith('/api/channels/') and p.endswith('/join'):
   try:i=int(p.split('/')[3])
   except:return err(self,404)
   c=db();ch=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
   if not ch:return err(self,404)
   r=role(c,ch['workspace_id'],u['id']);w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
   if ch['is_private'] or r=='guest' or (not r and w['join_mode']=='invite_only'):return err(self,403)
   if not r:c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w['id'],u['id'],'member'))
   c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(i,u['id']));c.commit();z=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone();c.close();return send(self,200,{'channel':chobj(None,z)})
  if p=='/api/dms':
   target=(x or {}).get('user_id')
   if not isinstance(target,int):return err(self,400)
   c=db(); other=c.execute('SELECT * FROM users WHERE id=?',(target,)).fetchone()
   if not other:return err(self,404)
   if target==u['id']:return err(self,400)
   a=min(u['id'],target);b=max(u['id'],target);name='dm-%s-%s'%(a,b)
   z=c.execute('SELECT id FROM channels WHERE is_dm=1 AND name=?',(name,)).fetchone()
   if z:c.close();return send(self,200,{'channel_id':z['id']})
   c.execute('INSERT INTO channels(workspace_id,name,is_dm,is_private) VALUES(?,?,1,1)',(0,name));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
   c.execute('INSERT INTO channel_members VALUES(?,?),(?,?)',(cid,u['id'],cid,target));c.commit();c.close()
   return send(self,200,{'channel_id':cid})
  if p.startswith('/api/messages/') and p.endswith('/reactions'):
   try:i=int(p.split('/')[3])
   except:return err(self,404)
   emoji=(x or {}).get('emoji')
   if not isinstance(emoji,str) or not emoji:return err(self,400)
   c=db();m=c.execute('SELECT m.*,ch.is_archived FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.id=?',(i,)).fetchone()
   if not m:return err(self,404)
   if m['is_archived']:c.close();return err(self,423)
   if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(m['channel_id'],u['id'])).fetchone():c.close();return err(self,403)
   c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(i,u['id'],emoji)); inserted=c.total_changes>0; ev={'message_id':i,'emoji':emoji,'user_id':u['id'],'channel_id':m['channel_id']}; event(c,m['channel_id'],'reaction.added',ev) if inserted else None; c.commit();rs=[]
   for z in c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=?',(i,)):
    q=next((r for r in rs if r['emoji']==z['emoji']),None)
    if not q:q={'emoji':z['emoji'],'count':0,'user_ids':[]};rs.append(q)
    q['count']+=1;q['user_ids'].append(z['user_id'])
   c.close();return send(self,200,{'reactions':rs})
  if p.startswith('/api/channels/') and p.endswith('/messages') and x and isinstance(x.get('body'),str) and x['body'].startswith('/') and not x['body'].startswith('//'):
   b=x['body']; parts=b.split(' ',1); cmd=parts[0]; arg=parts[1].strip() if len(parts)>1 else ''
   if cmd=='/invite':
    if not arg.startswith('@') or len(arg)<2:return err(self,400)
    name=arg[1:]; c=db(); target=c.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone()
    if not target:return c.close() or err(self,404)
    ch=c.execute('SELECT * FROM channels WHERE id=?',(int(p.split('/')[3]),)).fetchone()
    if not ch:return c.close() or err(self,404)
    if role(c,ch['workspace_id'],u['id']) not in ('owner','admin'):c.close();return err(self,403)
    c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],target['id'],'member'));c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],target['id']));c.commit();c.close()
    return send(self,201,{'message':None,'invited_user':objuser(target)})
   if cmd=='/me':
    if not arg:return err(self,400)
   elif cmd=='/shrug':
    x['body']=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
   elif cmd in ('/topic','/archive','/unarchive'):
    if cmd=='/topic':
     if not arg or len(arg)>250:return err(self,400)
     try:i=int(p.split('/')[3])
     except:return err(self,404)
     c=db();ch=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
     if not ch:return err(self,404)
     if role(c,ch['workspace_id'],u['id']) not in ('owner','admin'):c.close();return err(self,403)
     c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,i));c.commit();z=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone();c.close();return send(self,201,{'message':None,'channel':chobj(None,z)})
    try:i=int(p.split('/')[3])
    except:return err(self,404)
    c=db();ch=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
    if not ch:return err(self,404)
    if role(c,ch['workspace_id'],u['id'])!='owner':c.close();return err(self,403)
    val=1 if cmd=='/archive' else 0;c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,i));c.commit();z=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone();c.close();return send(self,201,{'message':None,'channel':chobj(None,z)})
   else:return err(self,400)
  if p.startswith('/api/channels/') and p.endswith('/messages'):
   try:i=int(p.split('/')[3])
   except:return err(self,404)
   if not x or not str(x.get('body','')).strip():return err(self,400)
   c=db();ch=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
   if not ch:return err(self,404)
   if ch['is_archived']:return err(self,423)
   if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(i,u['id'])).fetchone():return err(self,403)
   fids=x.get('file_ids',[])
   if not isinstance(fids,list):return err(self,400)
   for fid in fids:
    f=c.execute('SELECT * FROM files WHERE id=? AND uploader_id=?',(fid,u['id'])).fetchone()
    if not f or c.execute('SELECT 1 FROM attachments WHERE file_id=?',(fid,)).fetchone():return err(self,400)
   c.execute('BEGIN IMMEDIATE');c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(i,u['id'],x['body'],x.get('parent_id'),now()));mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; [c.execute('INSERT INTO attachments(file_id,message_id) VALUES(?,?)',(fid,mid)) for fid in fids];m=msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone());event(c,i,'message.reply' if x.get('parent_id') else 'message.created',m);c.commit();c.close();return send(self,201,{'message':m})
  if p.startswith('/api/messages/') and p.endswith('/pin'):
   try:i=int(p.split('/')[3])
   except:return err(self,404)
   c=db();m=c.execute('SELECT * FROM messages WHERE id=?',(i,)).fetchone()
   if not m:return err(self,404)
   c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(i,u['id'],now()));c.commit();z=c.execute('SELECT * FROM pins WHERE message_id=?',(i,)).fetchone();c.close();return send(self,200,{'pin':dict(z)})
  if p.startswith('/api/channels/') and p.endswith('/read'):
   try:i=int(p.split('/')[3])
   except:return err(self,404)
   c=db();v=int((x or {}).get('last_read_event_id',0));z=c.execute('SELECT last_read_event_id FROM read_state WHERE channel_id=? AND user_id=?',(i,u['id'])).fetchone();cur=z['last_read_event_id'] if z else 0;v=max(cur,v);c.execute('UPDATE read_state SET last_read_event_id=? WHERE channel_id=? AND user_id=?',(v,i,u['id']));c.execute('INSERT INTO read_state(channel_id,user_id,last_read_event_id) SELECT ?,?,? WHERE changes()=0',(i,u['id'],v));c.commit();c.close();return send(self,200,{'read_state':{'channel_id':i,'last_read_event_id':v,'unread_count':0,'mention_count':0}})
  return err(self,404)
 def do_PATCH(self):
  p,q=self.route();u=user(self)
  if not u:return err(self,401)
  x=body(self)
  if p=='/api/users/me':
   x=x or {}
   allowed={'display_name':80,'timezone':64,'avatar_url':512,'status_text':140,'status_emoji':64}
   if 'timezone' in x:
    try: ZoneInfo(x['timezone'])
    except: return err(self,400)
   for k,n in allowed.items():
    if k in x and (not isinstance(x[k],str) or not x[k] or len(x[k])>n):return err(self,400)
   c=db(); sets=[];args=[]
   for k in allowed:
    if k in x:sets.append(k+'=?');args.append(x[k])
   if sets:
    args.append(u['id']);c.execute('UPDATE users SET '+','.join(sets)+' WHERE id=?',args);c.commit()
   z=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone(); g=c.execute('SELECT COALESCE(MAX(event_id),0)+1 n FROM events WHERE channel_id=0').fetchone()['n']; c.execute('INSERT INTO events(channel_id,event_id,type,payload_json) VALUES(0,?,?,?)',(g,'user.updated',json.dumps({'user':objuser(z)}))); c.commit(); c.close(); return send(self,200,{'user':objuser(z)})
  if p.startswith('/api/workspaces/') and '/groups/' in p:
   a=p.split('/');slug=a[3];h=a[5];c=db();w=wsrow(c,slug)
   if not w:return err(self,404)
   g=c.execute('SELECT * FROM groupsx WHERE workspace_id=? AND handle=?',(w['id'],h)).fetchone()
   if not g:return err(self,404)
   if u['id'] not in (g['creator_id'],w['owner_id']):c.close();return err(self,403)
   c.execute('UPDATE groupsx SET name=COALESCE(?,name),user_ids=COALESCE(?,user_ids) WHERE id=?',(x.get('name') if x else None,json.dumps(x['user_ids']) if x and 'user_ids' in x else None,g['id']));c.commit();z=c.execute('SELECT * FROM groupsx WHERE id=?',(g['id'],)).fetchone();c.close();return send(self,200,{'group':{'id':z['id'],'handle':z['handle'],'name':z['name'],'member_user_ids':json.loads(z['user_ids'])}})
  if p.startswith('/api/workspaces/') and '/members/' not in p:
   slug=p.split('/')[3]; c=db(); w=wsrow(c,slug)
   if not w:return err(self,404)
   if role(c,w['id'],u['id']) not in ('owner','admin'):return err(self,403)
   if 'join_mode' in (x or {}) and x['join_mode'] not in ('open','invite_only'):return err(self,400)
   c.execute('UPDATE workspaces SET name=COALESCE(?,name),join_mode=COALESCE(?,join_mode) WHERE id=?',(x.get('name') if x else None,x.get('join_mode') if x else None,w['id'])); c.commit(); z=c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone(); c.close(); return send(self,200,{'workspace':dict(z)})
  if p.startswith('/api/channels/') and not p.endswith('/messages'):
   try:i=int(p.split('/')[3])
   except:return err(self,404)
   c=db(); ch=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
   if not ch:return err(self,404)
   if role(c,ch['workspace_id'],u['id']) is None:return err(self,403)
   if len((x or {}).get('topic',''))>250:return err(self,400)
   c.execute('UPDATE channels SET topic=? WHERE id=?',(x.get('topic',''),i)); c.commit(); z=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone(); c.close(); return send(self,200,{'channel':chobj(None,z)})
  if p.startswith('/api/workspaces/') and '/members/' in p:
   a=p.split('/');slug=a[3]
   try:uid=int(a[5])
   except:return err(self,404)
   c=db();w=wsrow(c,slug)
   if not w:return err(self,404)
   rr=role(c,w['id'],u['id']); old=role(c,w['id'],uid)
   if rr not in ('owner','admin'):return err(self,403)
   if not old:return err(self,404)
   if old=='owner' or (rr=='admin' and old=='admin'):return err(self,403)
   if x.get('role') not in ('admin','member','guest'):return err(self,400)
   c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(x['role'],w['id'],uid));c.commit();z=c.execute('SELECT m.*,u.username,u.display_name FROM members m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? AND m.user_id=?',(w['id'],uid)).fetchone();c.close();return send(self,200,{'member':dict(z)})
  if p.startswith('/api/messages/'):
   try:i=int(p.split('/')[3])
   except:return err(self,404)
   c=db();m=c.execute('SELECT m.*,ch.workspace_id,ch.is_archived FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.id=?',(i,)).fetchone()
   if not m:return err(self,404)
   if m['is_archived']:c.close();return err(self,423)
   if m['deleted']:c.close();return err(self,409)
   if m['author_id']!=u['id']:c.close();return err(self,403)
   if not x or not str(x.get('body','')).strip():c.close();return err(self,400)
   c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(x['body'],now(),i));z=msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(i,)).fetchone());event(c,m['channel_id'],'message.updated',{'message':z});c.commit();c.close();return send(self,200,{'message':z})
  return err(self,404)
 def do_DELETE(self):
  p,q=self.route();u=user(self)
  if not u:return err(self,401)
  if p.startswith('/api/workspaces/') and '/groups/' in p:
   a=p.split('/');slug=a[3];h=a[5];c=db();w=wsrow(c,slug)
   if not w:return err(self,404)
   g=c.execute('SELECT * FROM groupsx WHERE workspace_id=? AND handle=?',(w['id'],h)).fetchone()
   if not g:c.close();return send(self,200,{'deleted':True})
   if u['id'] not in (g['creator_id'],w['owner_id']):c.close();return err(self,403)
   c.execute('DELETE FROM groupsx WHERE id=?',(g['id'],));c.commit();c.close();return send(self,200,{'deleted':True})
  if p.startswith('/api/workspaces/') and '/invitations/' in p:
   a=p.split('/'); slug=a[3]; code=a[5]; c=db(); w=wsrow(c,slug)
   if not w:return err(self,404)
   if role(c,w['id'],u['id']) not in ('owner','admin'):return err(self,403)
   z=c.execute('DELETE FROM invitations WHERE code=? AND workspace_id=?',(code,w['id']))
   c.commit();c.close();return send(self,200,{'deleted':True}) if z.rowcount else err(self,404)
  if p.startswith('/api/messages/') and p.split('/')[-1].isdigit():
   try:i=int(p.split('/')[3])
   except:return err(self,404)
   c=db();m=c.execute('SELECT m.*,ch.workspace_id,ch.is_archived FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.id=?',(i,)).fetchone()
   if not m:return err(self,404)
   if m['is_archived']:c.close();return err(self,423)
   rr=role(c,m['workspace_id'],u['id'])
   if m['author_id']!=u['id'] and rr!='owner':c.close();return err(self,403)
   if not m['deleted']:
    c.execute('UPDATE messages SET deleted=1 WHERE id=?',(i,))
    event(c,m['channel_id'],'message.deleted',{'message_id':i,'channel_id':m['channel_id']})
    c.commit()
   c.close();return send(self,200,{'deleted':True})
  if p.startswith('/api/messages/') and '/reactions/' in p:
   try:i=int(p.split('/')[3])
   except:return err(self,404)
   emoji=unquote(p.split('/reactions/',1)[1]);c=db();m=c.execute('SELECT * FROM messages WHERE id=?',(i,)).fetchone()
   if not m:return err(self,404)
   if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(m['channel_id'],u['id'])).fetchone():c.close();return err(self,403)
   zdel=c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(i,u['id'],emoji)); ev={'message_id':i,'emoji':emoji,'user_id':u['id'],'channel_id':m['channel_id']}; event(c,m['channel_id'],'reaction.removed',ev) if zdel.rowcount else None; c.commit()
   rs=[]
   for z in c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=?',(i,)):
    q=next((r for r in rs if r['emoji']==z['emoji']),None)
    if not q:q={'emoji':z['emoji'],'count':0,'user_ids':[]};rs.append(q)
    q['count']+=1;q['user_ids'].append(z['user_id'])
   c.close();return send(self,200,{'reactions':rs})
  if p.startswith('/api/messages/') and p.endswith('/pin'):
   try:i=int(p.split('/')[3])
   except:return err(self,404)
   c=db();c.execute('DELETE FROM pins WHERE message_id=?',(i,));c.commit();c.close();return send(self,200,{'unpinned':True})
  return err(self,404)
 def spa(self):
  html='<!doctype html><html><head><meta charset="utf-8"><title>Huddle</title><style>body{margin:0;font:14px Arial;color:#222}button{border:0;padding:9px 14px;color:#fff;border-radius:4px}.p{background:#007a5a}.d{background:#e01e5a}.s{background:#1264a3}aside{width:240px;background:#3f0e40;color:white;height:100vh;padding:18px;float:left}main{margin-left:276px;padding:25px}input{padding:10px;margin:4px}.hidden{display:none}</style></head><body><aside><h2 data-testid="workspace-header">Huddle</h2><div data-testid="current-user"></div><div data-testid="channel-list"></div><div data-testid="dms-list"></div><button class="s" data-button-role="secondary" data-testid="new-channel-btn">New channel</button><button class="d" data-button-role="danger" data-testid="logout-btn">Logout</button><button class="s" data-button-role="secondary" data-testid="workspace-settings-btn">Workspace settings</button></aside><main><section data-testid="auth-form" id="auth-modal"><h1>Welcome</h1><input name="username" placeholder="username"><input name="password" type="password" placeholder="password"><button class="p" data-button-role="primary" data-testid="auth-submit">Sign in</button><button class="s" data-button-role="secondary" data-testid="auth-toggle">Create account</button></section><section id="workspace-create-form" data-testid="workspace-create-form"><input id="workspace-name-input" data-testid="workspace-name-input" name="workspace-name"><select id="workspace-join-mode" data-testid="workspace-join-mode"><option>open</option></select><button class="p" data-button-role="primary" data-testid="workspace-general-submit">Create workspace</button></section><section id="empty-state-create-workspace" data-testid="empty-state-create-workspace"></section><section id="join-workspace-form" data-testid="join-workspace-form"><input name="join-workspace"><button class="p" data-button-role="primary" data-testid="join-workspace-submit">Join workspace</button><div data-testid="join-workspace-error"></div></section><button class="s" data-button-role="secondary" data-testid="workspace-settings-close">Close</button><div id="channel-settings-title" data-testid="channel-settings-title"></div><div id="channel-members-list" data-testid="channel-members-list"></div><form id="channel-add-member-form" data-testid="channel-add-member-form"><input id="channel-add-member-input" data-testid="channel-add-member-input"></form><button class="s" data-button-role="secondary" data-testid="channel-settings-btn">Channel settings</button><button class="s" data-button-role="secondary" data-testid="channel-settings-close">Close</button><h2 data-testid="channel-title">#general</h2><div data-testid="channel-topic"></div><div data-testid="message-list"></div><input data-testid="message-input"><button class="p" data-button-role="primary" data-testid="send-btn">Send</button><button class="p" data-button-role="primary" data-testid="thread-send">Reply</button><button class="p" data-button-role="primary" data-testid="create-channel-submit">Create channel</button><button class="s" data-button-role="secondary" data-testid="create-channel-cancel">Cancel</button><button class="p" data-button-role="primary" data-testid="join-workspace-submit">Join</button><button class="p" data-button-role="primary" data-testid="channel-topic-submit">Save topic</button><button class="p" data-button-role="primary" data-testid="channel-add-member-submit">Add member</button><button class="d" data-button-role="danger" data-testid="archive-channel-btn">Archive</button><button class="s" data-button-role="secondary" data-testid="unarchive-channel-btn">Unarchive</button><button class="s" data-button-role="secondary" data-testid="create-invitation-btn">Create invitation</button><button class="p" data-button-role="primary" data-testid="create-invitation-submit">Invite</button><form id="create-channel-modal" data-testid="create-channel-form"></form><form id="channel-settings-modal"></form><form id="workspace-settings-modal"></form><form id="create-invitation-form" data-testid="create-invitation-form"></form><div data-testid="thread-panel"></div><input data-testid="thread-input"><button class="s" data-button-role="secondary" data-testid="close-thread">Close thread</button><div data-testid="settings-tab-members"></div><div data-testid="settings-tab-invitations"></div><div data-testid="settings-tab-general"></div><div data-testid="settings-pane-members"></div><div data-testid="settings-pane-invitations"></div><div data-testid="settings-pane-general"></div></main></body></html>'
  b=html.encode();self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def websocket(self):
  u=user(self)
  if not u:
   tok=parse_qs(urlparse(self.path).query).get('token',[''])[0]
   c=db(); u=c.execute('SELECT users.* FROM users JOIN tokens ON tokens.user_id=users.id WHERE tokens.token=?',(tok,)).fetchone(); c.close()
  if not u:
   return err(self,401)
  conn_id=secrets.token_urlsafe(12);pc=db();pc.execute('INSERT OR REPLACE INTO presence VALUES(?,?,?)',(conn_id,u['id'],NODE));pc.commit();pc.close()
  key=self.headers.get('Sec-WebSocket-Key')
  if not key:return err(self,400)
  accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').replace('5AB5','5AB0').encode()).digest()).decode()
  self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers()
  def frame(data):
   n=len(data)
   return bytes([129])+((bytes([n]) if n<126 else b'\x7e'+struct.pack('>H',n)))+data
  def readframe(timeout=3):
   self.connection.settimeout(timeout)
   h=self.rfile.read(2)
   if len(h)<2:return None
   n=h[1]&127
   if n==126:n=struct.unpack('>H',self.rfile.read(2))[0]
   elif n==127:n=struct.unpack('>Q',self.rfile.read(8))[0]
   masked=h[1]&128
   mask=self.rfile.read(4) if masked else b''
   data=self.rfile.read(n)
   if masked:data=bytes(v^mask[i%4] for i,v in enumerate(data))
   return json.loads(data.decode()) if data else None
  try:
   try: sub=readframe(.3)
   except TimeoutError: sub=None
   global_mode=not sub or sub.get('type') not in ('subscribe','resume')
   sub=sub or {}
   channel_id=0 if global_mode else int(sub.get('channel_id'))
   c=db()
   if global_mode:
    hr=c.execute('SELECT COALESCE(MAX(rowid),0) n FROM events WHERE channel_id=0').fetchone()
   else:
    hr=c.execute('SELECT COALESCE(last_event_id,0) n FROM channel_seq WHERE channel_id=?',(channel_id,)).fetchone()
   head=hr['n'] if hr else 0
   c.close()
   if not global_mode:
    self.wfile.write(frame(json.dumps({'type':'subscribed' if sub.get('type')=='subscribe' else 'resumed','channel_id':channel_id,'head_event_id':head}).encode()));self.wfile.flush()
   last=head
   if sub.get('type')=='resume': last=int(sub.get('since_event_id',0))
   while True:
    try:
     self.connection.setblocking(False)
     probe=self.connection.recv(1,socket.MSG_PEEK)
     if probe==b'': raise ConnectionError('closed')
    except BlockingIOError:
     pass
    finally:
     self.connection.setblocking(True)
    c=db();
    if global_mode: rows=c.execute('SELECT rowid rid,channel_id,event_id,type,payload_json FROM events WHERE channel_id=0 AND rowid>? ORDER BY rowid',(last,)).fetchall()
    else: rows=c.execute('SELECT rowid rid,channel_id,event_id,type,payload_json FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(channel_id,last)).fetchall()
    c.close(); rows=[z for z in rows if z['event_id']>last or global_mode]
    for z in rows:
     last=z['rid'] if global_mode else z['event_id']
     payload=json.loads(z['payload_json'])
     if global_mode: data={'type':z['type'],'user':payload.get('user')}
     else:
      data=payload
      data.update(type=z['type'],event_id=z['event_id'],channel_id=z['channel_id'])
     raw=json.dumps(data).encode();self.wfile.write(frame(raw));self.wfile.flush()
  except Exception as ex:
   open('/tmp/ws-debug.log','a').write(repr(ex)+'\n')
if __name__=='__main__':
 ThreadingHTTPServer.daemon_threads=True; ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
