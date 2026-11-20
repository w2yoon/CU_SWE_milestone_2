import sqlite3
import os,sys,json,re,urllib.parse,threading,time,base64,hashlib,socket,struct,html,cgi,secrets
from zoneinfo import ZoneInfo
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
import core
NODE=int(os.environ.get('NODE_ID','0')); PORT=int(os.environ.get('PORT','8000'))
try:
 c0=core.db();c0.execute('DELETE FROM presence WHERE node_id=?',(NODE,));c0.commit();c0.close()
except: pass
WS_CLIENTS=[]; WS_LOCK=threading.RLock()
def sendws(s,obj):
    b=json.dumps(obj,separators=(',',':')).encode(); n=len(b)
    if n<126: h=bytes([0x81,n])
    elif n<65536:h=bytes([0x81,126])+struct.pack('>H',n)
    else:h=bytes([0x81,127])+struct.pack('>Q',n)
    try: s.sendall(h+b); return True
    except Exception: return False
def broadcast(cid,event):
    with WS_LOCK:
        for x in list(WS_CLIENTS):
            if x['cid']==cid: sendws(x['s'],event)
def event_poller():
    last={}; glast=0
    while True:
        try:
            c=core.db()
            for r in c.execute('SELECT * FROM events ORDER BY channel_id,event_id'):
                if r['event_id']>last.get(r['channel_id'],0):
                    p=json.loads(r['payload_json']); p.update(type=r['type'],event_id=r['event_id'],channel_id=r['channel_id']); broadcast(r['channel_id'],p); last[r['channel_id']]=r['event_id']
            for r in c.execute('SELECT * FROM global_events WHERE id>? ORDER BY id',(glast,)):
                broadcast_global(json.loads(r['payload'])); glast=r['id']
            c.close()
        except Exception: pass
        time.sleep(.05)
def broadcast_global(event):
    with WS_LOCK:
        for x in list(WS_CLIENTS): sendws(x['s'],event)
threading.Thread(target=event_poller,daemon=True).start()
def html_page():
 return '<!doctype html><html><head><meta charset="utf-8"><style>body{margin:0;font:14px Arial}.app{display:flex;height:100vh}.side{width:240px;background:#3f0f42;color:#fff;padding:16px}.main{flex:1;padding:20px}.thread{width:300px;border-left:1px solid #ddd;padding:16px}.hidden{display:none!important}.modal{position:fixed;inset:0;background:#0008;display:flex;align-items:center;justify-content:center}.modal>form,.modal>div{background:white;padding:20px;min-width:300px}input,select{padding:8px;margin:5px;width:90%}[data-button-role=primary]{background:#007a5a;color:#fff}[data-button-role=danger]{background:#e01e5a;color:#fff}[data-button-role=secondary]{background:#1264a3;color:#fff}.channel-entry{padding:7px}.msg{padding:10px;border-bottom:1px solid #ddd}</style></head><body>\n<div id="auth-modal" class="modal"><form data-testid="auth-form" data-mode="login"><div data-testid="auth-error"></div><input name="username"><input name="password" type="password"><input name="display_name"><button data-testid="auth-submit" data-button-role="primary">Log in</button><button type="button" data-testid="auth-toggle" data-button-role="secondary">Sign up</button></form></div>\n<div class="app"><aside class="side"><h2 data-testid="workspace-header">Huddle</h2><button data-testid="workspace-settings-btn" data-button-role="secondary">Workspace</button><div data-testid="current-user"></div><button data-testid="new-channel-btn" data-button-role="secondary">New channel</button><div data-testid="channel-list"></div><div data-testid="dms-list"></div><button data-testid="logout-btn" data-button-role="danger">Logout</button></aside><main class="main"><h2 data-testid="channel-title"></h2><div data-testid="channel-topic"></div><button data-testid="channel-settings-btn" data-button-role="secondary">Channel settings</button><div data-testid="message-list"></div><form id="message-form"><input data-testid="message-input"><button data-testid="send-btn" data-button-role="primary">Send</button></form></main><aside class="thread hidden" data-testid="thread-panel"><button data-testid="close-thread" data-button-role="secondary">Close</button><div id="thread-content"></div><form id="thread-form"><input data-testid="thread-input"><button data-testid="thread-send" data-button-role="primary">Reply</button></form></aside></div>\n<div id="create-channel-modal" class="modal hidden"><form data-testid="create-channel-form"><input name="name"><input name="topic"><input name="is_private" type="checkbox" data-testid="create-channel-private"><div data-testid="create-channel-error"></div><button data-testid="create-channel-submit" data-button-role="primary">Create</button><button type="button" data-testid="create-channel-cancel" data-button-role="secondary">Cancel</button></form></div>\n<div id="workspace-settings-modal" class="modal hidden"><div><button data-testid="workspace-settings-close" data-button-role="secondary">Close</button><input data-testid="workspace-name-input"><select data-testid="workspace-join-mode"><option>open</option><option>invite_only</option></select><button data-testid="workspace-general-submit" data-button-role="primary">Save</button><button data-testid="settings-tab-members">Members</button><button data-testid="settings-tab-invitations">Invitations</button><button data-testid="settings-tab-general">General</button><div data-testid="settings-pane-members"></div><div data-testid="settings-pane-invitations"></div><div data-testid="settings-pane-general"></div><button data-testid="create-invitation-btn" data-button-role="secondary">Invite</button><form id="create-invitation-form" data-testid="create-invitation-form"><button data-testid="create-invitation-submit" data-button-role="primary">Create</button></form><div data-testid="invitations-list"></div></div></div>\n<div id="channel-settings-modal" class="modal hidden"><div><h3 data-testid="channel-settings-title"></h3><button data-testid="channel-settings-close" data-button-role="secondary">Close</button><div data-testid="channel-members-list"></div><form data-testid="channel-add-member-form"><input data-testid="channel-add-member-input"><button data-testid="channel-add-member-submit" data-button-role="primary">Add</button></form><input data-testid="channel-topic-input"><button data-testid="channel-topic-submit" data-button-role="primary">Save</button><button data-testid="archive-channel-btn" data-button-role="danger">Archive</button><button data-testid="unarchive-channel-btn" data-button-role="secondary">Unarchive</button></div></div>\n<form class="hidden" data-testid="join-workspace-form"><button data-testid="join-workspace-submit" data-button-role="primary">Join</button><div data-testid="join-workspace-error"></div></form>\n<div class="hidden" data-testid="empty-state-create-workspace"><form data-testid="workspace-create-form"><input name="name"><input name="slug"></form></div>\n<script>\nconst q=s=>document.querySelector(s),tok=()=>localStorage.getItem(\'huddle.token\');\nasync function api(p,o={}){o.headers={...(o.headers||{}),Authorization:\'Bearer \'+tok(),\'Content-Type\':\'application/json\'};let r=await fetch(\'/api\'+p,o),d=await r.json().catch(()=>({}));if(!r.ok)throw d;return d}\nlet me,ws,ch,channels=[],threadId=null;\nconst esc=x=>String(x||\'\').replace(/[&<>"]/g,c=>({\'&\':\'&amp;\',\'<\':\'&lt;\',\'>\':\'&gt;\',\'"\':\'&quot;\'}[c]));\nfunction render(){q(\'[data-testid=channel-list]\').innerHTML=channels.map(x=>\'<div data-testid="channel-entry" class="channel-entry" data-channel-id="\'+x.id+\'" data-channel-name="\'+x.name+\'" onclick="selectChannel(\'+x.id+\')"># \'+x.name+\'</div>\').join(\'\')}\nasync function selectChannel(id){ch=channels.find(x=>x.id==id);if(!ch)return;q(\'[data-testid=channel-title]\').textContent=ch.name;q(\'[data-testid=channel-topic]\').textContent=ch.topic||\'\';await refresh()}\nasync function refresh(){if(!ch)return;let d=await api(\'/channels/\'+ch.id+\'/messages\');renderMessages(d.messages)}\nfunction renderMessages(ms){let a=ms.filter(x=>!x.parent_id),b=q(\'[data-testid=message-list]\');b.innerHTML=a.map(m=>\'<div data-testid="message" class="msg" data-message-id="\'+m.id+\'"><span data-testid="message-avatar">\'+esc((m.author.display_name||\'?\')[0])+\'</span> <b data-testid="message-author">\'+esc(m.author.display_name)+\'</b> <span data-testid="message-time">\'+new Date(m.created_at).toLocaleTimeString()+\'</span><div data-testid="message-body">\'+esc(m.body)+\'</div>\'+(m.edited_at?\'<span data-testid="edited-indicator">(edited)</span>\':\'\')+\'<span data-testid="reply-count">\'+(m.reply_count?(m.reply_count+\' repl\'+(m.reply_count==1?\'y\':\'ies\')):\'\')+\'</span><div class="toolbar"><button data-testid="open-thread-btn" onclick="openThread(\'+m.id+\')">Thread</button><button data-testid="reaction-button" onclick="pickEmoji(\'+m.id+\')">☺</button>\'+(m.author_id==me.id?\'<button data-testid="edit-message-btn" onclick="editMessage(\'+m.id+\')">Edit</button><button data-testid="delete-message-btn" onclick="deleteMessage(\'+m.id+\')">Delete</button>\':\'\')+\'</div>\'+(m.reactions||[]).map(r=>\'<button data-testid="reaction-chip" onclick="removeReaction(\'+m.id+\',\\\'\'+encodeURIComponent(r.emoji)+\'\\\')">\'+r.emoji+\' \'+r.count+\'</button>\').join(\'\')+\'</div>\').join(\'\')||\'<div data-testid="empty-channel" class="empty">No messages yet — say hi!</div>\'}\nasync function editMessage(id){let r=q(\'[data-message-id="\'+id+\'"]\'),b=r.querySelector(\'[data-testid=message-body]\');let i=document.createElement(\'input\');i.setAttribute(\'data-testid\',\'edit-input\');i.value=b.textContent;b.replaceWith(i);i.focus();i.onkeydown=async e=>{if(e.key===\'Enter\'){await api(\'/messages/\'+id,{method:\'PATCH\',body:JSON.stringify({body:i.value})});refresh()}}}\nasync function deleteMessage(id){await api(\'/messages/\'+id,{method:\'DELETE\'});refresh()}\nfunction pickEmoji(id){let old=q(\'[data-testid=emoji-picker]\');if(old)old.remove();let p=document.createElement(\'div\');p.setAttribute(\'data-testid\',\'emoji-picker\');p.className=\'emoji-picker\';p.style.display=\'grid\';[\'😀\',\'👍\',\'🎉\',\'❤️\',\'🚀\',\'😂\',\'👏\',\'✅\'].forEach(e=>{let b=document.createElement(\'button\');b.setAttribute(\'data-testid\',\'emoji-option\');b.textContent=e;b.onclick=async()=>{await api(\'/messages/\'+id+\'/reactions\',{method:\'POST\',body:JSON.stringify({emoji:e})});p.remove();refresh()};p.appendChild(b)});q(\'[data-message-id="\'+id+\'"]\').appendChild(p)}\nasync function removeReaction(id,e){await api(\'/messages/\'+id+\'/reactions/\'+e,{method:\'DELETE\'});refresh()}\nasync function openThread(id){threadId=id;q(\'[data-testid=thread-panel]\').classList.remove(\'hidden\');let d=await api(\'/messages/\'+id+\'/replies\');q(\'#thread-content\').innerHTML=\'<div data-testid="message-body">Thread</div>\'+d.replies.map(x=>\'<div data-testid="message"><div data-testid="message-body">\'+esc(x.body)+\'</div></div>\').join(\'\')}\nq(\'[data-testid=close-thread]\').onclick=()=>q(\'[data-testid=thread-panel]\').classList.add(\'hidden\');\nq(\'#thread-form\').onsubmit=async e=>{e.preventDefault();let i=q(\'[data-testid=thread-input]\');if(!i.value.trim())return;await api(\'/channels/\'+ch.id+\'/messages\',{method:\'POST\',body:JSON.stringify({body:i.value,parent_id:threadId})});i.value=\'\';await openThread(threadId);refresh()};\nq(\'#message-form\').onsubmit=async e=>{e.preventDefault();let i=q(\'[data-testid=message-input]\');if(!i.value.trim()){i.focus();return}await api(\'/channels/\'+ch.id+\'/messages\',{method:\'POST\',body:JSON.stringify({body:i.value})});i.value=\'\';refresh()};\nasync function loadWorkspace(){let a=await api(\'/workspaces\');if(!a.workspaces.length){let e=q(\'[data-testid=empty-state-create-workspace]\');e.classList.remove(\'hidden\');q(\'[data-testid=workspace-create-form]\').onsubmit=async x=>{x.preventDefault();await api(\'/workspaces\',{method:\'POST\',body:JSON.stringify(Object.fromEntries(new FormData(x.target)))});location.reload()};return}ws=a.workspaces[0];q(\'[data-testid=workspace-header]\').textContent=ws.name;channels=(await api(\'/workspaces/\'+ws.slug)).channels;render();if(channels[0])selectChannel(channels[0].id)}\nq(\'[data-testid=auth-toggle]\').onclick=()=>{let f=q(\'[data-testid=auth-form]\');f.dataset.mode=f.dataset.mode===\'login\'?\'register\':\'login\';q(\'[data-testid=auth-submit]\').textContent=f.dataset.mode===\'login\'?\'Log in\':\'Sign up\'};\nq(\'[data-testid=auth-form]\').onsubmit=async e=>{e.preventDefault();let f=e.target;try{let r=await fetch(\'/api/auth/\'+(f.dataset.mode===\'register\'?\'register\':\'login\'),{method:\'POST\',headers:{\'Content-Type\':\'application/json\'},body:JSON.stringify(Object.fromEntries(new FormData(f)))}),d=await r.json();if(!r.ok)throw d;localStorage.setItem(\'huddle.token\',d.token);window.__huddle_token=d.token;location.reload()}catch(x){q(\'[data-testid=auth-error]\').textContent=x.error||\'Invalid input\'}};\nq(\'[data-testid=logout-btn]\').onclick=()=>{localStorage.removeItem(\'huddle.token\');location.reload()};q(\'[data-testid=new-channel-btn]\').onclick=()=>q(\'#create-channel-modal\').classList.remove(\'hidden\');q(\'[data-testid=create-channel-cancel]\').onclick=()=>q(\'#create-channel-modal\').classList.add(\'hidden\');\nq(\'[data-testid=create-channel-form]\').onsubmit=async e=>{e.preventDefault();try{let x=Object.fromEntries(new FormData(e.target));x.is_private=e.target.is_private.checked;let z=await api(\'/workspaces/\'+ws.slug+\'/channels\',{method:\'POST\',body:JSON.stringify(x)});q(\'#create-channel-modal\').classList.add(\'hidden\');channels=(await api(\'/workspaces/\'+ws.slug)).channels;render();selectChannel(z.channel.id)}catch(x){q(\'[data-testid=create-channel-error]\').textContent=x.error||\'Error\'}};\nq(\'[data-testid=close-thread]\').onclick=()=>q(\'[data-testid=thread-panel]\').classList.add(\'hidden\');\n(async()=>{let t=new URLSearchParams(location.search).get(\'token\');if(t)localStorage.setItem(\'huddle.token\',t);try{me=(await api(\'/auth/me\')).user;q(\'#auth-modal\').classList.add(\'hidden\');q(\'[data-testid=current-user]\').textContent=me.username;loadWorkspace();setInterval(refresh,1000)}catch(e){}})();\n</script></body></html>'
def jsonbody(h):
 try:
  n=int(h.headers.get('Content-Length','0')); x=json.loads(h.rfile.read(n) or '{}'); return x if isinstance(x,dict) else None
 except:return None
def file_obj(r):
    return {k:r[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}
def multipart_file(h):
    n=int(h.headers.get('Content-Length','0') or 0)
    if n>10485760:
        h.rfile.read(n)
        return None,'large'
    raw=h.rfile.read(n)
    ct=h.headers.get('Content-Type','')
    m=re.search(r'boundary="?([^";]+)',ct)
    if not m:return None,'part'
    boundary=('--'+m.group(1)).encode()
    for part in raw.split(boundary):
        if b'Content-Disposition' not in part:continue
        head,sep,body=part.partition(b'\r\n\r\n')
        if not sep:continue
        disp=head.decode('utf-8','ignore')
        if 'name="file"' not in disp:continue
        body=body.rstrip(b'\r\n-')
        fn=re.search(r'filename="([^"]*)"',disp)
        filename=fn.group(1) if fn else 'file'
        mt=re.search(r'\r\nContent-Type:\s*([^\r\n]+)',head.decode('utf-8','ignore'),re.I)
        typ=mt.group(1).strip() if mt else 'application/octet-stream'
        return (filename,typ,body),None
    return None,'part'
class H(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a): pass
 def out(self,code,obj,headers=None):
  b=json.dumps(obj,separators=(',',':')).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.send_header('Connection','close')
  for k,v in (headers or {}).items():self.send_header(k,v)
  self.end_headers();self.wfile.write(b)
 def user(self):
  a=self.headers.get('Authorization',''); return core.token_user(a[7:] if a.startswith('Bearer ') else '')
 def do_GET(self):
  p=urllib.parse.urlparse(self.path); path=p.path; q=urllib.parse.parse_qs(p.query)
  if path=='/': b=html_page().encode();self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
  if path=='/api/health':return self.out(200,{'status':'ok','node_id':NODE})
  if path=='/api/ws': return self.websocket(q.get('token',[''])[0])
  u=self.user()
  if path=='/api/auth/me':
   return self.out(200,{'user':core.user_obj(u)}) if u else self.out(401,{'error':'unauthorized'})
  if not u:return self.out(401,{'error':'unauthorized'})
  c=core.db()
  try:
   m=re.fullmatch(r'/api/workspaces/([^/]+)/groups',path)
   if m:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
    if not w:return self.out(404,{'error':'not found'})
    if not core.member(c,w['id'],u['id']):return self.out(403,{'error':'forbidden'})
    out=[]
    for z in c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)):
     out.append({'id':z['id'],'handle':z['handle'],'name':z['name'],'member_user_ids':json.loads(z['user_ids'])})
    return self.out(200,{'groups':out})
   if path=='/api/workspaces':
    a=[core.workspace_obj(x) for x in c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],))];return self.out(200,{'workspaces':a})
   m=re.fullmatch(r'/api/workspaces/([^/]+)',path)
   if m:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
    if not w:return self.out(404,{'error':'not found'})
    a=[core.channel_obj(x) for x in c.execute('SELECT * FROM channels WHERE workspace_id=?'+('' if q.get('include_archived',[''])[0] in ('1','true') else ' AND is_archived=0'),(w['id'],)) if core.accessible(c,x,u['id'])]
    return self.out(200,{'workspace':core.workspace_obj(w),'channels':a,'read_state':[]})
   m=re.fullmatch(r'/api/channels/(\d+)/messages',path)
   if m:
    x=core.chan(c,int(m.group(1)))
    if not x:return self.out(404,{'error':'not found'})
    if not core.accessible(c,x,u['id']):return self.out(403,{'error':'forbidden'})
    a=[core.msg_obj(c,r) for r in c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 ORDER BY id DESC LIMIT 200',(x['id'],))];return self.out(200,{'messages':a,'next_cursor':None})
   m=re.fullmatch(r'/api/messages/(\d+)/replies',path)
   if m:
    a=[core.msg_obj(c,r) for r in c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(int(m.group(1)),))];return self.out(200,{'replies':a,'next_cursor':None})
   m=re.fullmatch(r'/api/channels/(\d+)/pins',path)
   if m:
    a=[] 
    for r in c.execute('SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? ORDER BY p.pinned_at DESC',(int(m.group(1)),)):a.append({'message':core.msg_obj(c,r),'pinned_by':r['pinned_by'],'pinned_at':r['pinned_at']})
    return self.out(200,{'pins':a,'next_cursor':None})
   if path=='/api/search':
    text=q.get('q',[''])[0];a=[core.msg_obj(c,r) for r in c.execute('SELECT * FROM messages WHERE body LIKE ? AND deleted=0 ORDER BY id DESC',(f'%{text}%',))];return self.out(200,{'results':a,'next_cursor':None})
   m=re.fullmatch(r'/api/users/(\d+)',path)
   if m:
    r=c.execute('SELECT * FROM users WHERE id=?',(int(m.group(1)),)).fetchone();return self.out(200,{'user':core.user_obj(r)}) if r else self.out(404,{'error':'not found'})
   m=re.fullmatch(r'/api/workspaces/([^/]+)/members',path)
   if m:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
    if not w:return self.out(404,{'error':'not found'})
    if not core.member(c,w['id'],u['id']):return self.out(403,{'error':'forbidden'})
    a=[dict(x) for x in c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],))]
    return self.out(200,{'members':a})
   m=re.fullmatch(r'/api/channels/(\d+)/members',path)
   if m:
    x=core.chan(c,int(m.group(1)))
    if not x:return self.out(404,{'error':'not found'})
    if not core.accessible(c,x,u['id']):return self.out(403,{'error':'forbidden'})
    a=[core.user_obj(c.execute('SELECT * FROM users WHERE id=?',(z['user_id'],)).fetchone()) for z in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(x['id'],))]
    return self.out(200,{'members':a})
   m=re.fullmatch(r'/api/channels/(\d+)/read',path)
   if m:
    cid=int(m.group(1)); x=core.chan(c,cid)
    if not x:return self.out(404,{'error':'not found'})
    if not core.accessible(c,x,u['id']):return self.out(403,{'error':'forbidden'})
    rr=c.execute('SELECT last_read_event_id FROM reads WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone(); last=rr[0] if rr else 0
    head=(c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(cid,)).fetchone() or [0])[0]
    return self.out(200,{'read_state':{'channel_id':cid,'last_read_event_id':last,'unread_count':max(0,head-last),'mention_count':0}})
   m=re.fullmatch(r'/api/workspaces/([^/]+)/groups',path)
   if m:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
    if not w:return self.out(404,{'error':'not found'})
    if not core.member(c,w['id'],u['id']):return self.out(403,{'error':'forbidden'})
    out=[]
    for z in c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)):
     out.append({'id':z['id'],'handle':z['handle'],'name':z['name'],'member_user_ids':json.loads(z['user_ids'])})
    return self.out(200,{'groups':out})
   m=re.fullmatch(r'/api/workspaces/([^/]+)/invitations',path)
   if m:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
    if not w:return self.out(404,{'error':'not found'})
    mm=core.member(c,w['id'],u['id'])
    if not mm or mm['role'] not in ('owner','admin'):return self.out(403,{'error':'forbidden'})
    a=[dict(x) for x in c.execute("SELECT code,workspace_id,expires_at,max_uses,uses,invited_user_id FROM invitations WHERE workspace_id=? AND revoked=0 AND uses<max_uses",(w['id'],))]
    return self.out(200,{'invitations':a})
   fm=re.fullmatch(r'/api/files/(\d+)(/download)?',path)
   if fm:
    r=c.execute('SELECT * FROM files WHERE id=?',(int(fm.group(1)),)).fetchone()
    if not r:return self.out(404,{'error':'not found'})
    if fm.group(2):
     b=r['data'];self.send_response(200);self.send_header('Content-Type',r['content_type']);self.send_header('Content-Disposition','attachment; filename="'+r['filename'].replace('"','')+'"');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
    return self.out(200,{'file':file_obj(r)})
   return self.out(404,{'error':'not found'})
  finally:c.close()
 def websocket(self,tok):
  u=core.token_user(tok)
  if not u:return self.send_error(401); return
  key=self.headers.get('Sec-WebSocket-Key','')
  if not key:return self.send_error(400); return
  accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
  self.send_response(101); self.send_header('Upgrade','websocket'); self.send_header('Connection','Upgrade'); self.send_header('Sec-WebSocket-Accept',accept); self.end_headers()
  item={'s':self.connection,'cid':None,'user':u['id']}
  conn_id=secrets.token_hex(12)
  pc=core.db();pc.execute('INSERT INTO presence VALUES(?,?,?)',(conn_id,u['id'],NODE));pc.commit();pc.close();item['presence']=conn_id
  with WS_LOCK: WS_CLIENTS.append(item)
  try:
   while True:
    h=self.connection.recv(2)
    if not h:break
    ln=h[1]&127; masked=h[1]&128
    if ln==126: ln=struct.unpack('>H',self.connection.recv(2))[0]
    elif ln==127: ln=struct.unpack('>Q',self.connection.recv(8))[0]
    mask=self.connection.recv(4) if masked else b''; data=self.connection.recv(ln)
    if masked:data=bytes(v^mask[i%4] for i,v in enumerate(data))
    if data:
     x=json.loads(data); cid=int(x.get('channel_id',0)); item['cid']=cid
     c=core.db(); head=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(cid,)).fetchone(); head=head[0] if head else 0
     if x.get('type')=='resume':
      since=int(x.get('since_event_id',0))
      for r in c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)):
       p=json.loads(r['payload_json']);p.update(type=r['type'],event_id=r['event_id'],channel_id=cid);sendws(self.connection,p)
      sendws(self.connection,{'type':'resumed','channel_id':cid,'head_event_id':head})
     else:sendws(self.connection,{'type':'subscribed','channel_id':cid,'head_event_id':head})
     c.close()
  except Exception:pass
  finally:
   pc=core.db();pc.execute('DELETE FROM presence WHERE conn_id=?',(item.get('presence'),));pc.commit();pc.close()
   with WS_LOCK:
    if item in WS_CLIENTS:WS_CLIENTS.remove(item)
 def do_POST(self):
  p=urllib.parse.urlparse(self.path); path=p.path; u=self.user()
  if path=='/api/files':
   if not u:return self.out(401,{'error':'unauthorized'})
   z,e=multipart_file(self)
   if e=='large':return self.out(413,{'error':'too large'})
   if not z:return self.out(400,{'error':'file required'})
   fn,ct,b=z;c=core.db()
   try:
    c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at,data) VALUES(?,?,?,?,?,?)',(u['id'],fn,ct,len(b),core.now(),b));c.commit();r=c.execute('SELECT * FROM files WHERE id=?',(c.execute('SELECT last_insert_rowid()').fetchone()[0],)).fetchone();return self.out(201,{'file':file_obj(r)})
   finally:c.close()
  d=jsonbody(self)
  if path.startswith('/api/workspaces/') and path.endswith('/groups'):
   if not u:return self.out(401,{'error':'unauthorized'})
   slug=path.split('/')[3]; w=candidate=None
   c0=core.db()
   try:
    w=c0.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
    if not w:return self.out(404,{'error':'not found'})
    if not core.member(c0,w['id'],u['id']):return self.out(403,{'error':'forbidden'})
    if not d or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,31}',d.get('handle','')) or not d.get('name'):return self.out(400,{'error':'invalid group'})
    ids=d.get('user_ids',[])
    if not isinstance(ids,list):return self.out(400,{'error':'user_ids'})
    try:c0.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id,user_ids) VALUES(?,?,?,?,?)',(w['id'],d['handle'],d['name'],u['id'],json.dumps(ids)));c0.commit()
    except sqlite3.IntegrityError:return self.out(409,{'error':'duplicate'})
    z=c0.execute('SELECT * FROM groups_ WHERE id=?',(c0.execute('SELECT last_insert_rowid()').fetchone()[0],)).fetchone()
    return self.out(201,{'group':{'id':z['id'],'handle':z['handle'],'name':z['name'],'member_user_ids':ids}})
   finally:c0.close()
  if path=='/api/auth/register':
   if not d:return self.out(400,{'error':'invalid body'})
   z,e=core.register(d.get('username'),d.get('password'),d.get('display_name',''))
   return self.out(201,{'user':{'id':z[0]['id'],'username':z[0]['username'],'display_name':z[0]['display_name'],'timezone':'UTC','avatar_url':'','status_text':'','status_emoji':''},'token':z[1]}) if z else self.out(409 if e=='dup' else 400,{'error':e})
  if path=='/api/auth/login':
   if not d:return self.out(400,{'error':'invalid body'})
   z=core.login(d.get('username'),d.get('password'));return self.out(200,{'user':z[0],'token':z[1]}) if z else self.out(401,{'error':'invalid credentials'})
  if not u:return self.out(401,{'error':'unauthorized'})
  c=core.db()
  try:
   if path=='/api/workspaces':
    if not d or not re.fullmatch(r'[a-z0-9-]{2,32}',d.get('slug','')):return self.out(400,{'error':'invalid workspace'})
    try:
     c.execute('BEGIN IMMEDIATE');c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d.get('name',''),u['id']));wid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'));c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));gid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO channel_members VALUES(?,?)',(gid,u['id']));c.execute('INSERT INTO channel_seq VALUES(?,0)',(gid,));c.commit();w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone();ch=core.chan(c,gid);return self.out(201,{'workspace':core.workspace_obj(w),'general_channel':core.channel_obj(ch)})
    except sqlite3.IntegrityError:c.rollback();return self.out(409,{'error':'duplicate'})
   m=re.fullmatch(r'/api/workspaces/([^/]+)/transfer_ownership',path)
   if m:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
    if not w:return self.out(404,{'error':'not found'})
    if w['owner_id']!=u['id']:return self.out(403,{'error':'forbidden'})
    target=int((d or {}).get('new_owner_id',0));tm=core.member(c,w['id'],target)
    if not tm or tm['role']!='admin':return self.out(400,{'error':'target'})
    c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],u['id']))
    c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],target))
    c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id']));c.commit()
    return self.out(200,{'workspace':core.workspace_obj(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
   m=re.fullmatch(r'/api/workspaces/([^/]+)/channels',path)
   if m:
    w=core.wsrow(m.group(1))
    if not w:return self.out(404,{'error':'not found'})
    mm=core.member(c,w['id'],u['id'])
    if not mm or mm['role']=='guest':return self.out(403,{'error':'forbidden'})
    if not d or not re.fullmatch(r'[a-z0-9-]{1,32}',d.get('name','')):return self.out(400,{'error':'invalid channel'})
    if len(d.get('topic',''))>250:return self.out(400,{'error':'topic'})
    if d.get('is_private') and mm['role'] not in ('owner','admin'):return self.out(403,{'error':'forbidden'})
    try:
     c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],d['name'],int(bool(d.get('is_private'))),d.get('topic','')));i=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO channel_members VALUES(?,?)',(i,u['id']));c.execute('INSERT INTO channel_seq VALUES(?,0)',(i,));c.commit();return self.out(201,{'channel':core.channel_obj(core.chan(c,i))})
    except sqlite3.IntegrityError:return self.out(409,{'error':'duplicate'})
   m=re.fullmatch(r'/api/channels/(\d+)/join',path)
   if m:
    x=core.chan(c,int(m.group(1)))
    if not x:return self.out(404,{'error':'not found'})
    w=c.execute('SELECT * FROM workspaces WHERE id=?',(x['workspace_id'],)).fetchone(); mm=core.member(c,w['id'],u['id'])
    if mm and mm['role']=='guest':return self.out(403,{'error':'forbidden'})
    if mm and mm['role']=='guest':return self.out(403,{'error':'forbidden'})
    if x['is_private'] and (not mm or not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(x['id'],u['id'])).fetchone()):return self.out(403,{'error':'forbidden'})
    if not mm:
     if w['join_mode']!='open' or x['is_private']:return self.out(403,{'error':'forbidden'})
     c.execute('INSERT INTO members VALUES(?,?,?)',(w['id'],u['id'],'member'))
    if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(x['id'],u['id'])).fetchone():c.execute('INSERT INTO channel_members VALUES(?,?)',(x['id'],u['id']))
    c.commit();return self.out(200,{'channel':core.channel_obj(x)})
   m=re.fullmatch(r'/api/channels/(\d+)/messages',path)
   if m:
    x=core.chan(c,int(m.group(1)))
    if not x:return self.out(404,{'error':'not found'})
    if not core.accessible(c,x,u['id']):return self.out(403,{'error':'forbidden'})
    body=str((d or {}).get('body',''))
    if body.startswith('/') and not body.startswith('//'):
     parts=body[1:].split(None,1); cmd=parts[0] if parts else ''; rest=parts[1] if len(parts)>1 else ''
     w=c.execute('SELECT * FROM workspaces WHERE id=?',(x['workspace_id'],)).fetchone()
     mm=core.member(c,x['workspace_id'],u['id'])
     if cmd=='me':
      if not rest.strip():return self.out(400,{'error':'text'})
     elif cmd=='shrug':
      body=(rest+' ' if rest else '')+'¯'+chr(92)+'_(ツ)_/¯'
     elif cmd=='topic':
      if not rest or len(rest)>250:return self.out(400,{'error':'topic'})
      if not w or w['owner_id']!=u['id']:return self.out(403,{'error':'forbidden'})
      c.execute('BEGIN IMMEDIATE');c.execute('UPDATE channels SET topic=? WHERE id=?',(rest,x['id']));co=core.channel_obj(core.chan(c,x['id']));core.add_event(c,x['id'],'channel.updated',{'channel':co});c.commit()
      return self.out(201,{'message':None,'channel':co})
     elif cmd=='invite':
      if not rest.startswith('@') or not rest[1:]:return self.out(400,{'error':'user'})
      if not w or not mm or mm['role'] not in ('owner','admin'):return self.out(403,{'error':'forbidden'})
      target=c.execute('SELECT * FROM users WHERE username=?',(rest[1:].strip(),)).fetchone()
      if not target:return self.out(404,{'error':'user'})
      if not core.member(c,x['workspace_id'],target['id']):return self.out(404,{'error':'user'})
      c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(x['id'],target['id']));c.commit()
      core.add_event(c,x['id'],'member.joined',{'user_id':target['id'],'channel_id':x['id']})
      return self.out(201,{'message':None,'invited_user':core.user_obj(target)})
     elif cmd in ('archive','unarchive'):
      if not w or w['owner_id']!=u['id']:return self.out(403,{'error':'forbidden'})
      val=1 if cmd=='archive' else 0;c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,x['id']));c.commit();x=core.chan(c,x['id']);co=core.channel_obj(x)
      return self.out(201,{'message':None,'channel':co})
     else:return self.out(400,{'error':'unknown command'})
    if x['is_archived']:return self.out(423,{'error':'archived'})
    if not d or not str(d.get('body','')).strip():return self.out(400,{'error':'empty'})
    d['body']=body
    fids=d.get('file_ids',[])
    if not isinstance(fids,list):return self.out(400,{'error':'file_ids'})
    for fid in fids:
     if not isinstance(fid,int):return self.out(400,{'error':'file_ids'})
     fr=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
     if not fr or fr['uploader_id']!=u['id'] or c.execute('SELECT 1 FROM message_files WHERE file_id=?',(fid,)).fetchone():return self.out(400,{'error':'file_ids'})
    c.execute('BEGIN IMMEDIATE');c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(x['id'],u['id'],d['body'],d.get('parent_id'),core.now()));i=c.execute('SELECT last_insert_rowid()').fetchone()[0];
    for fid in fids:c.execute('INSERT INTO message_files VALUES(?,?)',(i,fid))
    r=c.execute('SELECT * FROM messages WHERE id=?',(i,)).fetchone();mo=core.msg_obj(c,r);eid=core.add_event(c,x['id'],'message.reply' if d.get('parent_id') else 'message.created',{'message':mo,'message_id':i});c.commit();mo['event_id']=eid;return self.out(201,{'message':mo})
   m=re.fullmatch(r'/api/messages/(\d+)/reactions',path)
   if m:
    r=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(int(m.group(1)),)).fetchone()
    if not r:return self.out(404,{'error':'not found'})
    x=core.chan(c,r['channel_id'])
    if not core.accessible(c,x,u['id']):return self.out(403,{'error':'forbidden'})
    if x['is_archived']:return self.out(423,{'error':'archived'})
    if not d or not d.get('emoji'):return self.out(400,{'error':'emoji'})
    c.execute('BEGIN IMMEDIATE');c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(r['id'],u['id'],d['emoji']))
    changed=c.execute('SELECT changes()').fetchone()[0]
    mo=core.msg_obj(c,r)
    if changed: core.add_event(c,r['channel_id'],'reaction.added',{'message_id':r['id'],'emoji':d['emoji'],'user_id':u['id']})
    c.commit();rs=[]; 
    for z in c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)):rs.append({'emoji':z['emoji'],'count':z['n'],'user_ids':[int(q) for q in z['ids'].split(',')]})
    return self.out(200,{'reactions':rs})
   m=re.fullmatch(r'/api/workspaces/([^/]+)/invitations',path)
   if m:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
    if not w:return self.out(404,{'error':'not found'})
    mm=core.member(c,w['id'],u['id'])
    if not mm or mm['role'] not in ('owner','admin'):return self.out(403,{'error':'forbidden'})
    try: uses=int((d or {}).get('max_uses',1))
    except: return self.out(400,{'error':'max_uses'})
    if uses<1:return self.out(400,{'error':'max_uses'})
    target=(d or {}).get('invited_user_id')
    if not target and (d or {}).get('invited_username'):
     rr=c.execute('SELECT id FROM users WHERE username=?',(d['invited_username'],)).fetchone();target=rr['id'] if rr else None
    if target:uses=1
    code=core.secrets.token_urlsafe(12)
    c.execute('INSERT INTO invitations(code,workspace_id,creator_id,expires_at,max_uses,invited_user_id) VALUES(?,?,?,?,?,?)',(code,w['id'],u['id'],None,uses,target));c.commit()
    return self.out(201,{'invitation':{'code':code,'workspace_id':w['id'],'max_uses':uses,'uses':0,'invited_user_id':target}})
   m=re.fullmatch(r'/api/invitations/([^/]+)/accept',path)
   if m:
    inv=c.execute('SELECT * FROM invitations WHERE code=?',(m.group(1),)).fetchone()
    if not inv or inv['revoked'] or inv['uses']>=inv['max_uses']:return self.out(404,{'error':'not found'})
    if inv['invited_user_id'] and inv['invited_user_id']!=u['id']:return self.out(403,{'error':'targeted'})
    c.execute('BEGIN IMMEDIATE');c.execute('UPDATE invitations SET uses=uses+1 WHERE code=? AND uses<max_uses',(m.group(1),))
    if c.total_changes<1:c.rollback();return self.out(404,{'error':'used'})
    if not core.member(c,inv['workspace_id'],u['id']):c.execute('INSERT INTO members VALUES(?,?,?)',(inv['workspace_id'],u['id'],'member'))
    c.commit();return self.out(200,{'accepted':True})
   m=re.fullmatch(r'/api/channels/(\d+)/(archive|unarchive)',path)
   if m:
    x=core.chan(c,int(m.group(1)))
    if not x:return self.out(404,{'error':'not found'})
    w=c.execute('SELECT * FROM workspaces WHERE id=?',(x['workspace_id'],)).fetchone()
    if w['owner_id']!=u['id']:return self.out(403,{'error':'forbidden'})
    val=1 if m.group(2)=='archive' else 0;c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,x['id']));c.commit()
    return self.out(200,{'channel':core.channel_obj(core.chan(c,x['id']))})
   m=re.fullmatch(r'/api/channels/(\d+)/leave',path)
   if m:
    x=core.chan(c,int(m.group(1)))
    if not x:return self.out(404,{'error':'not found'})
    c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(x['id'],u['id']));c.commit();return self.out(200,{'channel':core.channel_obj(x)})
   m=re.fullmatch(r'/api/workspaces/([^/]+)/groups/([^/]+)',self.path)
   if m:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
    if not w:return self.out(404,{'error':'not found'})
    g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],m.group(2))).fetchone()
    if not g:return self.out(404,{'error':'not found'})
    mm=core.member(c,w['id'],u['id'])
    if g['creator_id']!=u['id'] and (not mm or mm['role']!='owner'):return self.out(403,{'error':'forbidden'})
    name=(d or {}).get('name',g['name']);ids=(d or {}).get('user_ids',json.loads(g['user_ids']))
    c.execute('UPDATE groups_ SET name=?,user_ids=? WHERE id=?',(name,json.dumps(ids),g['id']));c.commit()
    return self.out(200,{'group':{'id':g['id'],'handle':g['handle'],'name':name,'member_user_ids':ids}})
   m=re.fullmatch(r'/api/workspaces/([^/]+)',self.path)
   if m:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
    if not w:return self.out(404,{'error':'not found'})
    mm=core.member(c,w['id'],u['id'])
    if not mm or mm['role'] not in ('owner','admin'):return self.out(403,{'error':'forbidden'})
    if d and 'join_mode' in d and d['join_mode'] not in ('open','invite_only'):return self.out(400,{'error':'join_mode'})
    vals=[];args=[]
    for k in ('name','join_mode'):
     if d and k in d:vals.append(k+'=?');args.append(d[k])
    if vals:c.execute('UPDATE workspaces SET '+','.join(vals)+' WHERE id=?',(*args,w['id']));c.commit()
    return self.out(200,{'workspace':core.workspace_obj(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
   m=re.fullmatch(r'/api/workspaces/([^/]+)/transfer_ownership',self.path)
   if m:
    w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
    if not w:return self.out(404,{'error':'not found'})
    if w['owner_id']!=u['id']:return self.out(403,{'error':'forbidden'})
    target=int((d or {}).get('new_owner_id',0));tm=core.member(c,w['id'],target)
    if not tm:return self.out(400,{'error':'target'})
    if tm['role']!='admin':return self.out(400,{'error':'target'})
    c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],u['id']));c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],target));c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id']));c.commit();return self.out(200,{'workspace':core.workspace_obj(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
   m=re.fullmatch(r'/api/messages/(\d+)',self.path)
   if m:
    r=c.execute('SELECT * FROM messages WHERE id=?',(int(m.group(1)),)).fetchone()
    if not r:return self.out(404,{'error':'not found'})
    x=core.chan(c,r['channel_id']); w=c.execute('SELECT * FROM workspaces WHERE id=?',(x['workspace_id'],)).fetchone()
    if r['deleted']:return self.out(409,{'error':'deleted'})
    if x['is_archived']:return self.out(423,{'error':'archived'})
    if r['author_id']!=u['id']:return self.out(403,{'error':'forbidden'})
    if not d or not str(d.get('body','')).strip():return self.out(400,{'error':'empty'})
    c.execute('BEGIN IMMEDIATE');c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(d['body'],core.now(),r['id']))
    rr=c.execute('SELECT * FROM messages WHERE id=?',(r['id'],)).fetchone(); mo=core.msg_obj(c,rr);core.add_event(c,r['channel_id'],'message.updated',{'message':mo});c.commit()
    return self.out(200,{'message':mo})
   return self.out(404,{'error':'not found'})
  finally:c.close()
 def do_DELETE(self):
  u=self.user()
  if not u:return self.out(401,{'error':'unauthorized'})
  m=re.fullmatch(r'/api/messages/(\\d+)/pin',self.path)
  if m:
   c=core.db()
   try:
    r=c.execute('SELECT * FROM messages WHERE id=?',(int(m.group(1)),)).fetchone()
    if not r:return self.out(404,{'error':'not found'})
    if not core.accessible(c,core.chan(c,r['channel_id']),u['id']):return self.out(403,{'error':'forbidden'})
    c.execute('DELETE FROM pins WHERE message_id=?',(r['id'],));c.commit();return self.out(200,{'unpinned':True})
   finally:c.close()
  m=re.fullmatch(r'/api/workspaces/([^/]+)/invitations/([^/]+)',self.path)
  if m:
   c=core.db();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
   if not w:return self.out(404,{'error':'not found'})
   mm=core.member(c,w['id'],u['id'])
   if not mm or mm['role'] not in ('owner','admin'):return self.out(403,{'error':'forbidden'})
   c.execute('UPDATE invitations SET revoked=1 WHERE code=? AND workspace_id=?',(m.group(2),w['id']));c.commit();return self.out(200,{'deleted':True})
  u=self.user()
  if u:
   mr=re.fullmatch(r'/api/messages/(\d+)/reactions/(.+)',self.path)
   if mr:
    c=core.db()
    try:
     r=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(int(mr.group(1)),)).fetchone()
     if not r:return self.out(404,{'error':'not found'})
     x=core.chan(c,r['channel_id'])
     if not core.accessible(c,x,u['id']):return self.out(403,{'error':'forbidden'})
     emoji=urllib.parse.unquote(mr.group(2));c.execute('BEGIN IMMEDIATE');c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(r['id'],u['id'],emoji))
     core.add_event(c,r['channel_id'],'reaction.removed',{'message_id':r['id'],'emoji':emoji,'user_id':u['id']});c.commit();rs=[]
     for z in c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)):rs.append({'emoji':z['emoji'],'count':z['n'],'user_ids':[int(q) for q in z['ids'].split(',')]})
     return self.out(200,{'reactions':rs})
    finally:c.close()
  if not u:return self.out(401,{'error':'unauthorized'})
  m=re.fullmatch(r'/api/workspaces/([^/]+)/groups/([^/]+)',self.path)
  if m:
   c=core.db()
   w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
   if not w:return self.out(404,{'error':'not found'})
   g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],m.group(2))).fetchone()
   if g:
    mm=core.member(c,w['id'],u['id'])
    if g['creator_id']!=u['id'] and (not mm or mm['role']!='owner'):return self.out(403,{'error':'forbidden'})
    c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],));c.commit()
   return self.out(200,{'deleted':True})
  m=re.fullmatch(r'/api/messages/(\d+)',self.path);c=core.db()
  if m:
   r=c.execute('SELECT * FROM messages WHERE id=?',(int(m.group(1)),)).fetchone()
   if not r:return self.out(404,{'error':'not found'})
   x=core.chan(c,r['channel_id']);w=c.execute('SELECT * FROM workspaces WHERE id=?',(x['workspace_id'],)).fetchone()
   if x['is_archived']:return self.out(423,{'error':'archived'})
   if r['author_id']!=u['id'] and (not w or w['owner_id']!=u['id']):return self.out(403,{'error':'forbidden'})
   c.execute('BEGIN IMMEDIATE')
   if not r['deleted']:
    c.execute('UPDATE messages SET deleted=1 WHERE id=?',(r['id'],));core.add_event(c,r['channel_id'],'message.deleted',{'message_id':r['id']})
   c.commit();return self.out(200,{'deleted':True})
  return self.out(404,{'error':'not found'})
if __name__=='__main__':
 core.init(); ThreadingHTTPServer.daemon_threads=True
 ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
