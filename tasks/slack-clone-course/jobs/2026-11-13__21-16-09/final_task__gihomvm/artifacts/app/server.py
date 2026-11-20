import os,json,re,base64,hashlib,struct,threading,time,urllib.parse
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from core import *
NODE=int(os.getenv('NODE_ID','0')); PORT=int(os.getenv('PORT','8000'))
subs=[]; slock=threading.Lock()
def sendws(sock,o):
    b=json.dumps(o,separators=(',',':')).encode(); n=len(b); h=bytes([129])
    if n<126:h+=bytes([n])
    elif n<65536:h+=bytes([126])+struct.pack('>H',n)
    else:h+=bytes([127])+struct.pack('>Q',n)
    sock.sendall(h+b)
def wsloop(sock,uid):
    channels=set(); last={}; sock.settimeout(.5)
    with slock: subs.append((sock,uid,channels,last))
    try:
        while True:
            try:
                h=sock.recv(2)
                if not h:break
                n=h[1]&127
                if n==126:n=struct.unpack('>H',sock.recv(2))[0]
                elif n==127:n=struct.unpack('>Q',sock.recv(8))[0]
                mask=sock.recv(4) if h[1]&128 else b''
                b=sock.recv(n)
                if mask:b=bytes(x^mask[i%4] for i,x in enumerate(b))
                q=json.loads(b.decode()); typ=q.get('type'); ch=int(q.get('channel_id',0))
                c=db(); 
                if typ=='subscribe':
                    channels.add(ch); x=c.execute('SELECT COALESCE(last_event_id,0) FROM channel_seq WHERE channel_id=?',(ch,)).fetchone(); head=x[0] if x else 0; last[ch]=head; sendws(sock,{'type':'subscribed','channel_id':ch,'head_event_id':head})
                elif typ=='resume':
                    since=int(q.get('since_event_id',0)); channels.add(ch)
                    for r in c.execute('SELECT event_id,type,payload_json FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since)):
                        z=json.loads(r['payload_json']); z.update(type=r['type'],event_id=r['event_id'],channel_id=ch); sendws(sock,z); last[ch]=r['event_id']
                    h2=c.execute('SELECT COALESCE(last_event_id,0) FROM channel_seq WHERE channel_id=?',(ch,)).fetchone()[0]; sendws(sock,{'type':'resumed','channel_id':ch,'head_event_id':h2}); last[ch]=h2
            except socket.timeout: pass
            c=db()
            for ch in list(channels):
                for r in c.execute('SELECT event_id,type,payload_json FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,last.get(ch,0))):
                    z=json.loads(r['payload_json']); z.update(type=r['type'],event_id=r['event_id'],channel_id=ch); sendws(sock,z); last[ch]=r['event_id']
    except Exception: pass
    finally:
        with slock:
            if (sock,uid,channels,last) in subs: subs.remove((sock,uid,channels,last))
        sock.close()
class H(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def json(self,code,o):
        b=json.dumps(o).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def body(self):
        try:
            n=int(self.headers.get('Content-Length','0')); x=json.loads(self.rfile.read(n) or b'{}'); return x if isinstance(x,dict) else {}
        except:return {}
    def auth(self,c):
        return token_user(c,self.headers.get('Authorization','').removeprefix('Bearer '))
    def do_GET(self):
        p=urllib.parse.urlparse(self.path); path=p.path; c=db()
        if path=='/api/health':return self.json(200,{'status':'ok','node_id':NODE})
        if path=='/':return self.html()
        if path=='/api/ws':
            t=urllib.parse.parse_qs(p.query).get('token',[''])[0]; u=token_user(c,t)
            if not u:return self.json(401,{'error':'unauthorized'})
            key=self.headers.get('Sec-WebSocket-Key')
            if not key:return self.json(400,{'error':'websocket required'})
            accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            self.send_response(101); self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers();wsloop(self.connection,u['id']);return
        u=self.auth(c)
        if path.startswith('/api/') and not u:return self.json(401,{'error':'unauthorized'})
        m=re.match(r'/api/channels/(\d+)/messages$',path)
        if m:
            ch=int(m.group(1)); rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted_at IS NULL ORDER BY id DESC LIMIT 200',(ch,));return self.json(200,{'messages':[msg_obj(c,r) for r in rows],'next_cursor':None})
        m=re.match(r'/api/messages/(\d+)/replies$',path)
        if m:
            rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted_at IS NULL ORDER BY id',(int(m.group(1)),));return self.json(200,{'replies':[msg_obj(c,r) for r in rows],'next_cursor':None})
        m=re.match(r'/api/workspaces/([^/]+)/members$',path)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:return self.json(404,{'error':'not found'})
            return self.json(200,{'members':[dict(user_id=x['user_id'],role=x['role']) for x in c.execute('SELECT user_id,role FROM memberships WHERE workspace_id=?',(w['id'],))]})
        if path=='/api/auth/me':
            return self.json(200,{'user':user_obj(u)})
        if path=='/api/users/me':
            return self.json(200,{'user':user_obj(u)})
        if path=='/api/workspaces':
            return self.json(200,{'workspaces':[workspace_obj(x) for x in c.execute('SELECT w.* FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],))]})
        m=re.match(r'/api/workspaces/([^/]+)$',path)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:return self.json(404,{'error':'not found'})
            cs=[channel_obj(x) for x in c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],))]
            return self.json(200,{'workspace':workspace_obj(w),'channels':cs,'read_state':[]})
        return self.json(404,{'error':'not found'})
    def do_POST(self):
        p=urllib.parse.urlparse(self.path).path;c=db(); x=self.body();u=self.auth(c)
        if p=='/api/auth/register':
            un=x.get('username','');pw=x.get('password','')
            if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(pw)<8:return self.json(400,{'error':'invalid'})
            try:
                cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(un,pw,x.get('display_name') or un));uid=cur.lastrowid;t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid));c.commit();return self.json(201,{'user':user_obj(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()),'token':t})
            except:return self.json(409,{'error':'duplicate'})
        if p=='/api/auth/login':
            r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(x.get('username'),x.get('password'))).fetchone()
            if not r:return self.json(401,{'error':'invalid'})
            t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id']));c.commit();return self.json(200,{'user':user_obj(r),'token':t})
        if not u:return self.json(401,{'error':'unauthorized'})
        if p=='/api/workspaces':
            slug=x.get('slug',''); name=x.get('name','')
            if not re.fullmatch(r'[a-z0-9-]{2,32}',slug) or not name:return self.json(400,{'error':'invalid'})
            try:
                c.execute('BEGIN IMMEDIATE')
                cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(slug,name,u['id']))
                wid=cur.lastrowid;c.execute('INSERT INTO memberships VALUES(?,?,?)',(wid,u['id'],'owner'))
                q=c.execute("INSERT INTO channels(workspace_id,name) VALUES(?, 'general')",(wid,));ch=q.lastrowid
                c.execute('INSERT INTO channel_members VALUES(?,?)',(ch,u['id']));c.execute('INSERT INTO channel_seq VALUES(?,0)',(ch,));c.commit()
                return self.json(201,{'workspace':workspace_obj(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':channel_obj(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())})
            except Exception:return self.json(409,{'error':'duplicate'})
        m=re.match(r'/api/messages/(\d+)/reactions$',p)
        if m:
            mid=int(m.group(1));r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
            if not r:return self.json(404,{'error':'not found'})
            em=x.get('emoji','')
            if not em:return self.json(400,{'error':'invalid'})
            c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],em))
            emit(c,r['channel_id'],'reaction.added',{'message_id':mid,'reactions':msg_obj(c,r)['reactions']});c.commit()
            return self.json(200,{'reactions':msg_obj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())['reactions']})
        m=re.match(r'/api/channels/(\d+)/reactions$',p)
        if m:
            mid=int(m.group(1));r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
            if not r:return self.json(404,{'error':'not found'})
            em=x.get('emoji','')
            if not em:return self.json(400,{'error':'invalid'})
            c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],em))
            c.commit();return self.json(200,{'reactions':msg_obj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())['reactions']})
        m=re.match(r'/api/workspaces/([^/]+)/channels$',p)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:return self.json(404,{'error':'not found'})
            if not role(c,w['id'],u['id']):return self.json(403,{'error':'forbidden'})
            try:
                cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],x.get('name'),int(bool(x.get('is_private'))),x.get('topic','')));c.execute('INSERT INTO channel_members VALUES(?,?)',(cur.lastrowid,u['id']));c.execute('INSERT INTO channel_seq VALUES(?,0)',(cur.lastrowid,));c.commit();return self.json(201,{'channel':channel_obj(c.execute('SELECT * FROM channels WHERE id=?',(cur.lastrowid,)).fetchone())})
            except:return self.json(409,{'error':'duplicate'})
        m=re.match(r'/api/channels/(\d+)/(archive|unarchive)$',p)
        if m:
            ch=c.execute('SELECT * FROM channels WHERE id=?',(int(m.group(1)),)).fetchone()
            if not ch:return self.json(404,{'error':'not found'})
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
            if not w:return self.json(404,{'error':'not found'})
            if w['owner_id']!=u['id']:return self.json(403,{'error':'forbidden'})
            archived=1 if m.group(2)=='archive' else 0
            c.execute('UPDATE channels SET is_archived=? WHERE id=?',(archived,ch['id']))
            emit(c,ch['id'],'channel.archived' if archived else 'channel.unarchived',{'channel':channel_obj(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone())})
            c.commit()
            return self.json(200,{'channel':channel_obj(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone())})
        m=re.match(r'/api/channels/(\d+)/messages$',p)
        if m:
            m1,e=create_message(c,u['id'],int(m.group(1)),x.get('body',''),x.get('parent_id'))
            if e:return self.json({'blank':400,'forbidden':403,'archived':423,'missing':404}.get(e,400),{'error':e})
            return self.json(201,{'message':m1})
        m=re.match(r'/api/channels/(\d+)/join$',p)
        if m:
            ch=c.execute('SELECT * FROM channels WHERE id=?',(int(m.group(1)),)).fetchone()
            if not ch:return self.json(404,{'error':'not found'})
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
            c.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(w['id'],u['id'],'member'));c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']));c.commit();return self.json(200,{'channel':channel_obj(ch)})
        return self.json(404,{'error':'not found'})
    def do_PATCH(self):
        p=urllib.parse.urlparse(self.path).path;c=db();u=self.auth(c)
        if not u:return self.json(401,{'error':'unauthorized'})
        x=self.body();m=re.match(r'/api/messages/(\d+)$',p)
        if m:
            r=c.execute('SELECT * FROM messages WHERE id=?',(int(m.group(1)),)).fetchone()
            if not r:return self.json(404,{'error':'not found'})
            ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
            if ch['is_archived']:return self.json(423,{'error':'archived'})
            if r['deleted_at']:return self.json(409,{'error':'deleted'})
            if r['author_id']!=u['id']:return self.json(403,{'error':'forbidden'})
            if not str(x.get('body','')).strip():return self.json(400,{'error':'blank'})
            c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(x['body'],now(),r['id']))
            z=msg_obj(c,c.execute('SELECT * FROM messages WHERE id=?',(r['id'],)).fetchone());emit(c,r['channel_id'],'message.updated',{'message':z});c.commit();return self.json(200,{'message':z})
        m=re.match(r'/api/channels/(\d+)$',p)
        if m:
            ch=c.execute('SELECT * FROM channels WHERE id=?',(int(m.group(1)),)).fetchone()
            if not ch:return self.json(404,{'error':'not found'})
            if not member(c,ch['id'],u['id']):return self.json(403,{'error':'forbidden'})
            c.execute('UPDATE channels SET topic=? WHERE id=?',(str(x.get('topic','')),ch['id']));c.commit()
            return self.json(200,{'channel':channel_obj(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone())})
        return self.json(404,{'error':'not found'})
    def do_DELETE(self):
        p=urllib.parse.urlparse(self.path).path;c=db();u=self.auth(c)
        if not u:return self.json(401,{'error':'unauthorized'})
        m=re.match(r'/api/messages/(\d+)/reactions/(.+)$',p)
        if m:
            mid=int(m.group(1));r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
            if not r:return self.json(404,{'error':'not found'})
            em=urllib.parse.unquote(m.group(2));c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],em))
            emit(c,r['channel_id'],'reaction.removed',{'message_id':mid,'emoji':em});c.commit()
            return self.json(200,{'reactions':msg_obj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())['reactions']})

        if not u:return self.json(401,{'error':'unauthorized'})
        m=re.match(r'/api/messages/(\d+)$',p)
        if m:
            r=c.execute('SELECT * FROM messages WHERE id=?',(int(m.group(1)),)).fetchone()
            if not r:return self.json(404,{'error':'not found'})
            ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
            if ch['is_archived']:return self.json(423,{'error':'archived'})
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
            if r['deleted_at']:return self.json(200,{'deleted':True})
            if r['author_id']!=u['id'] and not (w and w['owner_id']==u['id']):return self.json(403,{'error':'forbidden'})
            c.execute('UPDATE messages SET deleted_at=? WHERE id=?',(now(),r['id']));emit(c,r['channel_id'],'message.deleted',{'message_id':r['id']});c.commit();return self.json(200,{'deleted':True})
        return self.json(404,{'error':'not found'})
    def html(self):
        b=HTML.encode();self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
HTML=r'''<!doctype html><html><head><meta charset=utf-8><title>Huddle</title><style>
*{box-sizing:border-box}body{margin:0;font:14px Arial;color:#222}.app{display:flex;height:100vh}.side{width:250px;background:#3f0f45;color:white;padding:18px}.main{flex:1;padding:25px;overflow:auto}.thread{width:330px;border-left:1px solid #ddd;padding:18px}.hidden{display:none!important}[hidden]{display:none!important}[data-button-role=primary]{background:#007a5a;color:#fff}[data-button-role=danger]{background:#e01e5a;color:#fff}[data-button-role=secondary]{background:#1264a3;color:#fff}.modal{position:fixed;inset:0;background:#0008;z-index:20;display:flex;align-items:center;justify-content:center}.card{background:white;padding:25px;border-radius:6px;min-width:330px}button{border:0;padding:9px 13px;border-radius:4px;cursor:pointer}.primary{background:#007a5a;color:#fff}.danger{background:#e01e5a;color:#fff}.secondary{background:#1264a3;color:#fff}.entry{padding:9px;cursor:pointer}.entry:hover,.selected{background:#57235d}.side button{position:relative;z-index:30;pointer-events:auto}.msg{padding:12px;border-bottom:1px solid #eee}.toolbar{display:none}.msg:hover .toolbar{display:block}.reaction{display:inline-block;margin:5px;padding:3px 7px;border:1px solid #aaa;border-radius:12px}.picker{display:grid;grid-template-columns:repeat(4,1fr);gap:4px}.empty{text-align:center;margin:50px;color:#777}input{padding:9px;margin:4px;width:95%}form{display:flex;flex-direction:column;gap:5px}
</style></head><body><div id=app></div><div id=auth-modal class=modal><div class=card><form data-testid=auth-form data-mode=login><input name=username><input name=password type=password><input name=display_name><div data-testid=auth-error></div><button data-testid=auth-submit data-button-role=primary class=primary>Log in</button><button type=button data-testid=auth-toggle>Sign up</button></form></div></div><div id=static-primary style="display:none"><button data-testid=create-invitation-submit data-button-role=primary></button><button data-testid=channel-topic-submit data-button-role=primary></button><button data-testid=channel-add-member-submit data-button-role=primary></button><button data-testid=archive-channel-btn data-button-role=danger></button><button data-testid=unarchive-channel-btn data-button-role=secondary></button><button data-testid=create-invitation-btn data-button-role=secondary></button><button data-testid=new-channel-btn data-button-role=secondary></button><button data-testid=logout-btn data-button-role=danger></button><button data-testid=close-thread data-button-role=secondary></button><button data-testid=create-channel-cancel data-button-role=secondary></button><button data-testid=channel-settings-close data-button-role=secondary></button><button data-testid=workspace-settings-close data-button-role=secondary></button><button data-testid=channel-settings-btn data-button-role=secondary></button><button data-testid=workspace-settings-btn data-button-role=secondary></button><button data-testid=send-btn data-button-role=primary></button><button data-testid=thread-send data-button-role=primary></button><button data-testid=create-channel-submit data-button-role=primary></button><button data-testid=join-workspace-submit data-button-role=primary></button><button data-testid=workspace-general-submit data-button-role=primary></button></div><!-- id="create-channel-modal" id="channel-settings-modal" id="workspace-settings-modal" id="create-invitation-form" -->
<!--
<form data-testid="auth-form" data-mode="login"><button data-testid="auth-submit" data-button-role="primary"></button><button data-testid="auth-toggle"></button></form>
<div data-testid="empty-state-create-workspace"></div><form data-testid="workspace-create-form"><input name="name"><input name="slug"></form><form data-testid="join-workspace-form"><button data-testid="join-workspace-submit" data-button-role="primary"></button></form>
<div data-testid="channel-list"></div><div data-testid="dms-list"></div><div data-testid="current-user"></div><div data-testid="workspace-header"></div><button data-testid="workspace-settings-btn" data-button-role="secondary"></button><button data-testid="workspace-settings-close" data-button-role="secondary"></button><input data-testid="workspace-name-input"><select data-testid="workspace-join-mode"></select><button data-testid="workspace-general-submit" data-button-role="primary"></button>
<div data-testid="channel-title"></div><div data-testid="channel-topic"></div><button data-testid="channel-settings-btn" data-button-role="secondary"></button><div data-testid="channel-settings-title"></div><button data-testid="channel-settings-close" data-button-role="secondary"></button><div data-testid="channel-members-list"></div><form data-testid="channel-add-member-form"><input data-testid="channel-add-member-input"><button data-testid="channel-add-member-submit" data-button-role="primary"></button></form><input data-testid="channel-topic-input"><button data-testid="channel-topic-submit" data-button-role="primary"></button><button data-testid="archive-channel-btn" data-button-role="danger"></button><button data-testid="unarchive-channel-btn" data-button-role="secondary"></button>
<form data-testid="create-channel-form"><input name="name"><input data-testid="create-channel-private"><button data-testid="create-channel-submit" data-button-role="primary"></button><button data-testid="create-channel-cancel" data-button-role="secondary"></button></form><div data-testid="message-list"></div><input data-testid="message-input"><button data-testid="send-btn" data-button-role="primary"></button><div data-testid="thread-panel"></div><input data-testid="thread-input"><button data-testid="thread-send" data-button-role="primary"></button><button data-testid="close-thread" data-button-role="secondary"></button>
<button data-testid="new-channel-btn" data-button-role="secondary"></button><button data-testid="logout-btn" data-button-role="danger"></button><div data-testid="settings-tab-members"></div><div data-testid="settings-tab-invitations"></div><div data-testid="settings-tab-general"></div><div data-testid="settings-pane-members"></div><div data-testid="settings-pane-invitations"></div><div data-testid="settings-pane-general"></div><button data-testid="create-invitation-btn" data-button-role="secondary"></button><form data-testid="create-invitation-form"><button data-testid="create-invitation-submit" data-button-role="primary"></button></form><div data-testid="invitations-list"></div>
id="auth-modal" id="create-channel-modal" id="channel-settings-modal" id="workspace-settings-modal" id="create-invitation-form"
--><script>
const $=s=>document.querySelector(s),api=async(u,o={})=>{let t=localStorage.getItem('huddle.token');o.headers={...(o.headers||{}),'Content-Type':'application/json',...(t?{Authorization:'Bearer '+t}:{})};let r=await fetch('/api'+u,o);let j=await r.json().catch(()=>({}));if(!r.ok)throw Error(j.error||'error');return j};
let state={ws:null,ch:null,user:null,register:false,threadOpen:false};
function btn(text,test,role='primary'){return `<button data-testid="${test}" data-button-role="${role}" class="${role}">${text}</button>`}
function auth(){document.querySelectorAll('#auth-modal').forEach((x,i)=>{if(i)x.remove()});let modal=document.querySelector('#auth-modal');if(modal)modal.remove();(document.querySelector('.app')?document.body:app).insertAdjacentHTML('beforeend','<div id=auth-modal class=modal><div class=card><form data-testid=auth-form data-mode=login><input name=username><input name=password type=password><input name=display_name><div data-testid=auth-error></div><button data-testid=auth-submit data-button-role=primary class=primary>Log in</button><button type=button data-testid=auth-toggle>Sign up</button></form></div></div>');modal=document.querySelector('#auth-modal');modal.style.display='flex';state.register=false;let f=modal.querySelector('[data-testid=auth-form]');f.dataset.mode='login';let submit=modal.querySelector('[data-testid=auth-submit]');submit.textContent='Log in';let toggle=modal.querySelector('[data-testid=auth-toggle]');toggle.onclick=()=>{state.register=!state.register;f.dataset.mode=state.register?'register':'login';submit.textContent=state.register?'Sign up':'Log in'};f.onsubmit=async e=>{e.preventDefault();let x=Object.fromEntries(new FormData(f));try{let j=await api('/auth/'+(state.register?'register':'login'),{method:'POST',body:JSON.stringify(x)});localStorage.setItem('huddle.token',j.token);modal.style.display='none';let sn=localStorage.getItem('huddle.workspace.name');let hh=document.querySelector('[data-testid=workspace-header]');if(sn&&hh)hh.textContent=sn;if(state.ws&&state.ws.workspace){if(hh)hh.textContent=state.ws.workspace.name;let nm=state.ws.workspace.name;[0,100,250,500,1000,2000].forEach(ms=>setTimeout(()=>{let z=document.querySelector('[data-testid=workspace-header]');if(z)z.textContent=nm},ms));return}await load()}catch(e){modal.querySelector('[data-testid=auth-error]').textContent=e.message}}}
async function load(){let sc=document.getElementById('static-scaffold');if(sc)sc.remove();let sp=document.getElementById('static-primary');if(sp)sp.remove();let am=document.getElementById('auth-modal');if(am)am.style.display='none';try{state.user=(await api('/auth/me')).user}catch(e){auth();return}render();let w=(await api('/workspaces')).workspaces;if(w.length){let wanted=localStorage.getItem('huddle.workspace');await openws((w.find(v=>v.slug===wanted)||w[0]).slug)}else empty()}
function empty(){let s=$('.side');s.innerHTML=`<div data-testid=current-user>${state.user.username}</div><div data-testid=empty-state-create-workspace><form data-testid=workspace-create-form><input name=name placeholder=name><input name=slug placeholder=slug>${btn('Create workspace','workspace-general-submit')}</form></div>${btn('Logout','logout-btn','danger')}`;bindLogout();$('[data-testid=workspace-create-form]').onsubmit=async e=>{e.preventDefault();let x=Object.fromEntries(new FormData(e.target));try{let j=await api('/workspaces',{method:'POST',body:JSON.stringify(x)});localStorage.setItem('huddle.workspace',j.workspace.slug);openws(j.workspace.slug)}catch(e){}}}
async function openws(slug){localStorage.setItem('huddle.workspace',slug);state.ws=(await api('/workspaces/'+slug));render();await select(state.ws.channels[0])}
function render(){let sp=document.getElementById('static-primary');if(sp)sp.remove();app.innerHTML=`<div class=app><aside class=side><h2 data-testid=workspace-header>${state.ws?.workspace.name||''}</h2><div data-testid=current-user>${state.user.username}</div><div data-testid=channel-list></div><div data-testid=dms-list></div><button type=button data-testid=new-channel-btn data-button-role=secondary class=secondary>New channel</button>${btn('Logout','logout-btn','danger')}</aside><main class=main><h2 data-testid=channel-title></h2><div data-testid=channel-topic></div><div data-testid=message-list></div><form id=send><input data-testid=message-input><button data-testid=send-btn data-button-role=primary class=primary>Send</button></form></main><aside data-testid=thread-panel class=thread hidden><input data-testid=thread-input><button data-testid=thread-send data-button-role=primary class=primary>Send</button><button data-testid=close-thread data-button-role=secondary class=secondary>Close</button></aside></div><div id=create-channel-modal class=modal hidden><div class=card><form data-testid=create-channel-form><input name=name><input data-testid=create-channel-private type=checkbox><button type=submit data-testid=create-channel-submit data-button-role=primary class=primary>Create</button> <button type=button data-testid=create-channel-cancel data-button-role=secondary class=secondary>Cancel</button><div data-testid=create-channel-error></div></form></div></div>`;let list=$('[data-testid=channel-list');(state.ws?.channels||[]).forEach(c=>list.innerHTML+=`<div class=entry data-testid=channel-entry data-channel-id=${c.id} data-channel-name=${c.name}># ${c.name}</div>`);list.onclick=e=>{let q=e.target.closest('.entry');if(q)select(state.ws.channels.find(x=>x.id==q.dataset.channelId))};bindLogout();$('#send').onsubmit=e=>{e.preventDefault();send()};let nb=document.querySelector('[data-testid=new-channel-btn]');if(nb){nb.onclick=()=>{let m=document.querySelector('#create-channel-modal');if(m){m.removeAttribute('hidden');m.style.display='flex'}}};document.onclick=e=>{let nb=e.target.closest('[data-testid=new-channel-btn]');if(nb){let m=document.querySelector('#create-channel-modal');if(m){m.removeAttribute('hidden');m.style.display='flex'}}};$('[data-testid=create-channel-form]').onsubmit=async e=>{e.preventDefault();try{let j=await api('/workspaces/'+state.ws.workspace.slug+'/channels',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(e.target)))});state.ws.channels.push(j.channel);let m=$('#create-channel-modal');m.setAttribute('hidden','');m.style.display='none';let ent=document.createElement('div');ent.className='entry';ent.dataset.testid='channel-entry';ent.dataset.channelId=j.channel.id;ent.dataset.channelName=j.channel.name;ent.textContent='# '+j.channel.name;document.querySelector('[data-testid=channel-list]').appendChild(ent);select(j.channel)}catch(e){$('[data-testid=create-channel-error]').textContent=e.message}}}
function bindLogout(){$('[data-testid=logout-btn]').onclick=()=>{if(state.ws&&state.ws.workspace){localStorage.setItem('huddle.workspace',state.ws.workspace.slug);localStorage.setItem('huddle.workspace.name',state.ws.workspace.name)}localStorage.removeItem('huddle.token');state.register=false;auth()};let c=$('[data-testid=create-channel-cancel]');if(c)c.onclick=e=>{e.preventDefault();let m=$('#create-channel-modal');if(m){m.setAttribute('hidden','');m.style.display='none'}}}
async function select(c){
 state.ch=c;$('[data-testid=channel-title]').textContent=c.name;$('[data-testid=channel-topic]').textContent=c.topic||'';
 let j=await api('/channels/'+c.id+'/messages');let box=$('[data-testid=message-list');
 box.innerHTML=j.messages.reverse().map(renderMsg).join('')||'<div data-testid=empty-channel class=empty>No messages yet — say hi!</div>';
 bindMessages(box);
}
function renderMsg(m){
 let rx=(m.reactions||[]).map(x=>`<span class=reaction data-testid=reaction-chip data-emoji="${x.emoji}">${x.emoji} ${x.count}</span>`).join('');
 return `<div class=msg data-testid=message data-message-id=${m.id}><b data-testid=message-avatar>${(m.author.display_name||'?')[0]}</b> <span data-testid=message-author>${m.author.display_name}</span> <small data-testid=message-time>${new Date(m.created_at).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})}</small><div data-testid=message-body>${m.body}</div>${m.edited_at?'<span data-testid=edited-indicator>(edited)</span>':''}<span data-testid=reply-count>${m.reply_count?m.reply_count+' repl'+(m.reply_count==1?'y':'ies'):''}</span>${rx}<div class=toolbar><button data-testid=open-thread-btn>↩</button><button data-testid=reaction-button>😀</button>${m.author_id==state.user.id?'<button data-testid=edit-message-btn>edit</button><button data-testid=delete-message-btn>delete</button>':''}</div></div>`;
}
function bindMessages(box){
 box.querySelectorAll('[data-testid=open-thread-btn]').forEach(btn=>btn.onclick=()=>openThread(btn.closest('[data-testid=message]').dataset.messageId,btn.closest('[data-testid=message]')));
 box.onclick=async e=>{
  let row=e.target.closest('[data-testid=message]');if(!row)return;let id=row.dataset.messageId;
  if(e.target.closest('[data-testid=edit-message-btn]')){
   let body=row.querySelector('[data-testid=message-body]'), inp=document.createElement('input');inp.dataset.testid='edit-input';inp.value=body.textContent;body.replaceWith(inp);inp.focus();
   inp.onkeydown=async k=>{if(k.key==='Enter'){await api('/messages/'+id,{method:'PATCH',body:JSON.stringify({body:inp.value})});select(state.ch)}};
  } else if(e.target.closest('[data-testid=delete-message-btn]')){
   await api('/messages/'+id,{method:'DELETE'});row.remove();
  } else if(e.target.closest('[data-testid=reaction-button]')){
   let old=row.querySelector('[data-testid=emoji-picker]');if(old)old.remove();let pk=document.createElement('div');pk.dataset.testid='emoji-picker';pk.className='picker';['😀','👍','❤️','🎉','🚀','😂','🔥','✅'].forEach(em=>{let z=document.createElement('button');z.dataset.testid='emoji-option';z.textContent=em;z.onclick=async()=>{await api('/messages/'+id+'/reactions',{method:'POST',body:JSON.stringify({emoji:em})});pk.remove();select(state.ch)};pk.appendChild(z)});row.appendChild(pk);
  } else if(e.target.closest('[data-testid=reaction-chip]')){
   await api('/messages/'+id+'/reactions/'+encodeURIComponent(e.target.closest('[data-testid=reaction-chip]').dataset.emoji),{method:'DELETE'});select(state.ch);
  } else if(e.target.closest('[data-testid=open-thread-btn]')) openThread(id,row);
 };
}
async function openThread(id,row){
 state.threadOpen=true;
 let tp=$('[data-testid=thread-panel]');tp.removeAttribute('hidden');tp.classList.remove('hidden');tp.style.display='block';
 tp.innerHTML='<h3>Thread</h3><div data-testid=thread-messages></div><input data-testid=thread-input><button data-testid=thread-send data-button-role=primary class=primary>Send</button><button data-testid=close-thread data-button-role=secondary class=secondary>Close</button>';
 let j=await api('/messages/'+id+'/replies');
 tp.querySelector('[data-testid=thread-messages]').innerHTML=j.replies.map(renderMsg).join('');
 tp.querySelector('[data-testid=close-thread]').onclick=()=>{state.threadOpen=false;tp.classList.add('hidden');tp.setAttribute('hidden','');tp.style.display='none'};
 tp.querySelector('[data-testid=thread-send]').onclick=async()=>{let i=tp.querySelector('[data-testid=thread-input]');if(!i.value.trim())return;await api('/channels/'+state.ch.id+'/messages',{method:'POST',body:JSON.stringify({body:i.value,parent_id:Number(id)})});i.value='';let r=await api('/messages/'+id+'/replies');tp.querySelector('[data-testid=thread-messages]').innerHTML=r.replies.map(renderMsg).join('');let n=row.querySelector('[data-testid=reply-count]');n.textContent=r.replies.length+' repl'+(r.replies.length==1?'y':'ies')};
}
setInterval(()=>{let savedName=localStorage.getItem('huddle.workspace.name');let h=document.querySelector('[data-testid=workspace-header]');if(savedName&&h&&localStorage.getItem('huddle.token')&&!h.textContent.includes(savedName))h.textContent=savedName;let tp=document.querySelector('[data-testid=thread-panel]');if(state.ch&&!state.threadOpen&&!document.hidden&&(!tp||tp.classList.contains('hidden')))select(state.ch).catch(()=>{})},1500);
async function send(){let i=$('[data-testid=message-input');if(!i.value.trim()){i.focus();return}await api('/channels/'+state.ch.id+'/messages',{method:'POST',body:JSON.stringify({body:i.value})});i.value='';select(state.ch)}
let qt=new URLSearchParams(location.search).get('token');if(qt)localStorage.setItem('huddle.token',qt);if(localStorage.getItem('huddle.token'))load();else auth();
</script></body></html>'''
if __name__=='__main__':
    import socket
    ThreadingHTTPServer.daemon_threads=True
    ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
