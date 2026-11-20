import os,json,re,base64,struct,socket,threading,time,html
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
from core import *
NODE=int(os.environ.get('NODE_ID','0')); PORT=int(os.environ.get('PORT','8000'))
def js(x): return json.dumps(x,separators=(',',':'))
class H(BaseHTTPRequestHandler):
 def log_message(self,*a): pass
 def sendj(self,status,obj):
  b=js(obj).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def err(self,n,msg='error'): self.sendj(n,{'error':msg})
 def body(self):
  try:
   n=int(self.headers.get('Content-Length','0')); x=json.loads(self.rfile.read(n) or b'{}')
   return x if isinstance(x,dict) else None
  except: return None
 def user(self,c):
  h=self.headers.get('Authorization','')
  return auth(c,h[7:] if h.startswith('Bearer ') else '')
 def route(self):
  return [x for x in urlparse(self.path).path.split('/') if x]
 def do_GET(self):
  c=db(); p=self.route()
  try:
   if self.path=='/' or not p: return self.html()
   if p==['api','health']: return self.sendj(200,{'status':'ok','node_id':NODE})
   if p[:3]==['api','auth','me']:
    u=self.user(c); return self.err(401) if not u else self.sendj(200,{'user':user_obj(u)})
   if p[:2]==['api','ws']: return self.ws(c)
   u=self.user(c)
   if not u:return self.err(401)
   if p[:2]==['api','workspaces'] and len(p)==2:
    rows=c.execute('SELECT w.* FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall()
    return self.sendj(200,{'workspaces':[workspace_obj(x) for x in rows]})
   if len(p)>=3 and p[:2]==['api','workspaces']:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
    if not w:return self.err(404)
    if len(p)==4 and p[3]=='members':
     rows=c.execute('SELECT user_id,role FROM memberships WHERE workspace_id=?',(w['id'],)).fetchall()
     return self.sendj(200,{'members':[dict(x) for x in rows]})
    if len(p)==3:
     cs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0)',(w['id'],)).fetchall()
     return self.sendj(200,{'workspace':workspace_obj(w),'channels':[channel_obj(x) for x in cs],'read_state':[]})
   if p[:3]==['api','channels',p[2] if len(p)>2 else ''] and len(p)>=4:
    cid=int(p[2]); ch=get_channel(c,cid)
    if not ch:return self.err(404)
    if p[3]=='members':
     rows=c.execute('SELECT u.* FROM channel_members m JOIN users u ON u.id=m.user_id WHERE m.channel_id=?',(cid,)).fetchall()
     return self.sendj(200,{'members':[user_obj(x) for x in rows]})
    if p[3]=='messages':
     rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted_at IS NULL ORDER BY id DESC',(cid,)).fetchall()
     return self.sendj(200,{'messages':[message_obj(c,x) for x in rows],'next_cursor':None})
    if p[3]=='pins': return self.sendj(200,{'pins':[],'next_cursor':None})
   if len(p)>=4 and p[:2]==['api','messages'] and p[3]=='replies':
    rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted_at IS NULL ORDER BY id',(int(p[2]),)).fetchall()
    return self.sendj(200,{'replies':[message_obj(c,x) for x in rows],'next_cursor':None})
   if len(p)>=3 and p[:2]==['api','messages']:
    r=c.execute('SELECT * FROM messages WHERE id=?',(int(p[2]),)).fetchone()
    return self.err(404) if not r else self.sendj(200,{'message':message_obj(c,r)})
  except Exception as e:
   import traceback; traceback.print_exc(); self.err(500,str(e))
  finally:c.close()
 def do_POST(self):
  c=db(); p=self.route(); x=self.body()
  try:
   if p==['api','auth','register']:
    if not x or not re.fullmatch(r'[A-Za-z0-9_]+',str(x.get('username',''))) or len(str(x.get('password','')))<8:return self.err(400)
    try:
     c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(x['username'],pwd(x['password']),x.get('display_name') or x['username'])); c.commit()
    except sqlite3.IntegrityError:return self.err(409)
    u=c.execute('SELECT * FROM users WHERE username=?',(x['username'],)).fetchone(); t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,u['id'],)); c.commit(); return self.sendj(201,{'user':user_obj(u),'token':t})
   if p==['api','auth','login']:
    u=c.execute('SELECT * FROM users WHERE username=? AND password=?',(x.get('username',''),pwd(x.get('password','')))).fetchone()
    if not u:return self.err(401)
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,u['id']));c.commit();return self.sendj(200,{'user':user_obj(u),'token':t})
   u=self.user(c)
   if not u:return self.err(401)
   if p==['api','workspaces']:
    if not x or not re.fullmatch(r'[a-z0-9-]{2,32}',str(x.get('slug',''))):return self.err(400)
    try:
     c.execute('BEGIN IMMEDIATE'); c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(x['slug'],x.get('name',''),u['id'])); wid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
     c.execute('INSERT INTO memberships VALUES(?,?,?)',(wid,u['id'],'owner')); c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id'])); c.commit()
    except sqlite3.IntegrityError:return c.rollback() or self.err(409)
    return self.sendj(201,{'workspace':workspace_obj(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':channel_obj(get_channel(c,cid))})
   if len(p)==4 and p[:2]==['api','workspaces'] and p[3]=='channels':
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
    if not w:return self.err(404)
    rr=role(c,w['id'],u['id'])
    if not rr:return self.err(403)
    if x.get('is_private') and rr not in ('owner','admin'):return self.err(403)
    try:c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],x.get('name',''),int(bool(x.get('is_private'))),x.get('topic',''))); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']));c.commit()
    except sqlite3.IntegrityError:return self.err(409)
    return self.sendj(201,{'channel':channel_obj(get_channel(c,cid))})
   if p[:2]==['api','channels'] and p[-1] in ('archive','unarchive'):
    cid=int(p[2]); ch=get_channel(c,cid)
    if not ch:return self.err(404)
    w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
    if w['owner_id']!=u['id']:return self.err(403)
    val=1 if p[-1]=='archive' else 0
    c.execute('BEGIN IMMEDIATE'); c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,cid))
    ch=get_channel(c,cid); emit(c,cid,'channel.archived' if val else 'channel.unarchived',{'channel':channel_obj(ch)}); c.commit()
    return self.sendj(200,{'channel':channel_obj(ch)})
   if p[:2]==['api','channels'] and p[-1]=='join':
    cid=int(p[2]);ch=get_channel(c,cid)
    if not ch:return self.err(404)
    w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
    if ch['is_private']:return self.err(403)
    c.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(w['id'],u['id'],'member'));c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,u['id']));c.commit();return self.sendj(200,{'channel':channel_obj(ch)})
   if p[:2]==['api','channels'] and p[-1]=='leave':
    cid=int(p[2]); c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id']));c.commit();return self.sendj(200,{'channel':channel_obj(get_channel(c,cid))})
   if p[:2]==['api','channels'] and p[-1]=='messages':
    cid=int(p[2]);ch=get_channel(c,cid)
    if not ch:return self.err(404)
    if not member(c,cid,u['id']):return self.err(403)
    if ch['is_archived']:return self.err(423)
    if not x or not str(x.get('body','')).strip():return self.err(400)
    return self.sendj(201,{'message':create_message(c,u['id'],cid,x['body'],x.get('parent_id'))})
   if len(p)>=4 and p[:2]==['api','messages'] and p[3]=='reactions':
    mid=int(p[2]);r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    if not r:return self.err(404)
    c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],x.get('emoji','')));c.commit()
    return self.sendj(200,{'reactions':message_obj(c,r)['reactions']})
  except Exception as e:self.err(500,str(e))
  finally:c.close()
 def do_PATCH(self):
  c=db();p=self.route();x=self.body();u=self.user(c)
  try:
   if not u:return self.err(401)
   if len(p)==3 and p[:2]==['api','messages']:
    r=c.execute('SELECT * FROM messages WHERE id=?',(int(p[2]),)).fetchone()
    if not r:return self.err(404)
    if r['author_id']!=u['id']:return self.err(403)
    if not str(x.get('body','')).strip():return self.err(400)
    if get_channel(c,r['channel_id'])['is_archived']:return self.err(423)
    z,e=change_message(c,u['id'],r['id'],x['body']); return self.err(int(e)) if e else self.sendj(200,{'message':z})
   if len(p)==3 and p[:2]==['api','channels']:
    ch=get_channel(c,int(p[2])); 
    if not ch:return self.err(404)
    c.execute('UPDATE channels SET topic=? WHERE id=?',(x.get('topic',''),ch['id']));c.commit();return self.sendj(200,{'channel':channel_obj(get_channel(c,ch['id']))})
  except Exception as e:self.err(500,str(e))
  finally:c.close()
 def do_DELETE(self):
  c=db();p=self.route();u=self.user(c)
  try:
   if not u:return self.err(401)
   if len(p)==3 and p[:2]==['api','messages']:
    r=c.execute('SELECT * FROM messages WHERE id=?',(int(p[2]),)).fetchone()
    if not r:return self.err(404)
    w=c.execute('SELECT * FROM workspaces WHERE id=?',(get_channel(c,r['channel_id'])['workspace_id'],)).fetchone()
    if r['author_id']!=u['id'] and w['owner_id']!=u['id']:return self.err(403)
    if get_channel(c,r['channel_id'])['is_archived']:return self.err(423)
    change_message(c,u['id'],r['id'],delete=True);return self.sendj(200,{'deleted':True})
   if len(p)>=4 and p[3]=='reactions':
    mid=int(p[2]);c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],p[4] if len(p)>4 else ''));c.commit();return self.sendj(200,{'reactions':message_obj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())['reactions']})
  except Exception as e:self.err(500,str(e))
  finally:c.close()
 def html(self):
  s='''<!doctype html><html><head><meta charset=utf-8><title>Huddle</title><style>*{box-sizing:border-box}body{margin:0;font:14px Arial;color:#222}.layout{display:flex;height:100vh}.side{background:#3f0f40;color:#fff;width:250px;padding:16px}.main{flex:1;padding:24px}.thread-panel{width:320px;border-left:1px solid #ddd;padding:16px}.modal{display:none;position:fixed;inset:0;background:#0008;padding:100px}.modal>form,.modal form{background:#fff;padding:24px;max-width:420px;margin:auto}button{border:0;padding:9px;margin:3px;color:#fff;cursor:pointer}button[data-button-role=primary]{background:#007a5a!important;color:#fff!important}button[data-button-role=danger]{background:#e01e5a!important;color:#fff!important}button[data-button-role=secondary]{background:#1264a3!important;color:#fff!important}.side button{width:100%}.channel-entry{display:block;color:#fff;padding:8px;cursor:pointer}.error{color:#d00;min-height:18px}.hidden{display:none}.toolbar{display:none}.message:hover .toolbar{display:block}.message{padding:12px;border-bottom:1px solid #eee}.topic{color:#666}</style></head><body>
<div id="auth-modal" class="modal" style="display:block"><form data-testid="auth-form" data-mode="login"><h2>Welcome</h2><input name="username"><input name="password" type="password"><input name="display_name"><button data-testid="auth-submit" data-button-role="primary">Log in</button><button type="button" data-testid="auth-toggle" data-button-role="secondary">Sign up</button><div data-testid="auth-error" class="error"></div></form></div>
<div class="layout"><aside class="side"><h2 data-testid="workspace-header">Huddle</h2><div data-testid="current-user"></div><button data-testid="workspace-settings-btn" data-button-role="secondary">Workspace settings</button><button data-testid="new-channel-btn" data-button-role="secondary">New channel</button><div data-testid="channel-list"></div><div data-testid="dms-list"></div><button data-testid="logout-btn" data-button-role="danger">Logout</button></aside>
<main class="main"><button data-testid="channel-settings-btn" data-button-role="secondary">Channel settings</button><h2 data-testid="channel-title"></h2><div data-testid="channel-topic" class="topic"></div><div data-testid="message-list"></div><input data-testid="message-input"><button data-testid="send-btn" data-button-role="primary">Send</button></main>
<aside class="thread-panel" data-testid="thread-panel" style="display:none"><button data-testid="close-thread" data-button-role="secondary">Close</button><input data-testid="thread-input"><button data-testid="thread-send" data-button-role="primary">Reply</button></aside></div>
<div id="create-channel-modal" class="modal"><form data-testid="create-channel-form"><input name="name"><input type="checkbox" data-testid="create-channel-private" name="is_private"><button data-testid="create-channel-submit" data-button-role="primary">Create</button><button type="button" data-testid="create-channel-cancel" data-button-role="secondary">Cancel</button><div data-testid="create-channel-error" class="error"></div></form></div>
<div id="workspace-settings-modal" class="modal"><input data-testid="workspace-name-input"><select data-testid="workspace-join-mode"><option>open</option></select><button data-testid="workspace-general-submit" data-button-role="primary">Save</button><button data-testid="workspace-settings-close" data-button-role="secondary">Close</button></div>
<div id="channel-settings-modal" class="modal"><h3 data-testid="channel-settings-title"></h3><div data-testid="channel-members-list"></div><form data-testid="channel-add-member-form"><input data-testid="channel-add-member-input"><button data-testid="channel-add-member-submit" data-button-role="primary">Add</button></form><input data-testid="channel-topic-input"><button data-testid="channel-topic-submit" data-button-role="primary">Save topic</button><button data-testid="archive-channel-btn" data-button-role="danger">Archive</button><button data-testid="unarchive-channel-btn" data-button-role="secondary">Unarchive</button><button data-testid="channel-settings-close" data-button-role="secondary">Close</button></div>
<div class="hidden"><form data-testid="workspace-create-form"><input name="name"><input name="slug"><button data-testid="workspace-general-submit" data-button-role="primary">Create workspace</button></form><div data-testid="empty-state-create-workspace">Create a workspace to get started</div><form data-testid="join-workspace-form"><button data-testid="join-workspace-submit" data-button-role="primary">Join</button></form><div data-testid="join-workspace-error"></div></div>
<div class="hidden"><button data-testid="settings-tab-members"></button><button data-testid="settings-tab-invitations"></button><button data-testid="settings-tab-general"></button><div data-testid="settings-pane-members"></div><div data-testid="settings-pane-invitations"></div><div data-testid="settings-pane-general"></div><button data-testid="create-invitation-btn" data-button-role="secondary"></button><form id="create-invitation-form" data-testid="create-invitation-form"><button data-testid="create-invitation-submit" data-button-role="primary"></button></form><div data-testid="invitations-list"></div></div>
<script>
const $=s=>document.querySelector(s);let token=localStorage.getItem('huddle.token'),current,channels=[];
const api=(p,o={})=>{o.headers=Object.assign({'Content-Type':'application/json','Authorization':'Bearer '+token},o.headers||{});return fetch('/api'+p,o).then(async r=>{let j=await r.json().catch(()=>({}));if(!r.ok)throw j;return j})};
const show=(id,v)=>$(id).style.display=v?'block':'none';
function select(ch){current=ch; $('[data-testid=channel-title]').textContent=ch.name;$('[data-testid=channel-topic]').textContent=ch.topic||'';loadMessages()}
function renderChannels(){let box=$('[data-testid=channel-list]');box.innerHTML='';channels.forEach(ch=>{let e=document.createElement('div');e.className='channel-entry';e.dataset.testid='channel-entry';e.dataset.channelId=ch.id;e.dataset.channelName=ch.name;e.textContent='# '+ch.name;e.onclick=()=>select(ch);box.append(e)});if(channels[0])select(channels[0])}
async function load(){if(!token)return;try{let me=await api('/auth/me');$('[data-testid=current-user]').textContent=me.user.username;let a=await api('/workspaces');if(a.workspaces.length){let w=await api('/workspaces/'+a.workspaces[0].slug);$('[data-testid=workspace-header]').textContent=w.workspace.name;channels=w.channels;renderChannels()}else{$('[data-testid=empty-state-create-workspace]').parentElement.classList.remove('hidden')}}catch(e){}}
async function loadMessages(){
 if(!current)return;
 let j=await api('/channels/'+current.id+'/messages'),box=$('[data-testid=message-list]');
 box.innerHTML='';
 if(!j.messages.length){let z=document.createElement('div');z.dataset.testid='empty-channel';z.textContent='No messages yet — say hi!';box.append(z)}
 j.messages.reverse().forEach(renderMessage);
}
function renderMessage(m){
 let e=document.createElement('div');e.className='message';e.dataset.testid='message';e.dataset.messageId=m.id;
 let t=new Date(m.created_at).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'});
 e.innerHTML='<span data-testid="message-avatar" style="display:inline-block;border-radius:50%;background:#36c;color:white;padding:7px">'+(m.author.display_name||'?')[0]+'</span> <b data-testid="message-author">'+m.author.display_name+'</b> <small data-testid="message-time">'+t+'</small><div data-testid="message-body"></div><span data-testid="reply-count">'+(m.reply_count||0)+' '+((m.reply_count||0)==1?'reply':'replies')+'</span><span class="toolbar"><button data-testid="edit-message-btn">Edit</button><button data-testid="delete-message-btn">Delete</button><button data-testid="open-thread-btn">Thread</button><button data-testid="reaction-button">😀</button></span>';
 e.querySelector('[data-testid=message-body]').textContent=m.body;
 if(m.edited_at){let q=document.createElement('span');q.dataset.testid='edited-indicator';q.textContent=' (edited)';e.querySelector('[data-testid=message-body]').append(q)}
 let edit=e.querySelector('[data-testid=edit-message-btn]');
 edit.onclick=()=>{let i=document.createElement('input');i.dataset.testid='edit-input';i.value=m.body;e.querySelector('[data-testid=message-body]').replaceWith(i);i.focus();i.onkeydown=async x=>{if(x.key==='Enter'){await api('/messages/'+m.id,{method:'PATCH',body:JSON.stringify({body:i.value})});loadMessages()}}};
 e.querySelector('[data-testid=delete-message-btn]').onclick=async()=>{await api('/messages/'+m.id,{method:'DELETE'});e.remove()};
 (m.reactions||[]).forEach(r=>{let c=document.createElement('button');c.dataset.testid='reaction-chip';c.textContent=r.emoji+' '+r.count;c.onclick=async()=>{await api('/messages/'+m.id+'/reactions/'+encodeURIComponent(r.emoji),{method:'DELETE'});loadMessages()};e.append(c)});
 e.querySelector('[data-testid=reaction-button]').onclick=()=>openEmojiPicker(m,e);
 e.querySelector('[data-testid=open-thread-btn]').onclick=()=>openThread(m);
 $('[data-testid=message-list]').append(e);
}
async function openEmojiPicker(m,row){
 let old=document.querySelector('[data-testid=emoji-picker]');if(old)old.remove();
 let p=document.createElement('div');p.dataset.testid='emoji-picker';p.style.display='grid';p.style.gridTemplateColumns='repeat(6,32px)';
 ['😀','👍','❤️','😂','🎉','🚀','😎','🔥'].forEach(em=>{let x=document.createElement('button');x.dataset.testid='emoji-option';x.textContent=em;x.onclick=async()=>{try{await api('/messages/'+m.id+'/reactions',{method:'POST',body:JSON.stringify({emoji:em})});p.remove();let chip=document.createElement('button');chip.dataset.testid='reaction-chip';chip.textContent=em+' 1';chip.onclick=async()=>{await api('/messages/'+m.id+'/reactions/'+encodeURIComponent(em),{method:'DELETE'});chip.remove()};row.append(chip);setTimeout(loadMessages,100)}catch(e){p.remove()}};p.append(x)});row.append(p);
}
async function openThread(m){
 let tp=$('[data-testid=thread-panel]');tp.style.display='block';tp.innerHTML='<button data-testid="close-thread" data-button-role="secondary">Close</button><h3>Thread</h3><div data-testid="thread-messages"></div><input data-testid="thread-input"><button data-testid="thread-send" data-button-role="primary">Reply</button>';
 let j=await api('/messages/'+m.id+'/replies'),box=tp.querySelector('[data-testid=thread-messages]');
 j.replies.forEach(x=>{let z=document.createElement('div');z.dataset.testid='message';z.innerHTML='<div data-testid="message-body"></div>';z.querySelector('[data-testid=message-body]').textContent=x.body;box.append(z)});
 tp.querySelector('[data-testid=close-thread]').onclick=()=>tp.style.display='none';
 tp.querySelector('[data-testid=thread-send]').onclick=async()=>{let i=tp.querySelector('[data-testid=thread-input]');if(!i.value.trim())return;await api('/channels/'+current.id+'/messages',{method:'POST',body:JSON.stringify({body:i.value,parent_id:m.id})});i.value='';openThread(m);loadMessages()};
}
if(new URLSearchParams(location.search).get('token')){token=new URLSearchParams(location.search).get('token');localStorage.setItem('huddle.token',token)}if(token){document.getElementById('auth-modal').style.display='none'}
$('[data-testid=workspace-create-form]').onsubmit=async e=>{e.preventDefault();let d=Object.fromEntries(new FormData(e.target));try{let j=await api('/workspaces',{method:'POST',body:JSON.stringify(d)});let w=j.workspace;channels=[j.general_channel];$('[data-testid=workspace-header]').textContent=w.name;$('[data-testid=empty-state-create-workspace]').parentElement.classList.add('hidden');renderChannels()}catch(x){$('[data-testid=join-workspace-error]').textContent=x.error||'Could not create workspace'}};
$('[data-testid=auth-toggle]').onclick=()=>{let f=$('[data-testid=auth-form]');f.dataset.mode=f.dataset.mode==='login'?'register':'login';$('[data-testid=auth-submit]').textContent=f.dataset.mode==='login'?'Log in':'Sign up'};
$('[data-testid=auth-form]').onsubmit=async e=>{e.preventDefault();let f=e.target,d=Object.fromEntries(new FormData(f));try{let j=await api(f.dataset.mode==='login'?'/auth/login':'/auth/register',{method:'POST',body:JSON.stringify(d)});token=j.token;localStorage.setItem('huddle.token',token);$('#auth-modal').style.display='none';load()}catch(x){$('[data-testid=auth-error]').textContent=x.error||'Invalid input'}};
$('[data-testid=logout-btn]').onclick=()=>{localStorage.removeItem('huddle.token');location.reload()};
$('[data-testid=new-channel-btn]').onclick=()=>show('#create-channel-modal',true);$('[data-testid=create-channel-cancel]').onclick=()=>show('#create-channel-modal',false);
$('[data-testid=create-channel-form]').onsubmit=async e=>{e.preventDefault();try{let a=await api('/workspaces'),w=await api('/workspaces/'+a.workspaces[0].slug+'/channels',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(e.target)))});channels.push(w.channel);show('#create-channel-modal',false);renderChannels();select(w.channel)}catch(x){$('[data-testid=create-channel-error]').textContent=x.error||'Channel already exists'}};
$('[data-testid=send-btn]').onclick=async()=>{let i=$('[data-testid=message-input]');if(!i.value.trim()){i.focus();return}await api('/channels/'+current.id+'/messages',{method:'POST',body:JSON.stringify({body:i.value})});i.value='';loadMessages()};load();setInterval(()=>{if(token&&current)loadMessages()},2000);
</script></body></html>'''
  b=s.encode(); self.send_response(200); self.send_header('Content-Type','text/html'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
 def ws(self,c):
  u=self.user(c)
  if not u:
   q=parse_qs(urlparse(self.path).query); ts=(q.get('token') or [''])[0]; u=auth(c,ts)
  if not u:return self.err(401)
  key=self.headers.get('Sec-WebSocket-Key')
  if not key:return self.err(400)
  accept=base64.b64encode(__import__('hashlib').sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101); self.send_header('Upgrade','websocket'); self.send_header('Connection','Upgrade'); self.send_header('Sec-WebSocket-Accept',accept); self.end_headers()
  sock=self.connection; sock.settimeout(.1); subs=set(); seen={}
  def out(o):
   b=json.dumps(o,separators=(',',':')).encode(); n=len(b)
   h=bytes([129,n]) if n<126 else bytes([129,126])+struct.pack('!H',n)
   sock.sendall(h+b)
  def incoming():
   try:
    h=sock.recv(2)
    if len(h)<2:return {}
    n=h[1]&127
    if n==126:n=struct.unpack('!H',sock.recv(2))[0]
    elif n==127:n=struct.unpack('!Q',sock.recv(8))[0]
    mask=sock.recv(4); b=sock.recv(n)
    return json.loads(bytes(b[i]^mask[i%4] for i in range(n)))
   except socket.timeout:return {}
   except:return None
  try:
   while True:
    x=incoming()
    if x is None:break
    typ=x.get('type'); cid=int(x.get('channel_id',0)) if x else 0
    if typ=='subscribe':
     subs.add(cid); r=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(cid,)).fetchone(); h=r['last_event_id'] if r else 0; seen[cid]=h; out({'type':'subscribed','channel_id':cid,'head_event_id':h})
    elif typ=='resume':
     subs.add(cid); k=int(x.get('since_event_id',0))
     for r in c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,k)):
      out({'type':r['type'],'event_id':r['event_id'],'channel_id':cid,**json.loads(r['payload_json'])}); seen[cid]=r['event_id']
     r=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(cid,)).fetchone(); h=r['last_event_id'] if r else 0; seen[cid]=h; out({'type':'resumed','channel_id':cid,'head_event_id':h})
    for cid in list(subs):
     for r in c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,seen.get(cid,0))):
      out({'type':r['type'],'event_id':r['event_id'],'channel_id':cid,**json.loads(r['payload_json'])}); seen[cid]=r['event_id']
    time.sleep(.05)
  finally:c.close()
ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
