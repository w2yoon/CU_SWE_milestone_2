import os,json,re,base64,hashlib,struct,socket,threading,time,urllib.parse
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from core import *
PORT=int(os.environ.get('PORT','8000')); NODE=int(os.environ.get('NODE_ID','0'))
def err(h,n,s):
 b=json.dumps({'error':s}).encode(); h.close_connection=True; h.send_response(n); h.send_header('Content-Type','application/json'); h.send_header('Content-Length',str(len(b))); h.end_headers(); h.wfile.write(b)
def js(h,n,o):
 b=json.dumps(o).encode(); h.close_connection=True; h.send_response(n); h.send_header('Content-Type','application/json'); h.send_header('Content-Length',str(len(b))); h.end_headers(); h.wfile.write(b)
def body(h):
 try:
  n=int(h.headers.get('Content-Length','0')); x=json.loads(h.rfile.read(n) or b'{}'); return x if isinstance(x,dict) else {}
 except: return None
def auth(h,c):
 z=h.headers.get('Authorization',''); return token_uid(c,z[7:] if z.startswith('Bearer ') else '')
def channel(c,i): return c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
def allowed(c,uid,ch):
 return c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(ch,uid)).fetchone() is not None
def wsframe(x):
 b=json.dumps(x).encode(); n=len(b)
 return b'\x81'+(bytes([n]) if n<126 else b'\x7e'+struct.pack('>H',n))+b
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.0'
 def log_message(self,*a): pass
 def do_GET(self): self.route('GET')
 def do_POST(self): self.route('POST')
 def do_PATCH(self): self.route('PATCH')
 def do_DELETE(self): self.route('DELETE')
 def route(self,method):
  p=urllib.parse.urlsplit(self.path); path=p.path; q=urllib.parse.parse_qs(p.query); c=conn()
  try:
   if path=='/': return self.html()
   if path=='/api/health': return js(self,200,{'status':'ok','node_id':NODE})
   if path=='/api/ws': return self.websocket(c,q)
   if path=='/api/auth/register' and method=='POST':
    x=body(self)
    if not x or not re.fullmatch(r'[A-Za-z0-9_]+',str(x.get('username',''))) or len(str(x.get('password','')))<8: return err(self,400,'invalid')
    try:
     c.execute('BEGIN IMMEDIATE'); c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(x['username'],x['password'],x.get('display_name') or x['username'])); uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); c.commit()
    except: c.rollback(); return err(self,409,'duplicate')
    return js(self,201,{'user':user(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()),'token':t})
   if path=='/api/auth/login' and method=='POST':
    x=body(self); r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(x.get('username'),x.get('password'))).fetchone() if x else None
    if not r:return err(self,401,'invalid credentials')
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id'])); c.commit(); return js(self,200,{'user':user(r),'token':t})
   uid=auth(self,c)
   if path=='/api/auth/me': return js(self,200,{'user':user(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())}) if uid else err(self,401,'auth')
   if not uid:return err(self,401,'auth')
   if path=='/api/users/me' and method=='PATCH':
    x=body(self); keys=[k for k in ('display_name','timezone','avatar_url','status_text','status_emoji') if k in x]
    if any(len(str(x[k]))>250 for k in keys):return err(self,400,'invalid')
    c.execute('UPDATE users SET '+','.join(k+'=?' for k in keys)+' WHERE id=?',tuple(x[k] for k in keys)+(uid,));c.commit();return js(self,200,{'user':user(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())})
   if path=='/api/workspaces' and method=='POST':
    x=body(self)
    if not x or not re.fullmatch(r'[a-z0-9-]{2,32}',str(x.get('slug',''))):return err(self,400,'invalid')
    try:
     c.execute('BEGIN IMMEDIATE');c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(x['slug'],x.get('name',''),uid)); wid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO members VALUES(?,?,?)',(wid,uid,'owner'));c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));ch=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO cmembers VALUES(?,?)',(ch,uid));c.commit()
    except: c.rollback();return err(self,409,'duplicate')
    w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone();return js(self,201,{'workspace':dict(w),'general_channel':dict(channel(c,ch))})
   if path=='/api/workspaces':
    a=[] 
    for w in c.execute('SELECT DISTINCT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(uid,)):a.append(dict(w))
    return js(self,200,{'workspaces':a})
   m=re.fullmatch(r'/api/workspaces/([^/]+)',path)
   if m:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
    if not w:return err(self,404,'unknown')
    if method=='GET':
     cs=[dict(x) for x in c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)) if not x['is_private'] or allowed(c,uid,x['id'])]
     return js(self,200,{'workspace':dict(w),'channels':cs,'read_state':[]})
   m=re.fullmatch(r'/api/workspaces/([^/]+)/members',path)
   if m and method=='GET':
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
    if not w:return err(self,404,'unknown')
    return js(self,200,{'members':[dict(x) for x in c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],))]})
   m=re.fullmatch(r'/api/workspaces/([^/]+)/channels',path)
   if m and method=='POST':
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
    if not w:return err(self,404,'unknown')
    role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],uid)).fetchone()
    x=body(self)
    if not role:return err(self,403,'member')
    if x.get('is_private') and role['role'] not in ('owner','admin'):return err(self,403,'private')
    if not re.fullmatch(r'[a-z0-9-]{1,32}',str(x.get('name',''))):return err(self,400,'invalid')
    try:c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],x['name'],int(bool(x.get('is_private'))),x.get('topic','')));i=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO cmembers VALUES(?,?)',(i,uid));c.execute('INSERT INTO seq VALUES(?,0)',(i,));c.commit()
    except:return err(self,409,'duplicate')
    return js(self,201,{'channel':dict(channel(c,i))})
   m=re.fullmatch(r'/api/channels/(\d+)',path)
   if m:
    ch=channel(c,int(m.group(1)))
    if not ch:return err(self,404,'unknown')
    if method=='PATCH':
     if not allowed(c,uid,ch['id']):return err(self,403,'member')
     x=body(self);c.execute('UPDATE channels SET topic=? WHERE id=?',(x.get('topic',''),ch['id']));c.commit();return js(self,200,{'channel':dict(channel(c,ch['id']))})
   m=re.fullmatch(r'/api/channels/(\d+)/(join|leave|archive|unarchive|members|messages)',path)
   if m:
    i=int(m.group(1)); action=m.group(2); ch=channel(c,i)
    if not ch:return err(self,404,'unknown')
    if action=='members' and method=='GET':return js(self,200,{'members':[user(c.execute('SELECT * FROM users WHERE id=?',(r['user_id'],)).fetchone()) for r in c.execute('SELECT user_id FROM cmembers WHERE channel_id=?',(i,))]})
    if action=='join' and method=='POST':
     if ch['is_private']:return err(self,403,'private')
     if not allowed(c,uid,i):
      c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],uid,'member'));c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(i,uid));c.commit()
     return js(self,200,{'channel':dict(ch)})
    if action=='leave' and method=='POST':c.execute('DELETE FROM cmembers WHERE channel_id=? AND user_id=?',(i,uid));c.commit();return js(self,200,{'channel':dict(ch)})
    if action in ('archive','unarchive') and method=='POST':
     w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
     if w['owner_id']!=uid:return err(self,403,'owner')
     c.execute('UPDATE channels SET is_archived=? WHERE id=?',(action=='archive',i));emit(c,i,'channel.'+action,{'channel':dict(channel(c,i))});c.commit();return js(self,200,{'channel':dict(channel(c,i))})
    if action=='messages' and method=='GET':
     a=[msgobj(c,r) for r in c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted_at IS NULL ORDER BY id DESC',(i,))];return js(self,200,{'messages':a,'next_cursor':None})
    if action=='messages' and method=='POST':
     if not allowed(c,uid,i):return err(self,403,'member')
     if ch['is_archived']:return err(self,423,'archived')
     x=body(self);b=str(x.get('body',''))
     if not b.strip():return err(self,400,'empty')
     c.execute('BEGIN IMMEDIATE');o=make_msg(c,uid,i,b,x.get('parent_id'));c.commit();return js(self,201,{'message':o})
   m=re.fullmatch(r'/api/messages/(\d+)',path)
   if m:
    r=c.execute('SELECT * FROM messages WHERE id=?',(int(m.group(1)),)).fetchone()
    if not r:return err(self,404,'unknown')
    ch=channel(c,r['channel_id']);w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
    if ch['is_archived']:return err(self,423,'archived')
    if method=='PATCH':
     if r['deleted_at']:return err(self,409,'deleted')
     if r['author_id']!=uid:return err(self,403,'author')
     x=body(self)
     if not x or not str(x.get('body','')).strip():return err(self,400,'empty')
     c.execute('BEGIN IMMEDIATE');t=now();c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(x['body'],t,r['id']));o=msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(r['id'],)).fetchone());emit(c,r['channel_id'],'message.updated',{'message':o});c.commit();return js(self,200,{'message':o})
    if method=='DELETE':
     if r['deleted_at']:return js(self,200,{'deleted':True})
     if r['author_id']!=uid and w['owner_id']!=uid:return err(self,403,'author')
     c.execute('BEGIN IMMEDIATE');c.execute('UPDATE messages SET deleted_at=? WHERE id=?',(now(),r['id']));emit(c,r['channel_id'],'message.deleted',{'message_id':r['id']});c.commit();return js(self,200,{'deleted':True})
   m=re.fullmatch(r'/api/messages/(\d+)/replies',path)
   if m and method=='GET':
    a=[msgobj(c,r) for r in c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted_at IS NULL ORDER BY id',(int(m.group(1)),))];return js(self,200,{'replies':a,'next_cursor':None})
   m=re.fullmatch(r'/api/messages/(\d+)/reactions/([^/]+)',path)
   if m and method=='DELETE':
    mid=int(m.group(1)); emoji=urllib.parse.unquote(m.group(2)); c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,uid,emoji)); c.commit(); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); return js(self,200,{'reactions':msgobj(c,r)['reactions']})
   m=re.fullmatch(r'/api/messages/(\d+)/reactions',path)
   if m and method=='POST':
    x=body(self);c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(int(m.group(1)),uid,x.get('emoji','😀')));c.commit();r=c.execute('SELECT * FROM messages WHERE id=?',(int(m.group(1)),)).fetchone();return js(self,200,{'reactions':msgobj(c,r)['reactions']})
   return err(self,404,'not found')
  except Exception as e:
   try:c.rollback()
   except:pass
   return err(self,500,str(e))
 def websocket(self,c,q):
  t=q.get('token',[''])[0];uid=token_uid(c,t)
  if not uid:return err(self,401,'auth')
  key=self.headers.get('Sec-WebSocket-Key',''); acc=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',acc);self.end_headers()
  self.connection.settimeout(.4); subs={}; buf=b''
  while True:
   try:
    d=self.connection.recv(4096)
    if d:
     n=d[1]&127; pos=2
     if n==126:n=struct.unpack('>H',d[2:4])[0];pos=4
     mask=d[pos:pos+4];raw=d[pos+4:pos+4+n]; x=json.loads(bytes(v^mask[i%4] for i,v in enumerate(raw)))
     i=int(x.get('channel_id')); head=c.execute('SELECT last_event_id FROM seq WHERE channel_id=?',(i,)).fetchone(); head=head[0] if head else 0
     if x.get('type')=='subscribe':subs[i]=head;self.connection.sendall(wsframe({'type':'subscribed','channel_id':i,'head_event_id':head}))
     elif x.get('type')=='resume':
      k=int(x.get('since_event_id',0))
      for r in c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(i,k)):
       z=json.loads(r['payload']);z.update(type=r['type'],event_id=r['event_id'],channel_id=i);self.connection.sendall(wsframe(z))
      subs[i]=head;self.connection.sendall(wsframe({'type':'resumed','channel_id':i,'head_event_id':head}))
   except socket.timeout:pass
   except:break
   for i,last in list(subs.items()):
    for r in c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(i,last)):
     z=json.loads(r['payload']);z.update(type=r['type'],event_id=r['event_id'],channel_id=i);self.connection.sendall(wsframe(z));subs[i]=r['event_id']
 def html(self):
  self.close_connection=True; self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(HTML.encode())
HTML='''<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;font:14px Arial}.app{display:flex;height:100vh}.side{width:240px;background:#3f0e40;color:#fff;padding:18px}.main{flex:1;padding:24px}.thread{width:320px;padding:18px;border-left:1px solid #ddd}.hidden{display:none!important}.modal{padding:20px}.msg{padding:12px;border-bottom:1px solid #ddd}.tools{display:none}.msg:hover .tools{display:inline}.primary,[data-button-role="primary"]{background:#007a5a;color:#fff}.danger,[data-button-role="danger"]{background:#e01e5a;color:#fff}.secondary,[data-button-role="secondary"]{background:#1264a3;color:#fff}button{border:0;padding:9px 14px;margin:3px;color:#fff}.channel{padding:8px;cursor:pointer}.picker{display:grid;grid-template-columns:repeat(6,1fr)}
</style></head><body>
<div id="auth-modal" class="modal"><form data-testid="auth-form" data-mode="login"><input name="username" placeholder="username"><input name="password" type="password" placeholder="password"><input name="display_name" placeholder="display name"><div data-testid="auth-error"></div><button type="submit" data-testid="auth-submit" data-button-role="primary">Login</button></form><button type="button" data-testid="auth-toggle" data-button-role="secondary">Register</button></div>
<div class="app"><aside class="side"><h2 data-testid="workspace-header">Huddle</h2><div data-testid="current-user"></div><div data-testid="channel-list"></div><div data-testid="dms-list"></div><button data-testid="new-channel-btn" data-button-role="secondary">New channel</button><button data-testid="workspace-settings-btn" data-button-role="secondary">Workspace</button><button data-testid="logout-btn" data-button-role="danger">Logout</button><div data-testid="empty-state-create-workspace">Create workspace</div><form data-testid="workspace-create-form"><input name="name"><input name="slug"><button type="submit" data-testid="workspace-general-submit" data-button-role="primary">Create</button></form><form data-testid="join-workspace-form"><input name="slug"><button data-testid="join-workspace-submit" data-button-role="primary">Join</button><div data-testid="join-workspace-error"></div></form></aside>
<main class="main"><h2 data-testid="channel-title"># general</h2><div data-testid="channel-topic"></div><div data-testid="message-list"></div><div data-testid="empty-channel">Nothing here yet — start the conversation.</div><input data-testid="message-input"><button data-testid="send-btn" data-button-role="primary">Send</button><button data-testid="channel-settings-btn" data-button-role="secondary">Settings</button></main>
<section data-testid="thread-panel" class="thread hidden"><button data-testid="close-thread" data-button-role="secondary">Close</button><div data-testid="thread-list"></div><input data-testid="thread-input"><button data-testid="thread-send" data-button-role="primary">Reply</button></section></div>
<div id="create-channel-modal" class="hidden"><form data-testid="create-channel-form"><input name="name"><input data-testid="create-channel-private" type="checkbox"><div data-testid="create-channel-error"></div><button type="submit" data-testid="create-channel-submit" data-button-role="primary">Create</button><button type="button" data-testid="create-channel-cancel" data-button-role="secondary">Cancel</button></form></div>
<div id="channel-settings-modal" class="hidden"><h3 data-testid="channel-settings-title">Channel</h3><button data-testid="channel-settings-close" data-button-role="secondary">Close</button><input data-testid="channel-topic-input"><button data-testid="channel-topic-submit" data-button-role="primary">Save</button><div data-testid="channel-members-list"></div><form data-testid="channel-add-member-form"><input data-testid="channel-add-member-input"><button data-testid="channel-add-member-submit" data-button-role="primary">Add</button></form><button data-testid="archive-channel-btn" data-button-role="danger">Archive</button><button data-testid="unarchive-channel-btn" data-button-role="secondary">Unarchive</button></div>
<div id="workspace-settings-modal" class="hidden"><input data-testid="workspace-name-input"><select data-testid="workspace-join-mode"></select><button data-testid="workspace-general-submit" data-button-role="primary">Save</button><button data-testid="workspace-settings-close" data-button-role="secondary">Close</button><button data-testid="settings-tab-members">Members</button><button data-testid="settings-tab-invitations">Invitations</button><button data-testid="settings-tab-general">General</button><div data-testid="settings-pane-members"></div><div data-testid="settings-pane-invitations"></div><div data-testid="settings-pane-general"></div><button data-testid="create-invitation-btn" data-button-role="secondary">Invite</button><form id="create-invitation-form" data-testid="create-invitation-form"><button data-testid="create-invitation-submit" data-button-role="primary">Create</button></form><div data-testid="invitations-list"></div></div>
<script>
const q=s=>document.querySelector(s),all=s=>document.querySelectorAll(s),T=()=>localStorage.getItem('huddle.token');let W,C;
const api=(p,o={})=>fetch('/api'+p,{...o,headers:{'Content-Type':'application/json',Authorization:'Bearer '+(T()||''),...(o.headers||{})}}).then(async r=>[r.status,await r.json()]);
const fd=f=>Object.fromEntries(new FormData(f));
function row(m){return '<div data-testid="message" class="msg" data-id="'+m.id+'"><span data-testid="message-avatar">'+(m.author?.display_name||'A')[0]+'</span> <b data-testid="message-author">'+(m.author?.display_name||'')+'</b> <small data-testid="message-time">'+new Date(m.created_at).toLocaleString()+'</small><div data-testid="message-body">'+m.body+'</div><span data-testid="reply-count">'+(m.reply_count||0)+' replies</span><span class="tools"><button data-testid="edit-message-btn" onclick="editMsg('+m.id+',this)">Edit</button><button data-testid="delete-message-btn" onclick="delMsg('+m.id+')">Delete</button><button data-testid="open-thread-btn" onclick="thread('+m.id+')">Thread</button><button data-testid="reaction-button" onclick="react('+m.id+',this)">😀</button></span>'+(m.edited_at?'<i data-testid="edited-indicator">(edited)</i>':'')+(m.reactions||[]).map(r=>'<span data-testid="reaction-chip" data-emoji="'+r.emoji+'" onclick="removeReact('+m.id+',this)">'+r.emoji+' '+r.count+'</span>').join('')+'</div>'}
function editMsg(id,b){let r=b.closest('[data-testid="message"]'),e=document.createElement('input');e.setAttribute('data-testid','edit-input');e.value=r.querySelector('[data-testid="message-body"]').textContent;let done=false,save=()=>{if(done||!e.isConnected||!e.value.trim())return;done=true;let v=e.value,d=document.createElement('div');d.setAttribute('data-testid','message-body');d.textContent=v;e.replaceWith(d);let i=document.createElement('i');i.setAttribute('data-testid','edited-indicator');i.textContent='(edited)';r.append(i);let xhr=new XMLHttpRequest();xhr.open('PATCH','/api/messages/'+id,false);xhr.setRequestHeader('Content-Type','application/json');xhr.setRequestHeader('Authorization','Bearer '+T());try{xhr.send(JSON.stringify({body:v}))}catch(_){ }};r.querySelector('[data-testid="message-body"]').replaceWith(e);e.onkeydown=x=>{if(x.key==='Enter'){x.preventDefault();save()}};e.focus();e.select()}

function delMsg(id){api('/messages/'+id,{method:'DELETE'}).then(()=>open(C))}
function react(id,b){let p=document.createElement('div');p.dataset.testid='emoji-picker';p.style.display='grid';['😀','👍','❤️','🎉','🚀','🔥'].forEach(e=>{let x=document.createElement('button');x.dataset.testid='emoji-option';x.textContent=e;x.onclick=()=>api('/messages/'+id+'/reactions',{method:'POST',body:JSON.stringify({emoji:e})}).then(()=>{p.remove();open(C)});p.append(x)});b.after(p)}
function removeReact(id,b){let e=b.dataset.emoji;b.remove();api('/messages/'+id+'/reactions/'+encodeURIComponent(e),{method:'DELETE'});}
function thread(id){let p=q('[data-testid="thread-panel"]');p.classList.remove('hidden');p.dataset.parent=id;api('/messages/'+id+'/replies').then(x=>{q('[data-testid="thread-list"]').innerHTML=(x[1].replies||[]).map(row).join('')})}
q('[data-testid="close-thread"]').onclick=()=>q('[data-testid="thread-panel"]').classList.add('hidden');
q('[data-testid="thread-send"]').onclick=()=>{let e=q('[data-testid="thread-input"]'),id=q('[data-testid="thread-panel"]').dataset.parent;if(e.value.trim())api('/channels/'+C+'/messages',{method:'POST',body:JSON.stringify({body:e.value,parent_id:+id})}).then(()=>{e.value='';thread(+id);open(C)})};
function open(id){C=id;let e=q('[data-channel-id="'+id+'"]');q('[data-testid="channel-title"]').textContent='# '+(e?.dataset.channelName||'channel');api('/channels/'+id+'/messages').then(x=>{let a=x[1].messages||[];q('[data-testid="empty-channel"]').classList.toggle('hidden',a.length>0);q('[data-testid="message-list"]').innerHTML=a.map(row).join('')})}
function load(){return api('/auth/me').then(x=>{if(x[0]!=200)return;q('[data-testid="current-user"]').textContent=x[1].user.display_name||x[1].user.username;q('#auth-modal').classList.add('hidden');return api('/workspaces')}).then(x=>{if(!x)return;W=x[1].workspaces[0];if(!W)return;return api('/workspaces/'+W.slug)}).then(x=>{if(!x||!x[1]||!x[1].workspace)return;W=x[1].workspace;q('[data-testid="workspace-header"]').textContent=W.name;q('[data-testid="channel-list"]').innerHTML=x[1].channels.map(c=>'<div class="channel" data-testid="channel-entry" data-channel-id="'+c.id+'" data-channel-name="'+c.name+'"># '+c.name+'</div>').join('');all('[data-testid="channel-entry"]').forEach(e=>e.onclick=()=>open(e.dataset.channelId));let g=x[1].channels[0];if(g)open(g.id)})}
q('[data-testid="auth-toggle"]').onclick=()=>{let f=q('[data-testid="auth-form"]');f.dataset.mode=f.dataset.mode==='login'?'register':'login';q('[data-testid="auth-submit"]').textContent=f.dataset.mode==='login'?'Login':'Sign up'};
q('[data-testid="auth-form"]').onsubmit=e=>{e.preventDefault();let f=e.target;api(f.dataset.mode==='login'?'/auth/login':'/auth/register',{method:'POST',body:JSON.stringify(fd(f))}).then(x=>x[0]>=200&&x[0]<300?(localStorage.setItem('huddle.token',x[1].token),load().then(()=>{q('#auth-modal').classList.add('hidden');return new Promise(r=>setTimeout(r,50))})):q('[data-testid="auth-error"]').textContent=x[1].error||'Invalid')};
q('[data-testid="workspace-create-form"]').onsubmit=e=>{e.preventDefault();api('/workspaces',{method:'POST',body:JSON.stringify(fd(e.target))}).then(load)};
q('[data-testid="new-channel-btn"]').onclick=()=>q('#create-channel-modal').classList.remove('hidden');q('[data-testid="create-channel-cancel"]').onclick=()=>q('#create-channel-modal').classList.add('hidden');
q('[data-testid="create-channel-form"]').onsubmit=e=>{e.preventDefault();api('/workspaces/'+W.slug+'/channels',{method:'POST',body:JSON.stringify(fd(e.target))}).then(x=>{if(x[0]==201){q('#create-channel-modal').classList.add('hidden');let id=x[1].channel.id;load().then(()=>setTimeout(()=>open(id),100))}else q('[data-testid="create-channel-error"]').textContent='Duplicate channel'})};
q('[data-testid="send-btn"]').onclick=()=>{let e=q('[data-testid="message-input"]');if(e.value.trim())api('/channels/'+C+'/messages',{method:'POST',body:JSON.stringify({body:e.value})}).then(()=>{e.value='';open(C)})};q('[data-testid="logout-btn"]').onclick=()=>{localStorage.removeItem('huddle.token');location.reload()};let qt=new URLSearchParams(location.search).get('token');if(qt)localStorage.setItem('huddle.token',qt);if(T())load();
</script></body></html>
'''
ThreadingHTTPServer.allow_reuse_address=True
ThreadingHTTPServer.daemon_threads=True
ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
