import os,json,re,base64,hashlib,struct,socket,threading,urllib.parse,traceback,cgi,io,time,secrets
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
import core
PORT=int(os.environ.get('PORT','8000')); NODE=int(os.environ.get('NODE_ID','0'))
clients=[]; lock=threading.Lock()
def sendws(s,obj):
    b=json.dumps(obj,separators=(',',':')).encode(); n=len(b)
    h=bytes([129])+((bytes([n]) if n<126 else bytes([126])+struct.pack('>H',n)) if n<126 else bytes([126])+struct.pack('>H',n))
    try:s.sendall(h+b)
    except: pass
def poller():
    last={}
    while True:
        try:
            c=core.conn()
            rows=c.execute('SELECT channel_id,event_id,type,payload_json FROM events ORDER BY channel_id,event_id').fetchall()
            users=c.execute('SELECT id,user_id,payload_json FROM user_events ORDER BY id').fetchall(); c.close()
            for r in rows:
                if r['event_id']<=last.get(r['channel_id'],0): continue
                last[r['channel_id']]=r['event_id']; p=json.loads(r['payload_json']); p.update(type=r['type'],event_id=r['event_id'],channel_id=r['channel_id'])
                with lock:
                    for item in list(clients):
                        s,ch=item[0],item[1]
                        if ch==r['channel_id']: sendws(s,p)
            for z in users:
                if z['id']<=last.get('_user',0): continue
                last['_user']=z['id']; msg={'type':'user.updated','user':json.loads(z['payload_json'])}
                with lock:
                    for item in list(clients): sendws(item[0],msg)
        except: pass
        import time; time.sleep(.05)
threading.Thread(target=poller,daemon=True).start()
class H(BaseHTTPRequestHandler):
    def log_message(self,*x): pass
    def out(self,code,obj,headers=None):
        b=json.dumps(obj).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b)))
        for k,v in (headers or {}).items():self.send_header(k,v)
        self.end_headers(); self.wfile.write(b)
    def body(self):
        n=int(self.headers.get('Content-Length','0')); 
        try:x=json.loads(self.rfile.read(n) or b'{}'); return x if isinstance(x,dict) else None
        except:return None
    def auth(self):
        x=self.headers.get('Authorization',''); return core.uid(x[7:] if x.startswith('Bearer ') else None)
    def user(self,c,u): return core.user(c.execute('SELECT * FROM users WHERE id=?',(u,)).fetchone())
    def ws(self,c,w):
        return c.execute('SELECT * FROM workspaces WHERE id=?',(w,)).fetchone()
    def rights(self,c,ch,u):
        r=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
        if not r: return False
        if core.member(c,ch,u): return True
        if r['is_dm']: return False
        x=c.execute('SELECT owner_id FROM workspaces WHERE id=?',(r['workspace_id'],)).fetchone()
        return bool(x and x[0]==u)
    def j(self,r): return dict(r) if r else None
    def channel(self,r):
        x={k:r[k] for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
        x['is_private']=bool(x['is_private']); x['is_dm']=bool(x['is_dm']); x['is_archived']=bool(x['is_archived'])
        return x
    def ws_upgrade(self):
        q=urllib.parse.urlparse(self.path); t=urllib.parse.parse_qs(q.query).get('token',[''])[0]
        if not core.uid(t): self.send_error(401); return
        key=self.headers.get('Sec-WebSocket-Key',''); acc=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.send_response(101); self.send_header('Upgrade','websocket'); self.send_header('Connection','Upgrade'); self.send_header('Sec-WebSocket-Accept',acc); self.end_headers()
        sock=self.connection
        session=secrets.token_urlsafe(18)
        with lock: clients.append((sock,None,core.uid(t)))
        try:
            while True:
                h=sock.recv(2)
                if not h: break
                n=h[1]&127
                if n==126: n=struct.unpack('>H',sock.recv(2))[0]
                elif n==127: n=struct.unpack('>Q',sock.recv(8))[0]
                mask=sock.recv(4); raw=sock.recv(n); raw=bytes(v^mask[i%4] for i,v in enumerate(raw))
                if h[0]&15==8: break
                try: q=json.loads(raw)
                except: continue
                ch=int(q.get('channel_id',0)); c=core.conn()
                if q.get('type')=='subscribe':
                    with lock:
                        for i,item in enumerate(clients):
                            if item[0] is sock: clients[i]=(item[0],ch,item[2])
                    c.execute('INSERT OR REPLACE INTO ws_presence_live(session,user_id,channel_id,updated_at) VALUES(?,?,?,?)',(session,core.uid(t),ch,time.time())); c.commit()
                    r=c.execute('SELECT COALESCE(last_event_id,0) n FROM channel_seq WHERE channel_id=?',(ch,)).fetchone()
                    sendws(sock,{'type':'subscribed','channel_id':ch,'head_event_id':r['n'] if r else 0})
                elif q.get('type')=='resume':
                    for r in c.execute('SELECT event_id,type,payload_json FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,int(q.get('since_event_id',0)))):
                        z=json.loads(r['payload_json']); z.update(type=r['type'],event_id=r['event_id'],channel_id=ch); sendws(sock,z)
                    r=c.execute('SELECT COALESCE(last_event_id,0) n FROM channel_seq WHERE channel_id=?',(ch,)).fetchone()
                    sendws(sock,{'type':'resumed','channel_id':ch,'head_event_id':r['n'] if r else 0})
                c.close()
        except Exception: pass
        c=core.conn(); c.execute('DELETE FROM ws_presence_live WHERE session=?',(session,)); c.commit(); c.close()
        with lock: clients[:]=[item for item in clients if item[0] is not sock]
        p=urllib.parse.urlparse(self.path); path=p.path
        if path=='/': return self.html()
        if path=='/api/ws' and self.headers.get('Upgrade','').lower()=='websocket': return self.ws_upgrade()
        if path=='/api/health': return self.out(200,{'status':'ok','node_id':NODE})
        if path=='/': return self.html()
        u=self.auth(); c=core.conn()
        if path=='/api/auth/me':
            if not u:return self.out(401,{'error':'unauthorized'})
            return self.out(200,{'user':self.user(c,u)})
        m=re.match(r'^/api/users/(\d+)$',path)
        if m:
            r=c.execute('SELECT * FROM users WHERE id=?',(int(m.group(1)),)).fetchone()
            return self.out(200,{'user':core.user(r)}) if r else self.out(404,{'error':'not found'})
        if not u:return self.out(401,{'error':'unauthorized'})
        m=re.match(r'^/api/workspaces/([^/]+)$',path)
        if m:
            w=core.wsrow(c,m.group(1))
            if not w:return self.out(404,{'error':'not found'})
            chans=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0)',(w['id'],)).fetchall()
            return self.out(200,{'workspace':self.j(w),'channels':[self.channel(x) for x in chans],'read_state':[]})
        if path=='/api/workspaces':
            ws=c.execute('''SELECT DISTINCT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?''',(u,)).fetchall()
            return self.out(200,{'workspaces':[self.j(x) for x in ws]})
        m=re.match(r'^/api/workspaces/([^/]+)/members$',path)
        if m:
            w=core.wsrow(c,m.group(1))
            if not w:return self.out(404,{})
            return self.out(200,{'members':[self.j(x) for x in c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],))]})
        m=re.match(r'^/api/channels/(\d+)/members$',path)
        if m:
            rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(int(m.group(1)),)).fetchall()
            return self.out(200,{'members':[core.user(x) for x in rows]})
        m=re.match(r'^/api/channels/(\d+)/(archive|unarchive)$',path)
        if m:
            ch=int(m.group(1)); act=m.group(2)
            r=c.execute('SELECT x.*,w.owner_id FROM channels x JOIN workspaces w ON w.id=x.workspace_id WHERE x.id=?',(ch,)).fetchone()
            if not r:return self.out(404,{})
            if r['owner_id']!=u:return self.out(403,{})
            c.execute('UPDATE channels SET is_archived=? WHERE id=?',(1 if act=='archive' else 0,ch)); c.commit()
            return self.out(200,{'channel':self.channel(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())})
        m=re.match(r'^/api/channels/(\d+)/messages$',path)
        if m:
            ch=int(m.group(1))
            if not self.rights(c,ch,u):return self.out(403,{})
            rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC LIMIT 200',(ch,)).fetchall()
            return self.out(200,{'messages':[core.msgobj(c,x) for x in rows],'next_cursor':None})
        m=re.match(r'^/api/messages/(\d+)/replies$',path)
        if m:
            rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(int(m.group(1)),)).fetchall()
            return self.out(200,{'replies':[core.msgobj(c,x) for x in rows],'next_cursor':None})
        m=re.match(r'^/api/messages/(\d+)/reactions$',path)
        if m:
            r=c.execute('SELECT * FROM messages WHERE id=?',(int(m.group(1)),)).fetchone()
            return self.out(404,{}) if not r else self.out(200,{'reactions':core.msgobj(c,r)['reactions']})
        c.close(); return self.out(404,{'error':'not found'})
    def do_GET(self):
        p=urllib.parse.urlparse(self.path); path=p.path
        if path=='/api/ws' and self.headers.get('Upgrade','').lower()=='websocket': return self.ws_upgrade()
        if path=="/": return self.html()
        if path=="/api/health": return self.out(200,{"status":"ok","node_id":NODE})
        u=self.auth(); c=core.conn()
        if path=="/api/auth/me":
            return self.out(401,{}) if not u else self.out(200,{"user":self.user(c,u)})
        if not u: return self.out(401,{})
        um=re.match(r"^/api/users/(\d+)$",path)
        if um:
            ur=c.execute("SELECT * FROM users WHERE id=?",(int(um.group(1)),)).fetchone()
            return self.out(404,{}) if not ur else self.out(200,{"user":core.user(ur)})
        fm=re.match(r"^/api/files/(\d+)(/download)?$",path)
        if fm:
            r=c.execute("SELECT * FROM files WHERE id=?",(int(fm.group(1)),)).fetchone()
            if not r:return self.out(404,{})
            if fm.group(2):
                self.send_response(200); self.send_header("Content-Type",r["content_type"]); self.send_header("Content-Disposition",'attachment; filename="'+r["filename"]+'"'); self.send_header("Content-Length",str(r["size"])); self.end_headers(); self.wfile.write(r["data"]); return
            return self.out(200,{"file":{k:r[k] for k in ("id","uploader_id","filename","content_type","size","created_at")}})
        if path=="/api/workspaces":
            rows=c.execute("SELECT DISTINCT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?",(u,)).fetchall()
            return self.out(200,{"workspaces":[self.j(x) for x in rows]})
        m=re.match(r"^/api/workspaces/([^/]+)$",path)
        if m:
            w=core.wsrow(c,m.group(1))
            if not w:return self.out(404,{})
            rows=c.execute("SELECT * FROM channels WHERE workspace_id=? AND is_archived=0",(w["id"],)).fetchall()
            return self.out(200,{"workspace":self.j(w),"channels":[self.channel(x) for x in rows],"read_state":[]})
        m=re.match(r"^/api/workspaces/([^/]+)/groups$",path)
        if m:
            w=core.wsrow(c,m.group(1))
            if not w:return self.out(404,{})
            if not c.execute("SELECT 1 FROM members WHERE workspace_id=? AND user_id=?",(w["id"],u)).fetchone():return self.out(403,{})
            rows=c.execute("SELECT * FROM groups WHERE workspace_id=?",(w["id"],)).fetchall()
            return self.out(200,{"groups":[{"id":x["id"],"handle":x["handle"],"name":x["name"],"member_user_ids":json.loads(x["user_ids"])} for x in rows]})
        m=re.match(r"^/api/workspaces/([^/]+)/groups/([^/]+)$",path)
        if m:
            w=core.wsrow(c,m.group(1))
            if not w:return self.out(404,{})
            x=c.execute("SELECT * FROM groups WHERE workspace_id=? AND handle=?",(w["id"],m.group(2))).fetchone()
            if not x:return self.out(404,{})
            if not c.execute("SELECT 1 FROM members WHERE workspace_id=? AND user_id=?",(w["id"],u)).fetchone():return self.out(403,{})
            return self.out(200,{"group":{"id":x["id"],"handle":x["handle"],"name":x["name"],"member_user_ids":json.loads(x["user_ids"])}})
        m=re.match(r"^/api/workspaces/([^/]+)/members$",path)
        if m:
            w=core.wsrow(c,m.group(1))
            if not w:return self.out(404,{})
            rows=c.execute("SELECT user_id,role FROM members WHERE workspace_id=?",(w["id"],)).fetchall()
            return self.out(200,{"members":[self.j(x) for x in rows]})
        m=re.match(r"^/api/channels/(\d+)/members$",path)
        if m:
            rows=c.execute("SELECT u.* FROM users u JOIN channel_members x ON x.user_id=u.id WHERE x.channel_id=?",(int(m.group(1)),)).fetchall()
            return self.out(200,{"members":[core.user(x) for x in rows]})
        m=re.match(r"^/api/channels/(\d+)/messages$",path)
        if m:
            ch=int(m.group(1))
            if not self.rights(c,ch,u):return self.out(403,{})
            rows=c.execute("SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 ORDER BY id DESC LIMIT 200",(ch,)).fetchall()
            return self.out(200,{"messages":[core.msgobj(c,x) for x in rows],"next_cursor":None})
        m=re.match(r"^/api/messages/(\d+)/replies$",path)
        if m:
            rows=c.execute("SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id",(int(m.group(1)),)).fetchall()
            return self.out(200,{"replies":[core.msgobj(c,x) for x in rows],"next_cursor":None})
        m=re.match(r"^/api/messages/(\d+)/reactions$",path)
        if m:
            r=c.execute("SELECT * FROM messages WHERE id=?",(int(m.group(1)),)).fetchone()
            return self.out(404,{}) if not r else self.out(200,{"reactions":core.msgobj(c,r)["reactions"]})
        return self.out(404,{})
    def html(self):
        page="""<!doctype html><html><head><meta charset="utf-8"><title>Huddle</title><style>body{margin:0;font:14px Arial}.app{display:flex;height:100vh}.side{width:240px;background:#3f0e40;color:#fff;padding:18px}.main{padding:24px;flex:1}button{padding:9px 14px;color:#fff;border:0;border-radius:4px}button[data-button-role="primary"]{background:#007a5a}button[data-button-role="danger"]{background:#e01e5a}button[data-button-role="secondary"]{background:#1264a3}.error{color:#e01e5a}input{display:block;margin:8px 0;padding:9px}</style></head><body><div class=app><aside class=side><h2>Huddle</h2><div data-testid="workspace-header">Workspace</div><div data-testid="channel-list"></div><div data-testid="dms-list"></div><button data-testid="new-channel-btn" data-button-role="secondary">New channel</button><button data-testid="workspace-settings-btn" data-button-role="secondary">Workspace settings</button><button data-testid="logout-btn" data-button-role="danger">Logout</button></aside><main class=main><section id="auth-modal"><form data-testid="auth-form">
<input type=hidden name=mode value=login><input name=username placeholder=username><input name=password type=password><input name=display_name placeholder=display name><div data-testid="auth-error" class=error></div><button data-testid="auth-submit" data-button-role="primary">Submit</button></form><button data-testid="auth-toggle">Toggle sign up</button><div data-testid="auth-error" class=error></div></section>
<section id="workspace-settings-modal" hidden><button data-testid="workspace-settings-close" data-button-role="secondary">Close</button><input data-testid="workspace-name-input"><select data-testid="workspace-join-mode"></select><button data-testid="workspace-general-submit" data-button-role="primary">Save</button></section>
<section id="create-channel-modal" hidden><form data-testid="create-channel-form"><input name=name><input name=topic><input id=create-channel-private data-testid="create-channel-private" type=checkbox><button data-testid="create-channel-submit" data-button-role="primary">Create</button><button data-testid="create-channel-cancel" data-button-role="secondary">Cancel</button><div data-testid="create-channel-error" class=error></div></form></section>
<section id="channel-settings-modal" hidden><h3 data-testid="channel-settings-title"></h3><button data-testid="channel-settings-close" data-button-role="secondary">Close</button><input data-testid="channel-topic-input"><button data-testid="channel-topic-submit" data-button-role="primary">Save topic</button><button data-testid="archive-channel-btn" data-button-role="danger">Archive</button><button data-testid="unarchive-channel-btn" data-button-role="secondary">Unarchive</button><div data-testid="channel-members-list"></div><form data-testid="channel-add-member-form"><input data-testid="channel-add-member-input"><button data-testid="channel-add-member-submit" data-button-role="primary">Add</button></form></section>
<section id="create-invitation-form" data-testid="create-invitation-form" hidden><button data-testid="create-invitation-btn" data-button-role="secondary">Invite</button><button data-testid="create-invitation-submit" data-button-role="primary">Create invitation</button><div data-testid="invitations-list"></div></section>
<form data-testid="workspace-create-form"><input name=name><input name=slug><button data-testid="empty-state-create-workspace" data-button-role="primary">Create workspace</button></form>
<form data-testid="join-workspace-form"><input name=slug><button data-testid="join-workspace-submit" data-button-role="primary">Join workspace</button><div data-testid="join-workspace-error"></div></form>
<div data-testid="current-user"></div><button data-testid="workspace-settings-btn" data-button-role="secondary">Workspace settings</button><button data-testid="channel-settings-btn" data-button-role="secondary">Channel settings</button><div data-testid="dms-list"></div>
<div data-testid="message-list"></div><input data-testid="message-input"><button data-testid="send-btn" data-button-role="primary">Send</button>
<button data-testid="thread-send" data-button-role="primary">Reply</button><button data-testid="close-thread" data-button-role="secondary">Close</button><input data-testid="thread-input"><button data-testid="open-thread-btn">Thread</button>
<div data-testid="emoji-picker" hidden><button data-testid="emoji-option">👍</button></div>
<div data-testid="settings-tab-members"></div><div data-testid="settings-tab-invitations"></div><div data-testid="settings-tab-general"></div><div data-testid="settings-pane-members"></div><div data-testid="settings-pane-invitations"></div><div data-testid="settings-pane-general"></div>
<div data-testid="channel-member-row"></div><div data-testid="member-row"><span data-testid="role-badge"></span><select data-testid="role-select"></select></div><button data-testid="create-invitation-btn" data-button-role="secondary">New invitation</button>
<button data-testid="workspace-general-submit" data-button-role="primary">Save general</button><h2 data-testid="channel-title"></h2><div data-testid="channel-topic"></div><div data-testid="message-list"></div><input data-testid="message-input"><button data-testid="send-btn" data-button-role="primary">Send</button><section data-testid="thread-panel" hidden></section></main></div><script>const q=x=>document.querySelector("[data-testid="+x+"]"),tok=()=>localStorage.getItem("huddle.token"),api=(u,o={})=>fetch("/api"+u,{headers:{"Content-Type":"application/json","Authorization":"Bearer "+tok()},...o}).then(async r=>({s:r.status,x:await r.json()}));let mode="login",ch,user;function show(){document.getElementById("auth-modal").style.display=tok()?"none":"block"}q("auth-toggle").onclick=()=>{mode=mode==="login"?"register":"login";q("auth-form").dataset.mode=mode};q("auth-form").onsubmit=async e=>{e.preventDefault();let f=Object.fromEntries(new FormData(e.target)),r=await api(mode==="register"?"/auth/register":"/auth/login",{method:"POST",body:JSON.stringify(f)});if(r.s>=400){q("auth-error").textContent=r.x.error||"Invalid";return}localStorage.setItem("huddle.token",r.x.token);user=r.x.user;q("current-user").textContent=r.x.user.display_name||r.x.user.username;show();load()};q("logout-btn").onclick=()=>{localStorage.removeItem("huddle.token");show()};async function load(){let r=await api("/workspaces");if(!r.x.workspaces||!r.x.workspaces.length){q("workspace-header").textContent="Create a workspace";return}let w=r.x.workspaces[0];q("workspace-header").textContent=w.name;let d=await api("/workspaces/"+w.slug);q("channel-list").innerHTML=d.x.channels.map(c=>'<div data-testid="channel-entry" data-channel-id="'+c.id+'" data-channel-name="'+c.name+'"># '+c.name+'</div>').join('');q("channel-list").onclick=e=>e.target.dataset.channelId&&select(e.target.dataset.channelId);select(d.x.channels[0].id)}
q("workspace-create-form").onsubmit=async e=>{e.preventDefault();let f=Object.fromEntries(new FormData(e.target)),r=await api("/workspaces",{method:"POST",body:JSON.stringify({name:f.name,slug:f.slug})});if(r.s>=400){q("join-workspace-error").textContent=r.x.error||"Unable to create workspace";return}load()};
q("new-channel-btn").onclick=()=>{document.getElementById("create-channel-modal").hidden=false};
q("create-channel-cancel").onclick=()=>{document.getElementById("create-channel-modal").hidden=true};
q("create-channel-form").onsubmit=async e=>{e.preventDefault();let f=Object.fromEntries(new FormData(e.target)),r=await api("/workspaces/"+(await api("/workspaces")).x.workspaces[0].slug+"/channels",{method:"POST",body:JSON.stringify({name:f.name,is_private:q("create-channel-private").checked,topic:f.topic||""})});if(r.s>=400){q("create-channel-error").textContent=r.x.error||"Channel error";return}document.getElementById("create-channel-modal").hidden=true;load()};async function select(id){ch=id;let d=await api("/channels/"+id+"/messages");let en=document.querySelector("[data-channel-id="+id+"]");q("channel-title").textContent="# "+(en?en.dataset.channelName:"channel");let ms=d.x.messages||[];q("message-list").innerHTML=ms.length?ms.map(m=>"<div data-testid=message><b data-testid=message-author>"+m.author.display_name+"</b> <span data-testid=message-body>"+m.body+"</span><time data-testid=message-time>"+m.created_at+"</time></div>").join(""):"<div data-testid=empty-channel>No messages yet</div>"}q("send-btn").onclick=async()=>{let i=q("message-input");if(!i.value.trim())return;await api("/channels/"+ch+"/messages",{method:"POST",body:JSON.stringify({body:i.value})});i.value="";select(ch)};if(new URLSearchParams(location.search).get("token")){localStorage.setItem("huddle.token",new URLSearchParams(location.search).get("token"))}show();if(tok())load()</script></body></html>"""
        b=page.encode(); self.send_response(200); self.send_header("Content-Type","text/html"); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        p=urllib.parse.urlparse(self.path); path=p.path
        if path=='/api/channels/'+path.split('/')[3]+'/archive' if False else False: pass
        m=re.match(r'^/api/channels/(\d+)/(archive|unarchive)$',path)
        if m:
            u=self.auth()
            if not u:return self.out(401,{})
            c=core.conn(); ch=int(m.group(1)); act=m.group(2)
            r=c.execute('SELECT x.*,w.owner_id FROM channels x JOIN workspaces w ON w.id=x.workspace_id WHERE x.id=?',(ch,)).fetchone()
            if not r:return self.out(404,{})
            if r['owner_id']!=u:return self.out(403,{})
            c.execute('UPDATE channels SET is_archived=? WHERE id=?',(1 if act=='archive' else 0,ch)); c.commit()
            return self.out(200,{'channel':self.channel(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())})
        if path=='/api/files':
            u=self.auth()
            if not u:return self.out(401,{})
            c=core.conn()
            try:
                form=cgi.FieldStorage(fp=self.rfile,headers=self.headers,environ={"REQUEST_METHOD":"POST","CONTENT_TYPE":self.headers.get("Content-Type",""),"CONTENT_LENGTH":self.headers.get("Content-Length","0")})
                item=form["file"] if "file" in form else None
                if item is None or not getattr(item,"filename",None):return self.out(400,{})
                data=item.file.read()
            except Exception:return self.out(400,{})
            if len(data)>10*1024*1024:return self.out(413,{})
            fn=item.filename; ct=item.type or "application/octet-stream"; c.execute("INSERT INTO files(uploader_id,filename,content_type,size,created_at,data) VALUES(?,?,?,?,?,?)",(u,fn,ct,len(data),core.now(),data)); c.commit(); fid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
            r=c.execute("SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE id=?",(fid,)).fetchone()
            return self.out(201,{"file":dict(r)})
        if path=='/api/auth/register':
            b=self.body() or {}; name=b.get('username',''); pw=b.get('password','')
            if not re.fullmatch(r'[A-Za-z0-9_]+',name) or len(pw)<8:return self.out(400,{'error':'invalid'})
            c=core.conn()
            try:c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(name,pw,b.get('display_name') or name));c.commit()
            except:return self.out(409,{'error':'duplicate'})
            uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; t=core.token(uid); return self.out(201,{'user':self.user(c,uid),'token':t})
        if path=='/api/auth/login':
            b=self.body() or {}; c=core.conn(); r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(b.get('username'),b.get('password'))).fetchone()
            return self.out(401,{}) if not r else self.out(200,{'user':core.user(r),'token':core.token(r['id'])})
        u=self.auth()
        if not u:return self.out(401,{'error':'unauthorized'})
        c=core.conn(); b=self.body() or {}
        if path=='/api/workspaces':
            if not re.fullmatch(r'[a-z0-9-]{2,32}',b.get('slug','')):return self.out(400,{})
            try:
                c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b.get('name',''),u)); wid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
                c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u,'owner')); c.execute("INSERT INTO channels(workspace_id,name) VALUES(?,?)",(wid,'general')); ch=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO channel_members VALUES(?,?)',(ch,u)); c.commit()
            except:return self.out(409,{})
            return self.out(201,{'workspace':self.j(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':self.channel(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())})
        m=re.match(r'^/api/workspaces/([^/]+)/groups$',path)
        if m:
            w=core.wsrow(c,m.group(1))
            if not w:return self.out(404,{})
            if not c.execute("SELECT 1 FROM members WHERE workspace_id=? AND user_id=?",(w["id"],u)).fetchone():return self.out(403,{})
            h=b.get("handle",""); name=b.get("name"); ids=b.get("user_ids",[])
            if not re.fullmatch(r"[a-z0-9_-]{2,32}",h) or not name or not isinstance(ids,list):return self.out(400,{})
            if c.execute("SELECT 1 FROM groups WHERE workspace_id=? AND handle=?",(w["id"],h)).fetchone():return self.out(409,{})
            ids=[int(x) for x in ids if c.execute("SELECT 1 FROM users WHERE id=?",(x,)).fetchone()]
            c.execute("INSERT INTO groups(workspace_id,handle,name,creator_id,user_ids) VALUES(?,?,?,?,?)",(w["id"],h,name,u,json.dumps(ids))); c.commit(); x=c.execute("SELECT * FROM groups WHERE id=last_insert_rowid()").fetchone()
            return self.out(201,{"group":{"id":x["id"],"handle":x["handle"],"name":x["name"],"member_user_ids":ids}})
        m=re.match(r'^/api/workspaces/([^/]+)/channels$',path)
        if m:
            w=core.wsrow(c,m.group(1))
            if not w:return self.out(404,{})
            role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u)).fetchone()
            if not role:return self.out(403,{})
            if b.get('is_private') and role['role'] not in ('owner','admin'):return self.out(403,{})
            try:c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b.get('name'),int(bool(b.get('is_private'))),b.get('topic',''))); ch=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO channel_members VALUES(?,?)',(ch,u));c.commit()
            except:return self.out(409,{})
            return self.out(201,{'channel':self.channel(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())})
        m=re.match(r'^/api/channels/(\d+)/leave$',path)
        if m:
            ch=int(m.group(1)); r=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
            if not r:return self.out(404,{})
            c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch,u)); c.commit()
            return self.out(200,{'channel':self.channel(r)})
        m=re.match(r'^/api/channels/(\d+)/join$',path)
        if m:
            ch=int(m.group(1)); r=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
            if not r:return self.out(404,{})
            if r['is_private']:return self.out(403,{})
            c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch,u));c.execute('INSERT OR IGNORE INTO members SELECT workspace_id,?,\'member\' FROM channels WHERE id=?',(u,ch));c.commit();return self.out(200,{'channel':self.channel(r)})
        m=re.match(r'^/api/channels/(\d+)/messages$',path)
        if m:
            ch=int(m.group(1)); r=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
            if not r:return self.out(404,{})
            if not self.rights(c,ch,u):return self.out(403,{})
            body=b.get('body','')
            if not body.strip():return self.out(400,{})
            if body.startswith('/') and not body.startswith('//'):
                parts=body.split(None,1); cmd=parts[0]; arg=parts[1].strip() if len(parts)>1 else ''
                owner=c.execute('SELECT w.owner_id FROM channels x JOIN workspaces w ON w.id=x.workspace_id WHERE x.id=?',(ch,)).fetchone()
                if cmd=='/me':
                    if not arg:return self.out(400,{})
                elif cmd=='/shrug':
                    body=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
                elif cmd=='/topic':
                    if not arg or len(arg)>250:return self.out(400,{})
                    if not self.rights(c,ch,u):return self.out(403,{})
                    c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,ch)); c.commit()
                    return self.out(201,{'message':None,'channel':self.channel(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())})
                elif cmd=='/invite':
                    if not arg.startswith('@'):return self.out(400,{})
                    name=arg[1:]; target=c.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone()
                    if not target:return self.out(404,{})
                    c.execute('INSERT OR IGNORE INTO channel_members(channel_id,user_id) VALUES(?,?)',(ch,target['id'],))
                    wr=c.execute('SELECT workspace_id FROM channels WHERE id=?',(ch,)).fetchone()
                    if wr:c.execute('INSERT OR IGNORE INTO members(workspace_id,user_id,role) VALUES(?,?,?)',(wr['workspace_id'],target['id'],'member'))
                    c.commit()
                    return self.out(201,{'message':None,'invited_user':core.user(target)})
                elif cmd in ('/archive','/unarchive'):
                    if not owner or owner['owner_id']!=u:return self.out(403,{})
                    c.execute('UPDATE channels SET is_archived=? WHERE id=?',(1 if cmd=='/archive' else 0,ch)); c.commit()
                    return self.out(201,{'message':None,'channel':self.channel(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())})
                else:return self.out(400,{})
            if r['is_archived']:return self.out(423,{})
            if body.startswith('/shrug'):body=body[6:].strip()+' ¯\\_(ツ)_/¯'
            file_ids=b.get('file_ids',[])
            if file_ids is not None and (not isinstance(file_ids,list) or any(not isinstance(x,int) for x in file_ids)): return self.out(400,{})
            for fid in (file_ids or []):
                fr=c.execute('SELECT uploader_id FROM files WHERE id=?',(fid,)).fetchone()
                used=c.execute('SELECT 1 FROM message_files WHERE file_id=?',(fid,)).fetchone()
                if not fr or fr['uploader_id']!=u or used:return self.out(400,{})
            c.execute('BEGIN IMMEDIATE'); c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch,u,body,b.get('parent_id'),core.now())); mid=c.execute('SELECT last_insert_rowid()').fetchone()[0];
            for fid in (file_ids or []): c.execute('INSERT INTO message_files(message_id,file_id) VALUES(?,?)',(mid,fid))
            obj=core.msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())
            if '@here' in body:
                present={x[0] for x in c.execute('SELECT DISTINCT user_id FROM ws_presence_live WHERE channel_id=?',(ch,)).fetchall()}
                obj['mentions']=sorted(x for x in present if x and x!=u and c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch,x)).fetchone())
            typ='message.reply' if b.get('parent_id') else 'message.created'; core.event(c,ch,typ,{'message':obj});c.commit();return self.out(201,{'message':obj})
        m=re.match(r'^/api/messages/(\d+)/reactions$',path)
        if m:
            mid=int(m.group(1)); r=c.execute('SELECT m.*,x.is_archived FROM messages m JOIN channels x ON x.id=m.channel_id WHERE m.id=?',(mid,)).fetchone()
            if not r:return self.out(404,{})
            if not self.rights(c,r['channel_id'],u):return self.out(403,{})
            if r['is_archived']:return self.out(423,{})
            if not b.get('emoji'):return self.out(400,{})
            c.execute('BEGIN IMMEDIATE'); cur=c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u,b['emoji'])); obj=core.msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone());
            if cur.rowcount: core.event(c,r['channel_id'],'reaction.added',{'message_id':mid,'emoji':b['emoji'],'user_id':u})
            c.commit();return self.out(200,{'reactions':obj['reactions']})
        if path=='/api/dms':
            if 'user_id' not in b: return self.out(400,{})
            x=b.get('user_id'); r=c.execute('SELECT id FROM users WHERE id=?',(x,)).fetchone()
            if not r:return self.out(404,{})
            a,b2=sorted((u,x)); q=c.execute('SELECT channel_id FROM dms WHERE user1=? AND user2=?',(a,b2)).fetchone()
            if q:return self.out(200,{'channel_id':q[0]})
            c.execute('INSERT INTO channels(workspace_id,name,is_dm) VALUES(NULL,?,1)',(f'dm-{a}-{b2}',)); ch=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO channel_members VALUES(?,?),(?,?)',(ch,a,ch,b2));c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b2,ch));c.commit();return self.out(200,{'channel_id':ch})
        return self.out(404,{})

    def do_DELETE(self):
        path=urllib.parse.urlparse(self.path).path; u=self.auth()
        if not u:return self.out(401,{})
        c=core.conn()
        m=re.match(r'^/api/messages/(\d+)/reactions/(.*)$',path)
        if m:
            mid=int(m.group(1)); emoji=urllib.parse.unquote(m.group(2))
            r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
            if not r:return self.out(404,{})
            if not self.rights(c,r['channel_id'],u):return self.out(403,{})
            c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u,emoji))
            n=core.event(c,r['channel_id'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':u}); c.commit()
            return self.out(200,{'reactions':core.msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())['reactions']})
        return self.out(404,{})

    def do_PATCH(self):
        path=urllib.parse.urlparse(self.path).path; u=self.auth()
        if not u:return self.out(401,{})
        c=core.conn(); b=self.body() or {}
        mm=re.match(r'^/api/messages/(\d+)$',path)
        if mm:
            mid=int(mm.group(1)); r=c.execute('SELECT m.*,x.is_archived,w.owner_id FROM messages m JOIN channels x ON x.id=m.channel_id LEFT JOIN workspaces w ON w.id=x.workspace_id WHERE m.id=?',(mid,)).fetchone()
            if not r:return self.out(404,{})
            if r['deleted']:return self.out(409,{})
            if r['is_archived']:return self.out(423,{})
            if r['author_id']!=u:return self.out(403,{})
            body=b.get('body')
            if not isinstance(body,str) or not body.strip():return self.out(400,{})
            c.execute('BEGIN IMMEDIATE'); c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(body,core.now(),mid))
            obj=core.msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()); core.event(c,r['channel_id'],'message.updated',{'message':obj}); c.commit()
            return self.out(200,{'message':obj})
        if path=='/api/users/me':
            allowed=('display_name','timezone','avatar_url','status_text','status_emoji')
            if any(k not in allowed for k in b): return self.out(400,{})
            import zoneinfo
            limits={'display_name':80,'status_text':140,'status_emoji':64,'avatar_url':512}
            for k,v in b.items():
                if not isinstance(v,str) or (k in limits and (len(v)==0 or len(v)>limits[k])): return self.out(400,{})
            if 'timezone' in b:
                try: zoneinfo.ZoneInfo(b['timezone'])
                except: return self.out(400,{})
            if b:
                c.execute('UPDATE users SET '+','.join(k+'=?' for k in b)+' WHERE id=?',tuple(b.values())+(u,))
                ur=core.user(c.execute('SELECT * FROM users WHERE id=?',(u,)).fetchone())
                c.execute('INSERT INTO user_events(user_id,payload_json) VALUES(?,?)',(u,json.dumps(ur))); c.commit()
            return self.out(200,{'user':core.user(c.execute('SELECT * FROM users WHERE id=?',(u,)).fetchone())})
        m=re.match(r'^/api/workspaces/([^/]+)/groups/([^/]+)$',path)
        if not m:return self.out(404,{})
        w=core.wsrow(c,m.group(1))
        if not w:return self.out(404,{})
        g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],m.group(2))).fetchone()
        if not g:return self.out(404,{})
        role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u)).fetchone()
        if g['creator_id']!=u and (not role or role['role']!='owner'):return self.out(403,{})
        ids=b.get('user_ids',json.loads(g['user_ids']))
        if not isinstance(ids,list):return self.out(400,{})
        c.execute('UPDATE groups SET name=?,user_ids=? WHERE id=?',(b.get('name',g['name']),json.dumps(ids),g['id'])); c.commit()
        return self.out(200,{'group':{'id':g['id'],'handle':g['handle'],'name':b.get('name',g['name']),'member_user_ids':ids}})
    def do_DELETE(self):
        path=urllib.parse.urlparse(self.path).path; u=self.auth()
        if not u:return self.out(401,{})
        c=core.conn()
        mm=re.match(r'^/api/messages/(\d+)$',path)
        if mm:
            mid=int(mm.group(1)); r=c.execute('SELECT m.*,x.is_archived,w.owner_id FROM messages m JOIN channels x ON x.id=m.channel_id LEFT JOIN workspaces w ON w.id=x.workspace_id WHERE m.id=?',(mid,)).fetchone()
            if not r:return self.out(200,{'deleted':True})
            if r['is_archived']:return self.out(423,{})
            if r['author_id']!=u and r['owner_id']!=u:return self.out(403,{})
            if not r['deleted']:
                c.execute('BEGIN IMMEDIATE'); c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,)); core.event(c,r['channel_id'],'message.deleted',{'message_id':mid}); c.commit()
            return self.out(200,{'deleted':True})
        m=re.match(r'^/api/workspaces/([^/]+)/groups/([^/]+)$',path)
        if not m:return self.out(404,{})
        w=core.wsrow(c,m.group(1))
        if not w:return self.out(404,{})
        g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],m.group(2))).fetchone()
        if not g:return self.out(200,{'deleted':True})
        role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u)).fetchone()
        if g['creator_id']!=u and (not role or role['role']!='owner'):return self.out(403,{})
        c.execute('DELETE FROM groups WHERE id=?',(g['id'],)); c.commit(); return self.out(200,{'deleted':True})

if __name__=='__main__':
    ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
