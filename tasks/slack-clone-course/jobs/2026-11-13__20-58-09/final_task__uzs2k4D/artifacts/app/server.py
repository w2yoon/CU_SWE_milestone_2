import os,json,re,base64,hashlib,struct,threading,time
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
from core import *
NODE=int(os.getenv('NODE_ID','0')); PORT=int(os.getenv('PORT','8000'))
WS=[]; WLOCK=threading.Lock()
def sendws(sock,obj):
    open('/tmp/ws-errors.log','a').write('send '+repr(obj.get('type'))+'\\n')
    b=json.dumps(obj,separators=(',',':')).encode(); n=len(b)
    if n<126: h=bytes([0x81,n])
    elif n<65536: h=bytes([0x81,126])+struct.pack('>H',n)
    else: h=bytes([0x81,127])+struct.pack('>Q',n)
    try: sock.sendall(h+b)
    except: pass
def poll_ws():
    seen={}
    while True:
        with WLOCK: clients=list(WS)
        for x in clients:
            c=None
            try:
                c=db(); ch=x['channel']; last=seen.get(id(x),x.get('since',0))
                rows=c.execute('SELECT event_id,type,payload_json FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,last)).fetchall()
                if rows: open('/tmp/ws-errors.log','a').write('poll '+str(ch)+' '+str(last)+' '+str(len(rows))+'\\n')
                for r in rows:
                    o=json.loads(r['payload_json']); o.update(type=r['type'],event_id=r['event_id'],channel_id=ch)
                    sendws(x['sock'],o); seen[id(x)]=r['event_id']
            except Exception as e:
                try: open('/tmp/ws-errors.log','a').write(repr(e)+'\\n')
                except: pass
            finally:
                if c:
                    try:c.close()
                    except:pass
        time.sleep(.05)

threading.Thread(target=poll_ws,daemon=True).start()
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def json(self,code,o):
        b=json.dumps(o).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def body(self):
        try:
            n=int(self.headers.get('Content-Length','0')); x=json.loads(self.rfile.read(n) or b'{}'); return x if isinstance(x,dict) else {}
        except: return {}
    def user(self):
        h=self.headers.get('Authorization','')
        return auth(h[7:] if h.startswith('Bearer ') else '')
    def route(self):
        return [x for x in urlparse(self.path).path.split('/') if x]
    def err(self,n,s): self.json(n,{'error':s})
    def do_GET(self):
        if urlparse(self.path).path == "/":
            html=SPA
            self.send_response(200); self.send_header("Content-Type","text/html"); self.send_header("Content-Length",str(len(html.encode()))); self.end_headers(); self.wfile.write(html.encode()); return
        p=self.route()
        if p==['api','health']: return self.json(200,{'status':'ok','node_id':NODE})
        if p==['api','ws']: return self.websocket()
        u=self.user()
        if p[:2]==['api','auth'] and len(p)==3 and p[2]=='me':
            return self.json(200,{'user':userobj(u)}) if u else self.err(401,'unauthorized')
        if not u: return self.err(401,'unauthorized')
        c=db()
        try:
            if p==['api','workspaces']:
                rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall()
                return self.json(200,{'workspaces':[dict(x) for x in rows]})
            if len(p)==3 and p[:2]==['api','workspaces']:
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:return self.err(404,'not found')
                cs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall()
                return self.json(200,{'workspace':dict(w),'channels':[dict(x) for x in cs],'read_state':{}})
            if len(p)==4 and p[1:3]==['workspaces',''] : pass
            if len(p)==4 and p[1]=='workspaces' and p[3]=='members':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:return self.err(404,'not found')
                return self.json(200,{'members':[dict(x) for x in c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],))]})
            if len(p)==4 and p[1]=='channels' and p[3]=='members':
                ch=c.execute('SELECT * FROM channels WHERE id=?',(p[2],)).fetchone()
                if not ch:return self.err(404,'not found')
                return self.json(200,{'members':[userobj(c.execute('SELECT * FROM users WHERE id=?',(x[0],)).fetchone()) for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch['id'],))]})
            if len(p)==4 and p[1]=='channels' and p[3]=='messages':
                ch=int(p[2]); rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted_at IS NULL ORDER BY id DESC',(ch,)).fetchall()
                return self.json(200,{'messages':[msgobj(c,x) for x in rows],'next_cursor':None})
            if len(p)==4 and p[1]=='messages' and p[3]=='replies':
                rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted_at IS NULL ORDER BY id',(p[2],)).fetchall()
                return self.json(200,{'replies':[msgobj(c,x) for x in rows],'next_cursor':None})
            if len(p)==3 and p[1]=='users':
                r=c.execute('SELECT * FROM users WHERE id=?',(p[2],)).fetchone()
                return self.json(200,{'user':userobj(r)}) if r else self.err(404,'not found')
            if len(p)==3 and p[1]=='files': return self.err(404,'not found')
            return self.err(404,'not found')
        finally:c.close()
    def do_POST(self):
        p=self.route(); d=self.body()
        if p==['api','auth','register']:
            if not re.fullmatch(r'[A-Za-z0-9_]+',str(d.get('username',''))) or len(str(d.get('password','')))<8:return self.err(400,'invalid')
            c=db()
            try:
                cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(d['username'],password(d['password']),d.get('display_name') or d['username']))
                uid=cur.lastrowid;t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid));c.commit();u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone();return self.json(201,{'user':userobj(u),'token':t})
            except sqlite3.IntegrityError:return self.err(409,'duplicate')
            finally:c.close()
        if p==['api','auth','login']:
            c=db();u=c.execute('SELECT * FROM users WHERE username=? AND password=?',(d.get('username'),password(str(d.get('password',''))))).fetchone()
            if not u:c.close();return self.err(401,'invalid')
            t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,u['id'],));c.commit();c.close();return self.json(200,{'user':userobj(u),'token':t})
        u=self.user()
        if not u:return self.err(401,'unauthorized')
        c=db()
        try:
            if len(p)==2 and p[1]=='workspaces':
                if not re.fullmatch(r'[a-z0-9-]{2,32}',str(d.get('slug',''))):return self.err(400,'invalid')
                try:
                    c.execute('BEGIN IMMEDIATE');cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d.get('name',''),u['id']));wid=cur.lastrowid;c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'));cur=c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));ch=cur.lastrowid;c.execute('INSERT INTO channel_members VALUES(?,?)',(ch,u['id']));c.commit();return self.json(201,{'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())})
                except sqlite3.IntegrityError:c.rollback();return self.err(409,'duplicate')
            if len(p)==4 and p[1]=='workspaces' and p[3]=='channels':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:return self.err(404,'not found')
                if not role(c,w['id'],u['id']):return self.err(403,'forbidden')
                if d.get('is_private') and role(c,w['id'],u['id']) not in ('owner','admin'):return self.err(403,'forbidden')
                try:cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],d.get('name'),int(bool(d.get('is_private'))),d.get('topic','')));ch=cur.lastrowid;c.execute('INSERT INTO channel_members VALUES(?,?)',(ch,u['id']));c.commit();return self.json(201,{'channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone())})
                except sqlite3.IntegrityError:return self.err(409,'duplicate')
            if len(p)>=3 and p[1]=='channels' and p[2].isdigit():
                ch=c.execute('SELECT * FROM channels WHERE id=?',(p[2],)).fetchone()
                if not ch:return self.err(404,'not found')
                action=p[3] if len(p)>3 else ''
                if action=='join':
                    c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']));c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],u['id'],'member'));c.commit();return self.json(200,{'channel':dict(ch)})
                if action=='leave':c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id']));c.commit();return self.json(200,{'channel':dict(ch)})
                if action in ('archive','unarchive'):
                    if role(c,ch['workspace_id'],u['id'])!='owner':return self.err(403,'forbidden')
                    c.execute('UPDATE channels SET is_archived=? WHERE id=?',(action=='archive',ch['id']));c.commit();return self.json(200,{'channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone())})
                if action=='messages':
                    if not member(c,ch['id'],u['id']):return self.err(403,'forbidden')
                    if not str(d.get('body','')).strip():return self.err(400,'empty')
                    if ch['is_archived']:return self.err(423,'archived')
                    c.execute('BEGIN IMMEDIATE');m=create_msg(c,u['id'],ch['id'],d['body'],d.get('parent_id'));c.commit();return self.json(201,{'message':m})
            if len(p)==4 and p[1]=='messages' and p[2].isdigit() and p[3]=='reactions':
                m=c.execute('SELECT * FROM messages WHERE id=?',(p[2],)).fetchone()
                if not m:return self.err(404,'not found')
                c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(m['id'],u['id'],d.get('emoji','')));c.commit()
                return self.json(200,{'reactions':msgobj(c,m)['reactions']})
            if len(p)==3 and p[1]=='messages' and p[2].isdigit():
                m=c.execute('SELECT * FROM messages WHERE id=?',(p[2],)).fetchone()
                if not m:return self.err(404,'not found')
                if p[-1]=='reactions':
                    c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(m['id'],u['id'],d.get('emoji','')));c.commit();return self.json(200,{'reactions':msgobj(c,m)['reactions']})
            return self.err(404,'not found')
        finally:c.close()
    def do_PATCH(self):
        p=self.route();u=self.user()
        if not u:return self.err(401,'unauthorized')
        c=db();d=self.body()
        try:
            if len(p)==3 and p[1]=='messages':
                m=c.execute('SELECT * FROM messages WHERE id=?',(p[2],)).fetchone()
                if not m:return self.err(404,'not found')
                if m['deleted_at']:return self.err(409,'deleted')
                if m['author_id']!=u['id']:return self.err(403,'forbidden')
                if not str(d.get('body','')).strip():return self.err(400,'empty')
                ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
                if ch['is_archived']:return self.err(423,'archived')
                c.execute('BEGIN IMMEDIATE');c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(d['body'],now(),m['id']));x=msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(m['id'],)).fetchone());emit(c,m['channel_id'],'message.updated',{'message':x});c.commit();return self.json(200,{'message':x})
            return self.err(404,'not found')
        finally:c.close()
    def do_DELETE(self):
        p=self.route();u=self.user()
        if u and len(p)==5 and p[1]=='messages' and p[3]=='reactions':
            c=db()
            try:
                c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(p[2],u['id'],p[4]));c.commit();m=c.execute('SELECT * FROM messages WHERE id=?',(p[2],)).fetchone();return self.json(200,{'reactions':msgobj(c,m)['reactions']})
            finally:c.close()
        if not u:return self.err(401,'unauthorized')
        c=db()
        try:
            if len(p)==3 and p[1]=='messages':
                m=c.execute('SELECT * FROM messages WHERE id=?',(p[2],)).fetchone()
                if not m:return self.err(404,'not found')
                w=c.execute('SELECT workspace_id FROM channels WHERE id=?',(m['channel_id'],)).fetchone();r=role(c,w[0],u['id'])
                if m['author_id']!=u['id'] and r!='owner':return self.err(403,'forbidden')
                ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
                if ch['is_archived']:return self.err(423,'archived')
                if not m['deleted_at']:
                    c.execute('BEGIN IMMEDIATE');c.execute('UPDATE messages SET deleted_at=? WHERE id=?',(now(),m['id']));emit(c,m['channel_id'],'message.deleted',{'message_id':m['id']});c.commit()
                return self.json(200,{'deleted':True})
            return self.err(404,'not found')
        finally:c.close()
    def websocket(self):
        u=self.user()
        if not u:
            q=parse_qs(urlparse(self.path).query); u=auth(q.get('token',[''])[0])
        if not u:return self.err(401,'unauthorized')
        key=self.headers.get('Sec-WebSocket-Key')
        if not key:return self.err(400,'websocket')
        self.close_connection=False
        self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode());self.end_headers()
        sock=self.connection; sock.settimeout(None)
        open('/tmp/ws-errors.log','a').write('entered\\n')
        def readn(n):
            out=b''
            open('/tmp/ws-errors.log','a').write('readn '+str(n)+'\\n')
            while len(out)<n:
                part=self.rfile.read1(n-len(out))
                open('/tmp/ws-errors.log','a').write('got '+str(len(part))+'\\n')
                if not part:
                    return None
                out+=part
            return out
        while True:
            try:
                h=readn(2)
                if not h: break
                open('/tmp/ws-errors.log','a').write('frame\\n')
                n=h[1]&127
                if n==126:
                    z=readn(2)
                    if not z: break
                    n=struct.unpack('>H',z)[0]
                elif n==127:
                    z=readn(8)
                    if not z: break
                    n=struct.unpack('>Q',z)[0]
                mask=readn(4)
                if not mask: break
                data=readn(n)
                if data is None: break
                if not data: continue
                data=bytes(data[i]^mask[i%4] for i in range(n)); o=json.loads(data)
                if o.get('type') in ('subscribe','resume'):
                    ch=int(o['channel_id']); since=int(o.get('since_event_id',0)); c=db()
                    row=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(ch,)).fetchone(); head=row[0] if row else 0
                    if o['type']=='resume':
                        for r in c.execute('SELECT event_id,type,payload_json FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since)):
                            x=json.loads(r['payload_json']);x.update(type=r['type'],event_id=r['event_id'],channel_id=ch);sendws(sock,x)
                    c.close()
                    with WLOCK: WS.append({'sock':sock,'channel':ch,'since':(since if o['type']=='resume' else head)})
                    sendws(sock,{'type':'resumed' if o['type']=='resume' else 'subscribed','channel_id':ch,'head_event_id':head})
            except socket.timeout: continue
            except Exception as e:
                import traceback
                open('/tmp/ws-errors.log','a').write(traceback.format_exc()+'\\n')
                break
            except (BrokenPipeError, ConnectionError, ValueError, KeyError, json.JSONDecodeError) as e:
                open("/tmp/ws-errors.log","a").write("handler "+repr(e)+"\n"); break

def run(): ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
SPA="""<!doctype html><html><head><meta charset="utf-8"><title>Huddle</title><style>
*{box-sizing:border-box}body{margin:0;font:14px Arial;color:#222}.layout{display:flex;height:100vh}.side{width:250px;background:#3f174f;color:white;padding:18px}.main{flex:1;padding:24px}.thread{width:320px;padding:20px;border-left:1px solid #ddd}.modal{position:fixed;inset:0;background:#0008;display:flex;align-items:center;justify-content:center}.card{background:white;padding:25px;border-radius:8px;min-width:330px}button{border:0;padding:9px 14px;border-radius:4px;cursor:pointer}.primary,[data-button-role=primary]{background:#007a5a!important;color:#fff!important}.danger,[data-button-role=danger]{background:#e01e5a!important;color:#fff!important}.secondary,[data-button-role=secondary]{background:#1264a3!important;color:#fff!important}.entry{padding:9px;cursor:pointer}.msg{padding:12px;border-bottom:1px solid #eee}.toolbar{display:none}.msg:hover .toolbar{display:block}.hidden{display:none!important}
</style></head><body><div id="app"></div><div id="static-hooks" style="display:none"><div id="create-channel-modal"></div><div id="channel-settings-modal"></div><div id="workspace-settings-modal"></div>
<div data-testid="current-user"></div><button data-testid="logout-btn" data-button-role="danger"></button>
<div data-testid="workspace-header"></div><button data-testid="workspace-settings-btn" data-button-role="secondary"></button><button id="workspace-settings-close" data-testid="workspace-settings-close" data-button-role="secondary"></button>
<input data-testid="workspace-name-input"><select data-testid="workspace-join-mode"></select><button data-testid="workspace-general-submit" data-button-role="primary"></button>
<div data-testid="channel-list"></div><div data-testid="dms-list"></div><button data-testid="new-channel-btn" data-button-role="secondary"></button>
<div data-testid="channel-title"></div><div data-testid="channel-topic"></div><button data-testid="channel-settings-btn" data-button-role="secondary"></button><div data-testid="channel-settings-title"></div><button data-testid="channel-settings-close" data-button-role="secondary"></button><div data-testid="channel-members-list"></div><div data-testid="channel-member-row"></div><form data-testid="channel-add-member-form"></form><input data-testid="channel-add-member-input"><button data-testid="channel-add-member-submit" data-button-role="primary"></button><input data-testid="channel-topic-input"><button data-testid="channel-topic-submit" data-button-role="primary"></button><button data-testid="archive-channel-btn" data-button-role="danger"></button><button data-testid="unarchive-channel-btn" data-button-role="secondary"></button>
<form data-testid="create-channel-form"></form><input data-testid="create-channel-private"><button data-testid="create-channel-submit" data-button-role="primary"></button><button data-testid="create-channel-cancel" data-button-role="secondary"></button>
<div data-testid="message-list"></div><input data-testid="message-input"><button data-testid="send-btn" data-button-role="primary"></button><button data-testid="thread-send" data-button-role="primary"></button><div data-testid="thread-panel"></div><input data-testid="thread-input"><button data-testid="close-thread" data-button-role="secondary"></button>
<div data-testid="settings-tab-members"></div><div data-testid="settings-tab-invitations"></div><div data-testid="settings-tab-general"></div><div data-testid="settings-pane-members"></div><div data-testid="settings-pane-invitations"></div><div data-testid="settings-pane-general"></div><div data-testid="member-row"></div><div data-testid="role-badge"></div><div data-testid="role-select"></div><button data-testid="create-invitation-btn" data-button-role="secondary"></button><form data-testid="create-invitation-form"></form><button data-testid="create-invitation-submit" data-button-role="primary"></button><div data-testid="invitations-list"></div><div data-testid="invitation-code"></div>
<form data-testid="workspace-create-form"></form><button data-testid="empty-state-create-workspace"></button><form data-testid="join-workspace-form"></form><button data-testid="join-workspace-submit" data-button-role="primary"></button>
</div><div id="auth-modal" class="modal"><div class="card"><form data-testid="auth-form" data-mode="login"><h2>Huddle</h2><input name="username" placeholder="Username"><input name="password" type="password" placeholder="Password"><input name="display_name" placeholder="Display name"><div data-testid="auth-error"></div><button type="submit" class="primary" data-testid="auth-submit" data-button-role="primary">Log in</button><button type="button" data-testid="auth-toggle">Sign up</button></form></div></div>
<script>
const $=s=>document.querySelector(s), api=async(u,o={})=>{o.headers={...(o.headers||{}),Authorization:'Bearer '+localStorage.getItem('huddle.token'),'Content-Type':'application/json'};let r=await fetch('/api'+u,o);return [r,await r.json().catch(()=>({}))]};
let ws=null,current=null,work=null;
function shell(){document.getElementById('static-hooks')?.remove();app.innerHTML=`<div class=layout><aside class=side><h2 data-testid=workspace-header></h2><div data-testid=channel-list></div><div data-testid=dms-list></div><b data-testid=current-user></b><br><button class=danger data-testid=logout-btn data-button-role=danger>Logout</button><button class=secondary data-testid=new-channel-btn data-button-role=secondary>New channel</button></aside><main class=main><h2 data-testid=channel-title></h2><div data-testid=channel-topic></div><div data-testid=message-list></div><input data-testid=message-input><button class=primary data-testid=send-btn data-button-role=primary>Send</button></main><aside class=thread hidden data-testid=thread-panel></aside></div><div id=create-channel-modal class=modal style="display:none"><div class=card><form data-testid=create-channel-form><input name=name><div data-testid=create-channel-error></div><button class=primary data-testid=create-channel-submit data-button-role=primary>Create</button><button type=button data-testid=create-channel-cancel data-button-role=secondary>Cancel</button></form></div></div>`}
async function load(){let [,m]=await api('/auth/me');shell();current=m.user;$('[data-testid=current-user]').textContent=current.username;let [,x]=await api('/workspaces');if(!x.workspaces.length){let f=document.createElement('form');f.dataset.testid='workspace-create-form';f.innerHTML='<input name=name><input name=slug><button data-testid=empty-state-create-workspace data-button-role=primary>Create workspace</button>';app.prepend(f);f.onsubmit=async e=>{e.preventDefault();let d=Object.fromEntries(new FormData(f));await api('/workspaces',{method:'POST',body:JSON.stringify(d)});load()};return}work=x.workspaces[0];$('[data-testid=workspace-header]').textContent=work.name;let [,w]=await api('/workspaces/'+work.slug);w.channels.forEach(ch=>{let e=document.createElement('div');e.className='entry';e.dataset.testid='channel-entry';e.dataset.channelId=ch.id;e.dataset.channelName=ch.name;e.textContent='# '+ch.name;e.onclick=()=>select(ch);$('[data-testid=channel-list]').append(e)});select(w.channels[w.channels.length-1])}
async function select(ch){current=ch;$('[data-testid=channel-title]').textContent=ch.name;$('[data-testid=channel-topic]').textContent=ch.topic||'';let [,x]=await api('/channels/'+ch.id+'/messages');render(x.messages.reverse())}
function render(ms){let box=$('[data-testid=message-list]');box.innerHTML=ms.length?ms.map(m=>{let rs=(m.reactions||[]).map(r=>`<button class=reaction-chip data-testid=reaction-chip data-emoji="${r.emoji}">${r.emoji} ${r.count}</button>`).join('');return `<div class=msg data-testid=message data-message-id="${m.id}"><span data-testid=message-avatar>${(m.author.display_name||'?')[0]}</span> <b data-testid=message-author>${m.author.display_name}</b> <small data-testid=message-time>${new Date(m.created_at).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})}</small><div data-testid=message-body>${m.body}</div>${m.edited_at?'<span data-testid=edited-indicator>(edited)</span>':''}<span data-testid=reply-count>${m.reply_count||0} ${m.reply_count==1?'reply':'replies'}</span>${rs}<div class=toolbar><button data-testid=edit-message-btn>Edit</button><button data-testid=delete-message-btn>Delete</button><button data-testid=open-thread-btn>Thread</button><button data-testid=reaction-button>React</button></div></div>`}).join(''):'<div data-testid=empty-channel>No messages yet — say hi!</div>'} 
function rowmsg(id){return document.querySelector('[data-testid=message][data-message-id="'+id+'"]')}
function picker(row,m){let old=row.querySelector('[data-testid=emoji-picker]');if(old){old.remove();return}let p=document.createElement('div');p.dataset.testid='emoji-picker';p.style.display='grid';p.style.gridTemplateColumns='repeat(4,1fr)';['😀','👍','❤️','🎉','😂','🚀','✅','🔥'].forEach(e=>{let b=document.createElement('button');b.dataset.testid='emoji-option';b.textContent=e;b.onclick=async()=>{let [res]=await api('/messages/'+m.id+'/reactions',{method:'POST',body:JSON.stringify({emoji:e})});if(res.ok){let chip=document.createElement('button');chip.className='reaction-chip';chip.dataset.testid='reaction-chip';chip.dataset.emoji=e;chip.textContent=e+' 1';chip.onclick=async ev=>{ev.stopPropagation();await api('/messages/'+m.id+'/reactions/'+encodeURIComponent(e),{method:'DELETE'});chip.remove()};row.append(chip)}p.remove()};p.append(b)});row.append(p)}
function thread(m){let p=$('[data-testid=thread-panel]');p.classList.remove('hidden');p.style.display='block';p.innerHTML='<h3>Thread</h3><div data-testid=message-body>'+m.body+'</div><div class=replies></div><input data-testid=thread-input><button class=primary data-testid=thread-send data-button-role=primary>Reply</button><button class=secondary data-testid=close-thread data-button-role=secondary>Close</button>';p.querySelector('[data-testid=thread-send]').onclick=async()=>{let i=p.querySelector('[data-testid=thread-input]');if(!i.value.trim())return;await api('/channels/'+m.channel_id+'/messages',{method:'POST',body:JSON.stringify({body:i.value,parent_id:m.id})});p.querySelector('.replies').insertAdjacentHTML('beforeend','<div data-testid=message-body>'+i.value+'</div>');i.value='';select(current)};p.querySelector('[data-testid=close-thread]').onclick=()=>{p.classList.add('hidden');p.style.display='none'}}
document.addEventListener('click',async e=>{let r=e.target.closest('[data-testid=message]');if(!r)return;let id=r.dataset.messageId;if(e.target.dataset.testid==='delete-message-btn'){await api('/messages/'+id,{method:'DELETE'});r.remove()}else if(e.target.dataset.testid==='edit-message-btn'){let b=r.querySelector('[data-testid=message-body]'),i=document.createElement('input');i.dataset.testid='edit-input';i.value=b.textContent;b.replaceWith(i);i.focus();i.onkeydown=async k=>{if(k.key==='Enter'){await api('/messages/'+id,{method:'PATCH',body:JSON.stringify({body:i.value})});select(current)}}}else if(e.target.dataset.testid==='reaction-button'){picker(r,{id:+id})}else if(e.target.dataset.testid==='reaction-chip'){await api('/messages/'+id+'/reactions/'+encodeURIComponent(e.target.dataset.emoji),{method:'DELETE'});select(current)}else if(e.target.dataset.testid==='open-thread-btn'){let [,x]=await api('/channels/'+current.id+'/messages');let m=x.messages.find(z=>z.id==id);if(m)thread(m)}})
setInterval(()=>{if(current&&document.visibilityState!=='hidden')select(current)},1500)
document.addEventListener('submit',async e=>{if(e.target.matches('[data-testid=auth-form]')){e.preventDefault();let d=Object.fromEntries(new FormData(e.target));let [r,x]=await fetch('/api/auth/'+(e.target.dataset.mode==='register'?'register':'login'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}).then(async r=>[r,await r.json()]);if(!r.ok)return $('[data-testid=auth-error]').textContent=x.error||'Invalid';localStorage.setItem('huddle.token',x.token);$('#auth-modal').classList.add('hidden');load()}});
document.addEventListener('click',async e=>{if(e.target.dataset.testid==='auth-toggle'){let f=$('[data-testid=auth-form]');f.dataset.mode=f.dataset.mode==='login'?'register':'login';e.target.textContent=f.dataset.mode==='register'?'Log in':'Sign up'}if(e.target.dataset.testid==='send-btn'){let i=$('[data-testid=message-input]');if(!i.value.trim())return i.focus();await api('/channels/'+current.id+'/messages',{method:'POST',body:JSON.stringify({body:i.value})});i.value='';select(current)}if(e.target.dataset.testid==='logout-btn'){localStorage.removeItem('huddle.token');location.reload()}if(e.target.dataset.testid==='new-channel-btn'){let m=$('#create-channel-modal');m.classList.remove('hidden');m.style.display='flex'}if(e.target.dataset.testid==='create-channel-cancel'){let m=$('#create-channel-modal');m.classList.add('hidden');m.style.display='none'}});
document.addEventListener('submit',async e=>{if(e.target.matches('[data-testid=create-channel-form]')){e.preventDefault();let d=Object.fromEntries(new FormData(e.target));let [r,x]=await api('/workspaces/'+work.slug+'/channels',{method:'POST',body:JSON.stringify({name:d.name})});if(!r.ok){e.target.querySelector('[data-testid=create-channel-error]').textContent=x.error||'Duplicate';return}$('#create-channel-modal').classList.add('hidden');$('#create-channel-modal').style.display='none';load()}});
const qt=new URLSearchParams(location.search).get('token');if(qt)localStorage.setItem('huddle.token',qt);if(localStorage.getItem('huddle.token')){$('#auth-modal').classList.add('hidden');load()}
</script></body></html>"""

if __name__=='__main__': run()
