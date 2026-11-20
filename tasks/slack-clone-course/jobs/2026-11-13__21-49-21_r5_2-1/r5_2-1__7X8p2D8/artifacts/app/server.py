import os,json,re,urllib.parse,hashlib,base64,threading,sqlite3,sys,struct,socket
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
import core
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a): pass
 def sendj(self,code,d):
  b=json.dumps(d).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def body(self):
  n=int(self.headers.get('Content-Length',0)); b=self.rfile.read(n)
  try:d=json.loads(b or '{}');return d if isinstance(d,dict) else {}
  except:return {}
 def auth(self):
  x=self.headers.get('Authorization','');return core.uid(x[7:] if x.startswith('Bearer ') else None)
 def do_GET(self): self.route('GET')
 def do_POST(self): self.route('POST')
 def do_PATCH(self): self.route('PATCH')
 def do_DELETE(self): self.route('DELETE')
 def route(self,method):
  p=urllib.parse.urlparse(self.path); q=urllib.parse.parse_qs(p.query); a=p.path.strip('/').split('/')
  try:
   if p.path=='/': return self.html()
   if p.path=='/api/health': return self.sendj(200,{'status':'ok','node_id':int(os.getenv('NODE_ID','0'))})
   if a[:3]==['api','auth','register'] and method=='POST':
    d=self.body(); un=d.get('username','');pw=d.get('password','')
    if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(pw)<8:return self.sendj(400,{'error':'invalid'})
    c=core.conn()
    try:c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(un,hashlib.sha256(pw.encode()).hexdigest(),d.get('display_name') or un));c.commit()
    except sqlite3.IntegrityError:return self.sendj(409,{'error':'duplicate'})
    u=c.execute('SELECT * FROM users WHERE username=?',(un,)).fetchone();c.close();return self.sendj(201,{'user':core.uobj(u),'token':core.token(u['id'])})
   if a[:3]==['api','auth','login'] and method=='POST':
    d=self.body();c=core.conn();u=c.execute('SELECT * FROM users WHERE username=? AND password=?',(d.get('username'),hashlib.sha256(d.get('password','').encode()).hexdigest())).fetchone();c.close()
    return self.sendj(200,{'user':core.uobj(u),'token':core.token(u['id'])}) if u else self.sendj(401,{'error':'invalid'})
   uid=self.auth()
   if not uid:return self.sendj(401,{'error':'auth'})
   c=core.conn()
   if a[:3]==['api','auth','me']: return self.sendj(200,{'user':core.uobj(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())})
   if a[:2]==['api','invitations'] and len(a)==4 and a[3]=='accept' and method=='POST':
    code=a[2]; inv=c.execute('SELECT * FROM invites WHERE code=?',(code,)).fetchone()
    if not inv or (inv['expires'] and inv['expires']<int(__import__('time').time())) or inv['uses']>=inv['max_uses']: return self.sendj(404,{})
    if inv['invited_user_id'] and inv['invited_user_id']!=uid:return self.sendj(403,{})
    if inv['invited_username'] and c.execute('SELECT username FROM users WHERE id=?',(uid,)).fetchone()[0]!=inv['invited_username']:return self.sendj(403,{})
    c.execute('INSERT OR IGNORE INTO wm VALUES(?,?,?)',(inv['workspace_id'],uid,'member'))
    c.execute('UPDATE invites SET uses=uses+1 WHERE code=? AND uses<max_uses',(code,));c.commit()
    return self.sendj(200,{'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(inv['workspace_id'],)).fetchone())})

   if a[:2]==['api','users'] and len(a)==3:
    if a[2]=='me' and method=='PATCH':
     d=self.body(); allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}; vals={k:v for k,v in d.items() if k in allowed}
     if 'timezone' in vals and not isinstance(vals['timezone'],str):return self.sendj(400,{'error':'timezone'})
     if vals:c.execute('UPDATE users SET '+','.join(k+'=?' for k in vals)+' WHERE id=?',(*vals.values(),uid));c.commit()
     return self.sendj(200,{'user':core.uobj(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())})
    try:u=int(a[2]);r=c.execute('SELECT * FROM users WHERE id=?',(u,)).fetchone()
    except:r=None
    return self.sendj(200,{'user':core.uobj(r)}) if r else self.sendj(404,{'error':'not found'})
   if a[:2]==['api','workspaces'] and len(a)==2 and method=='POST':
    d=self.body(); slug=d.get('slug','')
    if not re.fullmatch(r'[a-z0-9-]{2,32}',slug):return self.sendj(400,{'error':'slug'})
    try:
     cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(slug,d.get('name',''),uid));wid=cur.lastrowid;c.execute('INSERT INTO wm VALUES(?,?,?)',(wid,uid,'owner'));cur=c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));cid=cur.lastrowid;c.execute('INSERT INTO cm VALUES(?,?)',(cid,uid));c.commit()
    except sqlite3.IntegrityError:return self.sendj(409,{'error':'duplicate'})
    w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone();ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone();return self.sendj(201,{'workspace':dict(w),'general_channel':dict(ch)})
   if a[:2]==['api','workspaces'] and len(a)==2:
    rows=c.execute('SELECT w.* FROM workspaces w JOIN wm m ON m.workspace_id=w.id WHERE m.user_id=?',(uid,)).fetchall();return self.sendj(200,{'workspaces':[dict(x) for x in rows]})
   if a[:2]==['api','workspaces'] and len(a)>=3:
    slug=a[2];w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
    if not w:return self.sendj(404,{'error':'workspace'})
    wid=w['id'];r=core.role(c,wid,uid)
    if len(a)==3 and method=='GET':
     inc=q.get('include_archived',['0'])[0] in ('1','true'); rows=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(wid,inc)).fetchall();rows=[dict(x) for x in rows if not x['is_private'] or core.member(c,x['id'],uid)]
     return self.sendj(200,{'workspace':dict(w),'channels':rows,'read_state':[]})
    if len(a)==3 and method=='PATCH':
     d=self.body()
     if r not in ('owner','admin'):return self.sendj(403,{})
     if d.get('join_mode') not in (None,'open','invite_only'):return self.sendj(400,{})
     c.execute('UPDATE workspaces SET name=COALESCE(?,name),join_mode=COALESCE(?,join_mode) WHERE id=?',(d.get('name'),d.get('join_mode'),wid));c.commit();return self.sendj(200,{'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone())})
    if len(a)>=4 and a[3]=='invitations':
     if len(a)==4 and method=='POST':
      if r not in ('owner','admin'):return self.sendj(403,{})
      d=self.body()
      try: mx=int(d.get('max_uses',1))
      except: return self.sendj(400,{})
      if mx<1:return self.sendj(400,{})
      target=d.get('invited_user_id')
      if d.get('invited_username'):
       z=c.execute('SELECT id FROM users WHERE username=?',(d['invited_username'],)).fetchone()
       target=z[0] if z else None;mx=1
      exp=int(__import__('time').time())+int(d['expires_in_seconds']) if d.get('expires_in_seconds') is not None else None
      code=__import__('secrets').token_urlsafe(12)
      c.execute('INSERT INTO invites(code,workspace_id,invited_user_id,invited_username,max_uses,expires) VALUES(?,?,?,?,?,?)',(code,wid,target,d.get('invited_username'),mx,exp));c.commit()
      return self.sendj(201,{'invitation':dict(c.execute('SELECT * FROM invites WHERE code=?',(code,)).fetchone())})
     if len(a)==4 and method=='GET':
      if r not in ('owner','admin'):return self.sendj(403,{})
      import time as _time
      rows=c.execute('SELECT * FROM invites WHERE workspace_id=? AND uses<max_uses AND (expires IS NULL OR expires>?)',(wid,int(_time.time()))).fetchall()
      return self.sendj(200,{'invitations':[dict(x) for x in rows]})
     if len(a)==5 and method=='DELETE':
      if r not in ('owner','admin'):return self.sendj(403,{})
      z=c.execute('SELECT 1 FROM invites WHERE workspace_id=? AND code=?',(wid,a[4])).fetchone()
      if not z:return self.sendj(404,{})
      c.execute('DELETE FROM invites WHERE workspace_id=? AND code=?',(wid,a[4]));c.commit();return self.sendj(200,{'deleted':True})

    if len(a)>=4 and a[3]=='members':
     if len(a)==4 and method=='GET':
      rows=c.execute('SELECT u.id as user_id,m.role,u.username,u.display_name FROM wm m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(wid,)).fetchall()
      return self.sendj(200,{'members':[dict(x) for x in rows]}) if r else self.sendj(403,{})
     if len(a)==5 and method=='PATCH':
      if r not in ('owner','admin'): return self.sendj(403,{})
      try: target=int(a[4])
      except: return self.sendj(404,{})
      old=core.role(c,wid,target); nr=self.body().get('role')
      if nr not in ('owner','admin','member','guest'): return self.sendj(400,{})
      if nr=='owner' or target==w['owner_id'] or (r=='admin' and old in ('admin','owner')): return self.sendj(403,{})
      c.execute('UPDATE wm SET role=? WHERE workspace_id=? AND user_id=?',(nr,wid,target));c.commit()
      x=c.execute('SELECT u.id as user_id,m.role,u.username,u.display_name FROM wm m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? AND u.id=?',(wid,target)).fetchone()
      return self.sendj(200,{'member':dict(x)}) if x else self.sendj(404,{})
    if len(a)==4 and a[3]=='transfer_ownership' and method=='POST':
     if r!='owner': return self.sendj(403,{})
     target=self.body().get('new_owner_id'); tr=core.role(c,wid,target)
     if tr!='admin': return self.sendj(400,{})
     c.execute('UPDATE wm SET role=? WHERE workspace_id=? AND user_id=?',('admin',wid,uid));c.execute('UPDATE wm SET role=? WHERE workspace_id=? AND user_id=?',('owner',wid,target));c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,wid));c.commit()
     return self.sendj(200,{'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone())})
    if len(a)>=4 and a[3]=='channels' and method=='POST':
     d=self.body();name=d.get('name','')
     if not re.fullmatch(r'[a-z0-9-]{1,32}',name) or len(d.get('topic',''))>250:return self.sendj(400,{})
     if r=='guest' or (d.get('is_private') and r not in ('owner','admin')):return self.sendj(403,{})
     try:cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(wid,name,int(bool(d.get('is_private'))),d.get('topic','')));cid=cur.lastrowid;c.execute('INSERT INTO cm VALUES(?,?)',(cid,uid));c.commit()
     except sqlite3.IntegrityError:return self.sendj(409,{})
     return self.sendj(201,{'channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
   if a[:2]==['api','dms'] and len(a)==2 and method=='POST':
    d=self.body(); other=d.get('user_id')
    if not isinstance(other,int): return self.sendj(400,{})
    if not c.execute('SELECT 1 FROM users WHERE id=?',(other,)).fetchone(): return self.sendj(404,{})
    if other==uid:return self.sendj(400,{})
    row=c.execute('SELECT id FROM channels WHERE is_dm=1 AND id IN (SELECT channel_id FROM cm WHERE user_id=?) AND id IN (SELECT channel_id FROM cm WHERE user_id=?)',(uid,other)).fetchone()
    if row:return self.sendj(200,{'channel_id':row[0]})
    cur=c.execute("INSERT INTO channels(workspace_id,name,is_dm) VALUES(?,?,1)",(0,'dm-'+str(min(uid,other))+'-'+str(max(uid,other)))); cid=cur.lastrowid
    c.execute('INSERT INTO cm VALUES(?,?)',(cid,uid));c.execute('INSERT INTO cm VALUES(?,?)',(cid,other));c.commit()
    return self.sendj(200,{'channel_id':cid})
   if a[:2]==['api','channels']:
    try: cid=int(a[2])
    except: return self.sendj(404,{})
    ch=core.channel(c,cid)
    if not ch:return self.sendj(404,{})
    wid=ch['workspace_id'];r=core.role(c,wid,uid)
    if len(a)==3 and method=='PATCH':
     if not core.member(c,cid,uid) and r!='owner':return self.sendj(403,{})
     d=self.body()
     if len(d.get('topic',''))>250:return self.sendj(400,{})
     c.execute('UPDATE channels SET topic=? WHERE id=?',(d.get('topic',''),cid));c.commit();return self.sendj(200,{'channel':dict(core.channel(c,cid))})
    if len(a)==4 and a[3]=='join' and method=='POST':
     if ch['is_private'] and not core.member(c,cid,uid):return self.sendj(403,{})
     if not core.member(c,cid,uid) and core.role(c,wid,uid) is None and c.execute('SELECT join_mode FROM workspaces WHERE id=?',(wid,)).fetchone()[0]=='invite_only':return self.sendj(403,{})
     if not core.member(c,cid,uid):c.execute('INSERT OR IGNORE INTO cm VALUES(?,?)',(cid,uid));c.execute('INSERT OR IGNORE INTO wm VALUES(?,?,?)',(wid,uid,'member'));c.commit()
     return self.sendj(200,{'channel':dict(core.channel(c,cid))})
    if len(a)==4 and a[3]=='leave' and method=='POST':c.execute('DELETE FROM cm WHERE channel_id=? AND user_id=?',(cid,uid));c.commit();return self.sendj(200,{'channel':dict(ch)})
    if len(a)==4 and a[3]=='members':return self.sendj(200,{'members':[core.uobj(x) for x in c.execute('SELECT u.* FROM users u JOIN cm m ON m.user_id=u.id WHERE m.channel_id=?',(cid,))]})
    if len(a)==4 and a[3] in ('archive','unarchive') and method=='POST':
     if r!='owner':return self.sendj(403,{})
     c.execute('UPDATE channels SET is_archived=? WHERE id=?',(a[3]=='archive',cid));c.commit();return self.sendj(200,{'channel':dict(core.channel(c,cid))})
    if len(a)==4 and a[3]=='pins' and method=='GET':
     if not core.member(c,cid,uid): return self.sendj(403,{})
     lim=int(q.get('limit',['50'])[0]); boundary=core.dec(q['cursor'][0]) if 'cursor' in q else 9223372036854775807
     if lim<1 or lim>200:return self.sendj(400,{})
     rows=c.execute('SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? AND p.message_id<? ORDER BY p.message_id DESC LIMIT ?', (cid,boundary,lim+1)).fetchall()
     nc=core.cur(rows[lim-1]['message_id']) if len(rows)>lim else None
     return self.sendj(200,{'pins':[{'message':core.mobj(c,x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rows[:lim]],'next_cursor':nc})
    if len(a)==4 and a[3]=='messages':
     if not core.member(c,cid,uid):return self.sendj(403,{})
     if method=='POST':
      d=self.body();body=d.get('body','')
      if not isinstance(body,str) or not body.strip():return self.sendj(400,{})
      if ch['is_archived']:return self.sendj(423,{})
      par=d.get('parent_id');cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,uid,body,par,core.now()));mid=cur.lastrowid;m=core.mobj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone());core.emit(c,cid,'message.reply' if par else 'message.created',m);c.commit();return self.sendj(201,{'message':m})
     if method=='GET':
      lim=int(q.get('limit',['50'])[0]); boundary=core.dec(q['cursor'][0]) if 'cursor' in q else 9223372036854775807
      if lim<1 or lim>200:return self.sendj(400,{})
      rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL AND id<? ORDER BY id DESC LIMIT ?',(cid,boundary,lim+1)).fetchall();nextc=core.cur(rows[lim-1]['id']) if len(rows)>lim else None
      return self.sendj(200,{'messages':[core.mobj(c,x) for x in rows[:lim]],'next_cursor':nextc})
   if a[:2]==['api','messages'] and len(a)>=3:
    mid=int(a[2]);mr=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    if not mr:return self.sendj(404,{})
    if len(a)==4 and a[3]=='replies':
     lim=int(q.get('limit',['50'])[0]); boundary=core.dec(q['cursor'][0]) if 'cursor' in q else 0
     if lim<1 or lim>200:return self.sendj(400,{})
     if boundary: rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 AND id>? ORDER BY id LIMIT ?', (mid,boundary,lim+1)).fetchall()
     else: rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id LIMIT ?', (mid,lim+1)).fetchall()
     nc=core.cur(rows[lim-1]['id']) if len(rows)>lim else None
     return self.sendj(200,{'replies':[core.mobj(c,x) for x in rows[:lim]],'next_cursor':nc})
    if len(a)==4 and a[3]=='pin' and method=='POST':
     if not core.member(c,mr['channel_id'],uid): return self.sendj(403,{})
     z=core.now();c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(mid,uid,z));c.commit()
     x=c.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone();return self.sendj(200,{'pin':dict(x)})
    if len(a)==4 and a[3]=='pin' and method=='DELETE':
     c.execute('DELETE FROM pins WHERE message_id=?',(mid,));c.commit();return self.sendj(200,{'unpinned':True})
    if len(a)==4 and a[3]=='reactions' and method=='POST':
     if not core.member(c,mr['channel_id'],uid): return self.sendj(403,{})
     if core.channel(c,mr['channel_id'])['is_archived']: return self.sendj(423,{})
     e=self.body().get('emoji','')
     if not isinstance(e,str) or not e:return self.sendj(400,{})
     c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,uid,e));c.commit();return self.sendj(200,{'reactions':core.mobj(c,mr)['reactions']})
    if len(a)==5 and a[3]=='reactions' and method=='DELETE':
     if not core.member(c,mr['channel_id'],uid): return self.sendj(403,{})
     e=urllib.parse.unquote(a[4]); c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,uid,e));c.commit()
     return self.sendj(200,{'reactions':core.mobj(c,mr)['reactions']})
    if method=='PATCH':
     if mr['deleted']:return self.sendj(409,{})
     if core.channel(c,mr['channel_id'])['is_archived']:return self.sendj(423,{})
     if mr['author_id']!=uid:return self.sendj(403,{})
     d=self.body();c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(d.get('body',''),core.now(),mid));c.commit();return self.sendj(200,{'message':core.mobj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())})
    if method=='DELETE':
     if core.channel(c,mr['channel_id'])['is_archived']:return self.sendj(423,{})
     c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,));c.commit();return self.sendj(200,{'deleted':True})
   if a[:2]==['api','channels'] and len(a)==4 and a[3]=='read':
    try: cid=int(a[2])
    except: return self.sendj(404,{})
    ch=core.channel(c,cid)
    if not ch:return self.sendj(404,{})
    if not core.member(c,cid,uid):return self.sendj(403,{})
    row=c.execute('SELECT last_read_event_id FROM readstate WHERE user_id=? AND channel_id=?',(uid,cid)).fetchone(); last=row[0] if row else 0
    head=c.execute('SELECT last_event_id FROM seq WHERE channel_id=?',(cid,)).fetchone(); head=head[0] if head else 0
    state={'channel_id':cid,'last_read_event_id':last,'unread_count':max(0,head-last),'mention_count':0}
    if method=='POST':
     n=self.body().get('last_read_event_id',0)
     if isinstance(n,int) and n>last:c.execute('INSERT OR REPLACE INTO readstate VALUES(?,?,?)',(uid,cid,n));c.commit();state['last_read_event_id']=n;state['unread_count']=max(0,head-n)
    return self.sendj(200,{'read_state':state})
   if a[:2]==['api','search']:
    term=q.get('q',[''])[0];rows=c.execute('SELECT m.* FROM messages m JOIN cm x ON x.channel_id=m.channel_id WHERE x.user_id=? AND m.deleted=0 AND m.body LIKE ? ORDER BY m.id DESC',(uid,'%'+term+'%')).fetchall();return self.sendj(200,{'results':[core.mobj(c,x) for x in rows],'next_cursor':None})
   return self.sendj(404,{})
  except Exception as e:
   return self.sendj(400,{'error':str(e)})
 def html(self):
  b='''<!doctype html><html><head><title>Huddle</title><style>body{font-family:Arial;margin:0}button{padding:8px;background:#007a5a;color:#fff;border:0}.app{padding:30px}</style></head><body><div class="app"><h1>Huddle</h1><form data-testid="auth-form" id="auth-form"><input name="username" placeholder="username"><input name="password" type="password"><button data-testid="auth-submit" data-button-role="primary">Login</button></form><button data-testid="auth-toggle">Sign up</button><div data-testid="channel-list"></div><div data-testid="message-list"></div></div></body></html>''';b=b.encode();self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
if __name__=='__main__':
 core.init();ThreadingHTTPServer(('127.0.0.1',int(os.getenv('PORT','8000'))),H).serve_forever()
