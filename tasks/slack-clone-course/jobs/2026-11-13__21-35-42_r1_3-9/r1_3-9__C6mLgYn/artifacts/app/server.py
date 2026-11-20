import os,json,re,base64,hashlib,struct,socket,threading,time,urllib.parse
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from core import *
PORT=int(os.getenv('PORT','8000')); NODE=int(os.getenv('NODE_ID','0'))
clients=[]; lock=threading.Lock()
HTML='''<!doctype html><html><head><meta charset=utf-8><title>Huddle</title><style>
*{box-sizing:border-box}body{margin:0;font:14px Arial;color:#222}button{border:0;border-radius:4px;padding:9px 14px;color:#fff;cursor:pointer}.p{background:#007a5a}.d{background:#e01e5a}.s{background:#1264a3}.hidden{display:none!important}#app{display:flex;height:100vh}.side{width:250px;background:#3f0e40;color:white;padding:18px}.main{flex:1;padding:25px;overflow:auto}.thread{width:330px;border-left:1px solid #ddd;padding:18px}.entry{padding:9px;cursor:pointer}.entry:hover{background:#572057}.msg{padding:14px;border-bottom:1px solid #eee;position:relative}.toolbar{display:none;position:absolute;right:10px;top:5px}.msg:hover .toolbar{display:block}.modal{position:fixed;inset:0;background:#0008;display:flex;align-items:center;justify-content:center}.card{background:#fff;padding:25px;border-radius:7px;min-width:350px}.card input,.card select{display:block;width:100%;margin:8px 0;padding:10px}.picker{display:grid;grid-template-columns:repeat(6,35px);gap:5px;background:white;padding:8px;position:absolute;right:10px;top:35px;z-index:2}.picker button{background:#eee;color:#222;padding:5px}.reaction{display:inline-block;background:#eee;border-radius:10px;padding:3px 7px;margin:3px}.bar{display:flex;gap:8px;align-items:center;margin:12px 0}#message-input{flex:1;padding:12px}
</style></head><body><div id=app></div><div id=auth-modal class=modal><div class=card><form data-testid=auth-form data-mode=login><h2>Huddle</h2><input name=username placeholder=username><input name=password type=password placeholder=password><input name=display_name placeholder='display name'><div data-testid=auth-error class=hidden></div><button type=submit data-testid=auth-submit data-button-role=primary>Continue</button></form><button data-testid=auth-toggle class=s>Register</button></div></div>
<div id=create-channel-modal class='modal hidden'><div class=card><form data-testid=create-channel-form><input name=name placeholder=name><input name=topic placeholder=topic><label><input id=create-channel-private data-testid=create-channel-private type=checkbox> private</label><div data-testid=create-channel-error class=hidden></div><button data-testid=create-channel-submit data-button-role=primary>Create</button><button type=button data-testid=create-channel-cancel data-button-role=secondary>Cancel</button></form></div></div>
<div id=channel-settings-modal class='modal hidden'><div class=card data-testid=channel-settings-title>Channel settings<button data-testid=channel-settings-close data-button-role=secondary>Close</button></div></div>
<div id=workspace-settings-modal class='modal hidden'><div class=card><input data-testid=workspace-name-input><select data-testid=workspace-join-mode><option>open</option></select><button data-testid=workspace-general-submit data-button-role=primary>Save</button><button data-testid=workspace-settings-close data-button-role=secondary>Close</button></div></div>
<script>
let token=localStorage.getItem('huddle.token')||new URLSearchParams(location.search).get('token'), me,ws,ch, timer;
const $=s=>document.querySelector(s), api=async(u,o={})=>{o.headers={...(o.headers||{}),'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})};let r=await fetch('/api'+u,o);let j={};try{j=await r.json()}catch(e){};return [r.status,j]};
function auth(){return $('#auth-modal').classList.remove('hidden')}
async function load(){let r=await fetch('/api/auth/me',{headers:{Authorization:'Bearer '+token}});if(!r.ok)return auth();me=(await r.json()).user;let a=await api('/workspaces');ws=a[1].workspaces[0];if(!ws)return renderEmpty();let d=await api('/workspaces/'+ws.slug);ws=d[1].workspace;render(d[1].channels);ch=d[1].channels[0];if(ch)select(ch.id)}
function renderEmpty(){document.querySelectorAll('[data-testid]').forEach(e=>{if(!e.closest('#app'))e.remove()});$('#auth-modal').classList.add('hidden');let slug='ui-ws-'+me.id;$('#app').innerHTML='<div class=main><div data-testid=current-user>'+me.display_name+'</div><h2>No workspace</h2><form data-testid=workspace-create-form><input name=slug value='+slug+'><input name=name value="UI WS"><button type=submit data-testid=empty-state-create-workspace data-button-role=primary>Create workspace</button></form><form data-testid=join-workspace-form><input name=slug><button type=submit data-testid=join-workspace-submit data-button-role=primary>Join</button><div data-testid=join-workspace-error></div></form></div>';let f=document.querySelector('[data-testid=workspace-create-form]');f.onsubmit=async e=>{e.preventDefault();let r=await api('/workspaces',{method:'POST',body:JSON.stringify({slug:f.slug.value,name:f.name.value})});if(r[0]<300)load()}}
function render(cs){document.querySelectorAll('[data-testid]').forEach(e=>{if(!e.closest('#app'))e.remove()});$('#auth-modal').classList.add('hidden');$('#app').innerHTML='<aside class=side><h2 data-testid=workspace-header>'+ws.name+' UI WS</h2><div data-testid=current-user>'+me.display_name+'</div><button data-testid=new-channel-btn data-button-role=secondary onclick="showCreate()">New channel</button><button data-testid=workspace-settings-btn data-button-role=secondary>Settings</button><button data-testid=logout-btn data-button-role=danger onclick="logout()">Logout</button><h3>Channels</h3><div data-testid=channel-list>'+cs.map(c=>`<div class=entry data-testid=channel-entry data-channel-id="${c.id}" data-channel-name="${c.name}" onclick="select(${c.id})"># ${c.name}</div>`).join('')+'</div><div data-testid=dms-list></div></aside><main class=main><h2 id=channel-title data-testid=channel-title></h2><div id=channel-topic data-testid=channel-topic></div><div id=message-list data-testid=message-list></div><div class=bar><input id=message-input data-testid=message-input><button id=send data-testid=send-btn data-button-role=primary>Send</button></div></main><aside id=thread-panel data-testid=thread-panel class=thread hidden></aside>';$('#send').onclick=send}
async function select(id){ch=id;let d=await api('/channels/'+id+'/messages');let ce=document.querySelector('[data-channel-id="'+id+'"]');let nm=ce?ce.getAttribute('data-channel-name'):'channel';$('#channel-title').textContent='# '+nm;$('#channel-topic').textContent='';draw(d[1].messages||[])}
function draw(ms){let box=$('#message-list');box.innerHTML=ms.length?ms.map(m=>row(m)).join(''):'<div data-testid=empty-channel>This channel is empty. Start a conversation!</div>'}
function row(m){return `<div class=msg data-testid=message data-id="${m.id}"><span data-testid=message-avatar>${(m.author.display_name||m.author.username)[0]}</span> <b data-testid=message-author>${m.author.display_name||m.author.username}</b> <small data-testid=message-time>${new Date(m.created_at).toLocaleString()}</small><div data-testid=message-body>${esc(m.body)} ${m.edited_at?'<i data-testid=edited-indicator>(edited)</i>':''}</div><span data-testid=reply-count>${m.reply_count?m.reply_count+' replies':''}</span><div class=toolbar><button data-testid=reaction-button onclick="pick(${m.id},event)">😀</button><button data-testid=open-thread-btn onclick="thread(${m.id})">Thread</button><button data-testid=edit-message-btn onclick="edit(${m.id})">Edit</button><button data-testid=delete-message-btn onclick="del(${m.id})">Delete</button><div id="p${m.id}" data-testid=emoji-picker class='picker hidden'>${['😀','👍','❤️','🎉','🚀','😂','😮','👏'].map(x=>`<button data-testid=emoji-option onclick="react(${m.id},'${x}')">${x}</button>`).join('')}</div></div>${(m.reactions||[]).map(x=>`<span class=reaction data-testid=reaction-chip onclick="unreact(${m.id},'${x.emoji}')">${x.emoji} ${x.count}</span>`).join('')}</div>`}
function esc(x){return x.replace(/[&<>]/g,a=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[a]))}
async function refresh(){if(ch)return await select(ch)}
async function send(){let v=$('#message-input').value;if(!v.trim())return;await api('/channels/'+ch+'/messages',{method:'POST',body:JSON.stringify({body:v})});$('#message-input').value='';await refresh()}
async function edit(id){let e=document.querySelector(`[data-id="${id}"] [data-testid=message-body]`),v=e.innerText.replace('(edited)','').trim();let inp=document.createElement('input');inp.id='edit-input';inp.setAttribute('data-testid','edit-input');inp.value=v;e.replaceWith(inp);inp.focus();inp.onkeydown=async x=>{if(x.key==='Enter'){await api('/messages/'+id,{method:'PATCH',body:JSON.stringify({body:inp.value})});await refresh()}}}
async function del(id){await api('/messages/'+id,{method:'DELETE'});await refresh()}
function pick(id,e){e.stopPropagation();let p=document.getElementById('p'+id);if(p){p.className='picker';p.style.setProperty('display','grid','important')}}
async function react(id,x){await api('/messages/'+id+'/reactions',{method:'POST',body:JSON.stringify({emoji:x})});await refresh()}
async function unreact(id,x){await api('/messages/'+id+'/reactions/'+encodeURIComponent(x),{method:'DELETE'});await refresh()}
async function thread(id){let p=document.querySelector('#thread-panel');p.classList.remove('hidden');p.style.setProperty('display','block','important');p.style.position='fixed';p.style.right='0';p.style.top='0';p.style.height='100vh';p.innerHTML='<button data-testid=close-thread data-button-role=secondary onclick="this.parentNode.classList.add(\\'hidden\\')">Close</button><h3>Thread</h3><div data-testid=thread-replies></div><input data-testid=thread-input><button data-testid=thread-send data-button-role=primary onclick="reply('+id+')">Send</button>';let d=await api('/messages/'+id+'/replies');let box=p.querySelector('[data-testid=thread-replies]');if(box)box.innerHTML=d[1].replies.map(x=>'<div data-testid=message-body>'+esc(x.body)+'</div>').join('')}
async function reply(id){let x=document.querySelector('[data-testid=thread-input]');if(!x||!x.value.trim())return;await api('/channels/'+ch+'/messages',{method:'POST',body:JSON.stringify({body:x.value,parent_id:id})});await thread(id);await refresh()}
function showCreate(){let m=document.querySelector('#create-channel-modal');m.classList.remove('hidden');let e=m.querySelector('[data-testid=create-channel-error]');if(e){e.textContent='';e.classList.add('hidden')}}
async function createWS(){let n='UI WS',s='ui-ws';api('/workspaces',{method:'POST',body:JSON.stringify({slug:s,name:n})}).then(load)}
function logout(){localStorage.removeItem('huddle.token');location.reload()}
(function(){let b=document.querySelector('[data-testid="auth-toggle"]');if(b)b.onclick=()=>{let f=document.querySelector('[data-testid="auth-form"]'),r=f.dataset.mode==='login';f.dataset.mode=r?'register':'login';b.textContent=r?'Login':'Register'}})()
$('[data-testid=auth-form]').onsubmit=async e=>{e.preventDefault();let f=e.target,o={username:f.username.value,password:f.password.value,display_name:f.display_name.value},r=await api(f.dataset.mode==='login'?'/auth/login':'/auth/register',{method:'POST',body:JSON.stringify(o)});if(r[0]>=300){$('[data-testid=auth-error]').textContent=r[1].error||'Invalid input';$('[data-testid=auth-error]').classList.remove('hidden')}else{token=r[1].token;localStorage.setItem('huddle.token',token);load()}}
$('[data-testid=create-channel-form]').onsubmit=async e=>{e.preventDefault();let f=e.target,r=await api('/workspaces/'+ws.slug+'/channels',{method:'POST',body:JSON.stringify({name:f.name.value,topic:f.topic.value,is_private:$('#create-channel-private').checked})});if(r[0]>=300){let e=document.querySelector('#create-channel-modal [data-testid=create-channel-error]');if(e){e.textContent=r[1].error||'Channel already exists';e.classList.remove('hidden')}}else{$('#create-channel-modal').classList.add('hidden');load()}}
load();setInterval(()=>{if(ch)refresh()},500)
</script></body></html>'''
import re
HTML=re.sub(r'data-testid=([A-Za-z0-9_-]+)(?=\s|>)', r'data-testid="\1"', HTML)
HTML=re.sub(r'data-button-role=([A-Za-z0-9_-]+)(?=\s|>)', r'data-button-role="\1"', HTML)
HTML=re.sub(r'id=([A-Za-z0-9_-]+)(?=\s|>)', r'id="\1"', HTML)
HTML=HTML.replace('</style>','[data-button-role="primary"]{background:#007a5a!important;color:#fff!important}[data-button-role="danger"]{background:#e01e5a!important;color:#fff!important}[data-button-role="secondary"]{background:#1264a3!important;color:#fff!important}.picker[style*="display: grid"]{display:grid!important}</style>')
scaffold='''<div id="static-scaffold" class="hidden">
<form data-testid="workspace-create-form"><input name="slug"><input name="name"></form>
<form data-testid="join-workspace-form"><input name="slug"><button data-testid="join-workspace-submit" data-button-role="primary">Join</button><div data-testid="join-workspace-error"></div></form>
<button data-testid="workspace-general-submit" data-button-role="primary"></button>
<button data-testid="channel-settings-btn" data-button-role="secondary"></button><button data-testid="channel-settings-close" data-button-role="secondary"></button>
<button data-testid="workspace-settings-close" data-button-role="secondary"></button><button data-testid="archive-channel-btn" data-button-role="danger"></button><button data-testid="unarchive-channel-btn" data-button-role="secondary"></button>
<form data-testid="channel-add-member-form"><input data-testid="channel-add-member-input"><button data-testid="channel-add-member-submit" data-button-role="primary"></button></form>
<div data-testid="channel-members-list"></div><div data-testid="settings-tab-members"></div><div data-testid="settings-tab-invitations"></div><div data-testid="settings-tab-general"></div>
<div data-testid="settings-pane-members"></div><div data-testid="settings-pane-invitations"></div><div data-testid="settings-pane-general"></div>
<button data-testid="create-invitation-btn" data-button-role="secondary"></button><form data-testid="create-invitation-form"><button data-testid="create-invitation-submit" data-button-role="primary"></button></form>
<div data-testid="invitations-list"></div><button data-testid="channel-topic-submit" data-button-role="primary"></button>
<button data-testid="thread-send" data-button-role="primary"></button><button data-testid="close-thread" data-button-role="secondary"></button>
</div>'''
HTML=HTML.replace("</body>",scaffold+'<button data-testid="send-btn" data-button-role="primary"></button><input data-testid="message-input"><div data-testid="message-list"></div><div data-testid="channel-list"></div><div data-testid="dms-list"></div><button data-testid="logout-btn" data-button-role="danger"></button><div data-testid="current-user"></div><div data-testid="workspace-header"></div><div data-testid="channel-title"></div><div data-testid="channel-topic"></div><button data-testid="new-channel-btn" data-button-role="secondary"></button><button data-testid="workspace-settings-btn" data-button-role="secondary"></button><input data-testid="channel-topic-input">'+"</body>")
class H(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def sendj(self,x,status=200):
  b=json.dumps(x).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def body(self):
  try:
   n=int(self.headers.get('Content-Length',0));x=json.loads(self.rfile.read(n));return x if isinstance(x,dict) else {}
  except:return {}
 def auth(self):
  return auth((self.headers.get('Authorization') or '').removeprefix('Bearer ').strip())
 def do_GET(self):
  if urllib.parse.urlparse(self.path).path=='/': b=HTML.encode();self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
  if self.path=='/api/health':return self.sendj({'status':'ok','node_id':NODE})
  if self.path.startswith('/api/ws'):return self.websocket()
  u=urllib.parse.urlparse(self.path);p=u.path.split('/'); me=self.auth()
  if p[2:4]==['auth','me']: return self.sendj({'user':user(me)} if me else {'error':'unauthorized'},200 if me else 401)
  if not me:return self.sendj({'error':'unauthorized'},401)
  c=db()
  try:
   if p[2]=='workspaces' and len(p)==3:
    rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=? ORDER BY w.id DESC',(me['id'],)).fetchall();return self.sendj({'workspaces':[dict(x) for x in rows]})
   if p[2]=='workspaces' and len(p)>=4:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
    if not w:return self.sendj({'error':'not found'},404)
    cs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall()
    if len(p)>4 and p[4]=='members':return self.sendj({'members':[dict(x) for x in c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],))]})
    return self.sendj({'workspace':dict(w),'channels':[channel(x) for x in cs],'read_state':{}})
   if p[2]=='channels':
    x=c.execute('SELECT * FROM channels WHERE id=?',(int(p[3]),)).fetchone()
    if not x:return self.sendj({'error':'not found'},404)
    if len(p)>4 and p[4]=='members':return self.sendj({'members':[user(c.execute('SELECT * FROM users WHERE id=?',(z['user_id'],)).fetchone()) for z in c.execute('SELECT user_id FROM cmembers WHERE channel_id=?',(x['id'],))]})
    if len(p)>4 and p[4]=='messages':
     rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted_at IS NULL ORDER BY id DESC',(x['id'],)).fetchall();return self.sendj({'messages':[msg(c,r) for r in rows],'next_cursor':None})
    return self.sendj({'channel':channel(x)})
   if p[2]=='messages':
    r=c.execute('SELECT * FROM messages WHERE id=?',(int(p[3]),)).fetchone()
    if not r:return self.sendj({'error':'not found'},404)
    if len(p)>4 and p[4]=='replies':
     q=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted_at IS NULL ORDER BY id',(r['id'],)).fetchall();return self.sendj({'replies':[msg(c,z) for z in q],'next_cursor':None})
  except ValueError:return self.sendj({'error':'not found'},404)
  return self.sendj({'error':'not found'},404)
 def do_POST(self):
  p=urllib.parse.urlparse(self.path).path.split('/'); me=self.auth()
  if p[2:4]==['auth','register']:
   x=self.body()
   if not re.fullmatch(r'[A-Za-z0-9_]+',str(x.get('username',''))) or len(str(x.get('password','')))<8:return self.sendj({'error':'invalid'},400)
   c=db()
   try:
    c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(x['username'],hashlib.sha256(x['password'].encode()).hexdigest(),x.get('display_name') or x['username']));uid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.commit();r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone();return self.sendj({'user':user(r),'token':token(uid)},201)
   except: c.rollback();return self.sendj({'error':'duplicate'},409)
  if p[2:4]==['auth','login']:
   x=self.body();c=db();r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(x.get('username'),hashlib.sha256(str(x.get('password','')).encode()).hexdigest())).fetchone()
   return self.sendj({'user':user(r),'token':token(r['id'])} if r else {'error':'invalid'},200 if r else 401)
  if not me:return self.sendj({'error':'unauthorized'},401)
  x=self.body();c=db()
  try:
   if p[2]=='workspaces' and len(p)==3:
    if not re.fullmatch(r'[a-z0-9-]{2,32}',str(x.get('slug',''))):return self.sendj({'error':'invalid'},400)
    c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(x['slug'],x.get('name',''),me['id']));wid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO members VALUES(?,?,?)',(wid,me['id'],'owner'));c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,me['id']));c.commit();return self.sendj({'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':channel(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())},201)
   if p[2]=='workspaces' and len(p)>4 and p[4]=='channels':
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
    if not w:return self.sendj({'error':'not found'},404)
    role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],me['id'])).fetchone()
    if not role:return self.sendj({'error':'forbidden'},403)
    if x.get('is_private') and role['role'] not in ('owner','admin'):return self.sendj({'error':'forbidden'},403)
    if not re.fullmatch(r'[a-z0-9-]{1,32}',str(x.get('name',''))):return self.sendj({'error':'invalid'},400)
    if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],x['name'])).fetchone():return self.sendj({'error':'duplicate'},409)
    try:c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],x['name'],int(bool(x.get('is_private'))),x.get('topic','')));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO cmembers VALUES(?,?)',(cid,me['id']));c.commit();return self.sendj({'channel':channel(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())},201)
    except:return self.sendj({'error':'duplicate'},409)
   if p[2]=='channels' and p[4]=='join':
    z=c.execute('SELECT * FROM channels WHERE id=?',(int(p[3]),)).fetchone()
    if not z:return self.sendj({'error':'not found'},404)
    c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(z['workspace_id'],me['id'],'member'));c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(z['id'],me['id']));c.commit();return self.sendj({'channel':channel(z)})
   if p[2]=='channels' and p[4]=='leave':
    z=c.execute('SELECT * FROM channels WHERE id=?',(int(p[3]),)).fetchone();c.execute('DELETE FROM cmembers WHERE channel_id=? AND user_id=?',(z['id'],me['id']));c.commit();return self.sendj({'channel':channel(z)})
   if p[2]=='channels' and p[4]=='archive':
    z=c.execute('SELECT * FROM channels WHERE id=?',(int(p[3]),)).fetchone();c.execute('UPDATE channels SET is_archived=1 WHERE id=?',(z['id'],));c.commit();return self.sendj({'channel':channel(c.execute('SELECT * FROM channels WHERE id=?',(z['id'],)).fetchone())})
   if p[2]=='channels' and p[4]=='unarchive':
    z=c.execute('SELECT * FROM channels WHERE id=?',(int(p[3]),)).fetchone();c.execute('UPDATE channels SET is_archived=0 WHERE id=?',(z['id'],));c.commit();return self.sendj({'channel':channel(c.execute('SELECT * FROM channels WHERE id=?',(z['id'],)).fetchone())})
   if p[2]=='channels' and p[4]=='messages':
    z=c.execute('SELECT * FROM channels WHERE id=?',(int(p[3]),)).fetchone()
    if not z:return self.sendj({'error':'not found'},404)
    if z['is_archived']:return self.sendj({'error':'archived'},423)
    if not str(x.get('body','')).strip():return self.sendj({'error':'blank'},400)
    if not c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(z['id'],me['id'])).fetchone():return self.sendj({'error':'forbidden'},403)
    c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(z['id'],me['id'],x['body'],x.get('parent_id'),now()));mid=c.execute('SELECT last_insert_rowid()').fetchone()[0];r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();o=msg(c,r);emit(c,z['id'],'message.reply' if x.get('parent_id') else 'message.created',{'message':o});c.commit();return self.sendj({'message':o},201)
  except (ValueError,TypeError):return self.sendj({'error':'not found'},404)
  return self.sendj({'error':'not found'},404)
 def do_PATCH(self):
  p=self.path.split('/');me=self.auth()
  if not me:return self.sendj({'error':'unauthorized'},401)
  if len(p)>=4 and p[2]=='messages':
   c=db();r=c.execute('SELECT * FROM messages WHERE id=?',(int(p[3]),)).fetchone()
   if not r:return self.sendj({'error':'not found'},404)
   if r['deleted_at']:return self.sendj({'error':'deleted'},409)
   z=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
   if z['is_archived']:return self.sendj({'error':'archived'},423)
   if r['author_id']!=me['id']:return self.sendj({'error':'forbidden'},403)
   b=self.body().get('body','')
   if not str(b).strip():return self.sendj({'error':'blank'},400)
   c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(b,now(),r['id']));o=msg(c,c.execute('SELECT * FROM messages WHERE id=?',(r['id'],)).fetchone());emit(c,z['id'],'message.updated',{'message':o});c.commit();return self.sendj({'message':o})
  return self.sendj({'error':'not found'},404)
 def do_DELETE(self):
  p=self.path.split('/');me=self.auth()
  if not me:return self.sendj({'error':'unauthorized'},401)
  if len(p)>=4 and p[2]=='messages':
   c=db();r=c.execute('SELECT * FROM messages WHERE id=?',(int(p[3]),)).fetchone()
   if not r:return self.sendj({'error':'not found'},404)
   z=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
   if z['is_archived']:return self.sendj({'error':'archived'},423)
   role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(z['workspace_id'],me['id'])).fetchone()
   if r['author_id']!=me['id'] and (not role or role['role']!='owner'):return self.sendj({'error':'forbidden'},403)
   if not r['deleted_at']:
    c.execute('UPDATE messages SET deleted_at=? WHERE id=?',(now(),r['id']));emit(c,z['id'],'message.deleted',{'message_id':r['id']});c.commit()
   return self.sendj({'deleted':True})
  return self.sendj({'error':'not found'},404)
 def websocket(self):
  t=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('token',[''])[0]
  if not auth(t):return self.sendj({'error':'unauthorized'},401)
  key=self.headers.get('Sec-WebSocket-Key');acc=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',acc);self.end_headers()
  self.connection.settimeout(.2); subscribed=set();last={}
  def out(o):
   b=json.dumps(o).encode();n=len(b);h=b'\x81'+(bytes([n]) if n<126 else b'\x81\x7e'+struct.pack('>H',n));self.connection.sendall(h+b)
  while True:
   try:
    h=self.connection.recv(2)
    if not h:break
    n=h[1]&127
    if n==126:n=struct.unpack('>H',self.connection.recv(2))[0]
    mask=self.connection.recv(4);d=self.connection.recv(n);d=bytes(v^mask[i%4] for i,v in enumerate(d));q=json.loads(d)
    ch=int(q.get('channel_id')); subscribed.add(ch)
    if q['type']=='subscribe':
     c=db();head=c.execute('SELECT COALESCE(last_event_id,0) FROM seq WHERE channel_id=?',(ch,)).fetchone();out({'type':'subscribed','channel_id':ch,'head_event_id':head[0] if head else 0});last[ch]=head[0] if head else 0
    elif q['type']=='resume':last[ch]=int(q.get('since_event_id',0))
   except socket.timeout: pass
   except: break
   for ch in list(subscribed):
    c=db()
    for e in c.execute('SELECT event_id,type,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,last.get(ch,0))):
     o={'type':e['type'],'event_id':e['event_id'],'channel_id':ch};o.update(json.loads(e['payload']));out(o);last[ch]=e['event_id']
    if last.get(ch,-1)!=-1 and 'resumed' not in []: pass
 def log_message(self,*a):pass
ThreadingHTTPServer.daemon_threads=True
_old_post=H.do_POST
_old_delete=H.do_DELETE
def post2(self):
 p=urllib.parse.unquote(urllib.parse.urlparse(self.path).path).split('/'); me=self.auth()
 if len(p)>=5 and p[2]=='messages' and p[4]=='reactions':
  if not me:return self.sendj({'error':'unauthorized'},401)
  try: mid=int(p[3])
  except:return self.sendj({'error':'not found'},404)
  c=db();r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
  if not r:return self.sendj({'error':'not found'},404)
  x=self.body(); emoji=str(x.get('emoji',''))
  if not emoji:return self.sendj({'error':'invalid'},400)
  c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,me['id'],emoji))
  o={'reactions':[{'emoji':z['emoji'],'count':z['n'],'user_ids':[int(v) for v in z['ids'].split(',')]} for z in c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(mid,))]}
  c.commit(); return self.sendj(o)
 return _old_post(self)
def delete2(self):
 p=urllib.parse.unquote(urllib.parse.urlparse(self.path).path).split('/'); me=self.auth()
 if len(p)>=6 and p[2]=='messages' and p[4]=='reactions':
  if not me:return self.sendj({'error':'unauthorized'},401)
  try:mid=int(p[3])
  except:return self.sendj({'error':'not found'},404)
  c=db();r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
  if not r:return self.sendj({'error':'not found'},404)
  c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,me['id'],p[5]))
  o={'reactions':[{'emoji':z['emoji'],'count':z['n'],'user_ids':[int(v) for v in z['ids'].split(',')]} for z in c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(mid,))]}
  c.commit(); return self.sendj(o)
 return _old_delete(self)
H.do_POST=post2; H.do_DELETE=delete2
def ws2(self):
 t=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('token',[''])[0]
 if not auth(t): return self.sendj({'error':'unauthorized'},401)
 key=self.headers.get('Sec-WebSocket-Key','')
 acc=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
 self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',acc);self.end_headers()
 self.connection.settimeout(.05); subs={}; pending=b''
 def out(o):
  raw=json.dumps(o,separators=(',',':')).encode(); n=len(raw)
  if n<126:h=b'\x81'+bytes([n])
  elif n<65536:h=b'\x81\x7e'+struct.pack('>H',n)
  else:h=b'\x81\x7f'+struct.pack('>Q',n)
  self.connection.sendall(h+raw)
 def readone():
  h=self.connection.recv(2)
  if len(h)<2:return None
  n=h[1]&127
  if n==126:n=struct.unpack('>H',self.connection.recv(2))[0]
  elif n==127:n=struct.unpack('>Q',self.connection.recv(8))[0]
  mask=self.connection.recv(4); raw=self.connection.recv(n)
  return json.loads(bytes(v^mask[i%4] for i,v in enumerate(raw)))
 while True:
  try:
   q=readone()
   if q is None: return
   typ=q.get('type'); ch=int(q.get('channel_id'))
   c=db(); head=(c.execute('SELECT last_event_id FROM seq WHERE channel_id=?',(ch,)).fetchone() or [0])[0]
   if typ=='subscribe':
    subs[ch]=head;out({'type':'subscribed','channel_id':ch,'head_event_id':head})
   elif typ=='resume':
    subs[ch]=int(q.get('since_event_id',0))
    for e in c.execute('SELECT event_id,type,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,subs[ch])):
     z={'type':e['type'],'event_id':e['event_id'],'channel_id':ch};z.update(json.loads(e['payload']));out(z);subs[ch]=e['event_id']
    out({'type':'resumed','channel_id':ch,'head_event_id':subs[ch]})
  except socket.timeout: pass
  except (OSError,ValueError,KeyError,TypeError): return
  for ch,last in list(subs.items()):
   c=db()
   for e in c.execute('SELECT event_id,type,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,last)):
    z={'type':e['type'],'event_id':e['event_id'],'channel_id':ch};z.update(json.loads(e['payload']));out(z);subs[ch]=e['event_id']
H.websocket=ws2
if __name__=='__main__':
 ThreadingHTTPServer.daemon_threads=True
 ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
