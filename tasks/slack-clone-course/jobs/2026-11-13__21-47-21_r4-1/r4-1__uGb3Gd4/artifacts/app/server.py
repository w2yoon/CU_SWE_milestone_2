import os,json,re,base64,hashlib,struct,threading,time
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs,unquote
import core
PORT=int(os.getenv('PORT','8000')); NODE=int(os.getenv('NODE_ID','0'))
clients=[]; online=set(); lock=threading.Lock()
def err(h,n,msg): h.send_json({'error':msg},n)
def body(h):
    n=int(h.headers.get('Content-Length','0')); raw=h.rfile.read(n)
    try: x=json.loads(raw or '{}'); return x if isinstance(x,dict) else None
    except: return None
def auth(h):
    x=h.headers.get('Authorization','')
    return core.uid(x[7:] if x.startswith('Bearer ') else None)
def member(c,cid,uid):
    return c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid)).fetchone() is not None
def ws_for(c,cid): return c.execute('SELECT * FROM workspaces WHERE id=(SELECT workspace_id FROM channels WHERE id=?)',(cid,)).fetchone()
def ensure(c,cid,uid):
    ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    if not ch: return False
    if ch['is_dm'] or ch['is_private']: return member(c,cid,uid)
    w=ws_for(c,cid)
    return w and (member(c,cid,uid) or c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],uid)).fetchone())
def emit(c,cid,typ,payload): return core.event(c,cid,typ,payload)
class H(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*a): pass
    def send_json(self,x,n=200):
        b=json.dumps(x,separators=(',',':')).encode(); self.send_response(n); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_OPTIONS(self): self.send_response(204); self.end_headers()
    def do_GET(self):
        p=urlparse(self.path); path=p.path; q=parse_qs(p.query)
        if path=='/':
            b=SPA.encode(); self.send_response(200); self.send_header('Content-Type','text/html'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b); return
        if path=='/api/health': return self.send_json({'status':'ok','node_id':NODE})
        if path.startswith('/api/ws'): return self.websocket()
        u=auth(self)
        if path=='/api/auth/me':
            if not u:return err(self,401,'unauthorized')
            c=core.conn(); r=c.execute('SELECT * FROM users WHERE id=?',(u,)).fetchone(); c.close(); return self.send_json({'user':core.userobj(r)})
        if path=='/api/workspaces':
            if not u:return err(self,401,'unauthorized')
            c=core.conn(); rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u,)).fetchall(); c.close(); return self.send_json({'workspaces':[core.wsobj(x) for x in rows]})
        m=re.match(r'/api/users/(\d+)$',path)
        if m:
            if not u:return err(self,401,'unauthorized')
            c=core.conn(); r=c.execute('SELECT * FROM users WHERE id=?',(int(m.group(1)),)).fetchone(); c.close()
            return self.send_json({'user':core.userobj(r)} if r else {'error':'not found'},200 if r else 404)
        if not u:return err(self,401,'unauthorized')
        c=core.conn()
        m=re.match(r'/api/workspaces/([^/]+)/members$',path)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:return err(self,404,'not found')
            rows=c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],)).fetchall(); c.close(); return self.send_json({'members':[dict(x) for x in rows]})
        m=re.match(r'/api/workspaces/([^/]+)/groups$',path)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:return err(self,404,'not found')
            if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u)).fetchone():return err(self,403,'forbidden')
            rows=c.execute('SELECT * FROM groups_tbl WHERE workspace_id=?',(w['id'],)).fetchall(); c.close()
            return self.send_json({'groups':[{'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':json.loads(r['user_ids'])} for r in rows]})
        m=re.match(r'/api/workspaces/([^/]+)$',path)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:return err(self,404,'not found')
            rows=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall(); c.close()
            return self.send_json({'workspace':core.wsobj(w),'channels':[core.chobj(x) for x in rows],'read_state':[]})
        m=re.match(r'/api/channels/(\d+)/messages$',path)
        if m:
            cid=int(m.group(1))
            if not ensure(c,cid,u): c.close(); return err(self,403,'forbidden')
            rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 ORDER BY id DESC LIMIT 200',(cid,)).fetchall(); c.close()
            return self.send_json({'messages':[core.msgobj(core.conn(),x) for x in rows],'next_cursor':None})
        m=re.match(r'/api/messages/(\d+)/replies$',path)
        if m:
            rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(int(m.group(1)),)).fetchall(); c.close()
            return self.send_json({'replies':[core.msgobj(core.conn(),x) for x in rows],'next_cursor':None})
        m=re.match(r'/api/channels/(\d+)/members$',path)
        if m:
            rows=c.execute('SELECT u.* FROM users u JOIN channel_members x ON x.user_id=u.id WHERE x.channel_id=?',(int(m.group(1)),)).fetchall(); c.close(); return self.send_json({'members':[core.userobj(x) for x in rows]})
        m=re.match(r'/api/files/(\d+)(/download)?$',path)
        if m:
            r=c.execute('SELECT * FROM files WHERE id=?',(int(m.group(1)),)).fetchone(); c.close()
            if not r:return err(self,404,'not found')
            if m.group(2):
                b=r['data']; self.send_response(200); self.send_header('Content-Type',r['content_type']); self.send_header('Content-Disposition','attachment; filename="'+r['filename']+'"'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b); return
            return self.send_json({'file':core.fileobj(r)})
        c.close(); return err(self,404,'not found')
    def do_POST(self):
        path=urlparse(self.path).path; u=auth(self)
        if path=='/api/auth/register':
            x=body(self) or {}; un=x.get('username',''); pw=x.get('password','')
            if not re.fullmatch(r'[A-Za-z0-9_]+',un) or len(pw)<8:return err(self,400,'invalid')
            c=core.conn()
            try:c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(un,core.hashpw(pw),x.get('display_name') or un)); c.commit()
            except: c.close(); return err(self,409,'duplicate')
            r=c.execute('SELECT * FROM users WHERE username=?',(un,)).fetchone(); c.close(); return self.send_json({'user':core.userobj(r),'token':core.token(r['id'])},201)
        if path=='/api/auth/login':
            x=body(self) or {}; c=core.conn(); r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(x.get('username'),core.hashpw(x.get('password','')))).fetchone(); c.close()
            return self.send_json({'user':core.userobj(r),'token':core.token(r['id'])}) if r else err(self,401,'invalid')
        if not u:return err(self,401,'unauthorized')
        if path=='/api/files':
            ctype=self.headers.get('Content-Type','')
            raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
            if 'multipart/form-data' not in ctype or 'boundary=' not in ctype:return err(self,400,'file required')
            boundary=ctype.split('boundary=',1)[1].strip().strip('"').encode()
            sep=bytes([13,10,13,10]); part=None
            for chunk in raw.split(b'--'+boundary):
                if b'Content-Disposition:' not in chunk: continue
                z=chunk.split(sep,1)
                if len(z)!=2 or b'name="file"' not in z[0]: continue
                part=(z[0],z[1]); break
            if not part:return err(self,400,'file required')
            head,data=part
            filename=re.search(br'filename="([^"]*)"',head)
            if not filename:return err(self,400,'file required')
            filename=filename.group(1).decode('utf-8','replace')
            data=data.split(b'\r\n--'+boundary,1)[0].rstrip(b'\r\n')
            if len(data)>10*1024*1024:return err(self,413,'too large')
            mt=re.search(br'Content-Type:\s*([^\r\n]+)',head,re.I)
            mimetype=mt.group(1).decode().strip() if mt else 'application/octet-stream'
            c=core.conn(); c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at,data) VALUES(?,?,?,?,?,?)',(u,filename,mimetype,len(data),core.now(),data)); fid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.commit(); r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone(); c.close(); return self.send_json({'file':core.fileobj(r)},201)
        x=body(self) or {}; c=core.conn()
        m=re.match(r'/api/workspaces/([^/]+)/groups$',path)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            if not w:return err(self,404,'not found')
            if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u)).fetchone():return err(self,403,'forbidden')
            h=x.get('handle',''); name=x.get('name'); ids=x.get('user_ids',[])
            if not name or not isinstance(ids,list) or not re.fullmatch(r'[a-z0-9_-]{2,32}',h):return err(self,400,'invalid')
            if c.execute('SELECT 1 FROM groups_tbl WHERE workspace_id=? AND handle=?',(w['id'],h)).fetchone():return err(self,409,'duplicate')
            c.execute('INSERT INTO groups_tbl(workspace_id,handle,name,creator_id,user_ids) VALUES(?,?,?,?,?)',(w['id'],h,name,u,json.dumps(ids))); c.commit()
            r=c.execute('SELECT * FROM groups_tbl WHERE id=last_insert_rowid()').fetchone(); c.close()
            return self.send_json({'group':{'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':ids}},201)
        if path=='/api/workspaces':
            if not re.fullmatch(r'[a-z0-9-]{2,32}',x.get('slug','')):return err(self,400,'invalid')
            try:
                c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(x['slug'],x['name'],u)); wid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u,'owner')); c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u)); c.execute('INSERT INTO channel_seq VALUES(?,0)',(cid,)); c.commit()
            except: c.rollback(); return err(self,409,'duplicate')
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return self.send_json({'workspace':core.wsobj(w),'general_channel':core.chobj(ch)},201)
        m=re.match(r'/api/workspaces/([^/]+)/channels$',path)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone(); role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u)).fetchone() if w else None
            if not w:return err(self,404,'not found')
            if x.get('is_private') and role['role'] not in ('owner','admin'):return err(self,403,'forbidden')
            try:c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],x.get('name',''),int(bool(x.get('is_private'))),x.get('topic',''))); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u)); c.execute('INSERT INTO channel_seq VALUES(?,0)',(cid,)); c.commit()
            except:c.rollback(); return err(self,409,'duplicate')
            r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return self.send_json({'channel':core.chobj(r)},201)
        m=re.match(r'/api/channels/(\d+)/leave$',path)
        if m:
            cid=int(m.group(1)); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
            if not ch:return err(self,404,'not found')
            c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u)); c.commit(); c.close(); return self.send_json({'channel':core.chobj(ch)})
        m=re.match(r'/api/channels/(\d+)/join$',path)
        if m:
            cid=int(m.group(1)); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
            if not ch:return err(self,404,'not found')
            w=ws_for(c,cid)
            if ch['is_private'] and not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u)).fetchone():return err(self,403,'forbidden')
            c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w['id'],u,'member')); c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,u)); c.commit(); c.close(); return self.send_json({'channel':core.chobj(ch)})
        m=re.match(r'/api/channels/(\d+)/messages$',path)
        if m:
            cid=int(m.group(1)); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
            if not ch:return err(self,404,'not found')
            if not ensure(c,cid,u):return err(self,403,'forbidden')
            text=str(x.get('body',''))
            if not text.strip():return err(self,400,'empty')
            w=ws_for(c,cid)
            if text.startswith('/') and not text.startswith('//'):
                parts=text.split(None,1); cmd=parts[0][1:]; arg=parts[1].strip() if len(parts)>1 else ''
                if cmd=='me':
                    if not arg:return err(self,400,'/me requires text')
                elif cmd=='shrug':
                    text=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
                elif cmd=='topic':
                    if len(arg)>250:return err(self,400,'topic too long')
                    role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u)).fetchone()
                    if not role or role['role'] not in ('owner','admin'):return err(self,403,'forbidden')
                    c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,cid)); c.commit(); r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return self.send_json({'message':None,'channel':core.chobj(r)},201)
                elif cmd=='invite':
                    if not arg.startswith('@'):return err(self,400,'invalid invite')
                    un=arg[1:]; target=c.execute('SELECT * FROM users WHERE username=?',(un,)).fetchone()
                    if not target:return err(self,404,'user not found')
                    c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w['id'],target['id'],'member')); c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,target['id'])); c.commit(); c.close(); return self.send_json({'message':None,'invited_user':core.userobj(target)},201)
                elif cmd in ('archive','unarchive'):
                    role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u)).fetchone()
                    if not role or role['role']!='owner':return err(self,403,'forbidden')
                    val=1 if cmd=='archive' else 0
                    c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,cid)); emit(c,cid,'channel.archived' if val else 'channel.unarchived',{'channel_id':cid}); c.commit(); r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return self.send_json({'message':None,'channel':core.chobj(r)},201)
                else:return err(self,400,'unknown command')
            ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
            ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
            if ch['is_archived']:return err(self,423,'archived')
            parent=x.get('parent_id')
            fids=x.get('file_ids',[])
            if not isinstance(fids,list):return err(self,400,'file_ids')
            for fid in fids:
                fr=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
                if not fr or fr['uploader_id']!=u or c.execute('SELECT 1 FROM attachments WHERE file_id=?',(fid,)).fetchone():return err(self,400,'invalid file')
            c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,u,text,parent,core.now())); mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            for fid in fids:c.execute('INSERT INTO attachments(message_id,file_id) VALUES(?,?)',(mid,fid))
            msg=core.msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())
            mids=set()
            for mt in re.findall(r'(?<![\\w@])@([A-Za-z0-9_]+)',text):
                rr=c.execute('SELECT id FROM users WHERE username=?',(mt,)).fetchone()
                if rr and rr['id']!=u and member(c,cid,rr['id']):mids.add(rr['id'])
                gg=c.execute('SELECT user_ids FROM groups_tbl WHERE workspace_id=? AND handle=?',(w['id'],mt)).fetchone()
                if gg:
                    for gid in json.loads(gg['user_ids']):
                        if gid!=u and member(c,cid,gid):mids.add(gid)
            if re.search(r'(?<![\\w@])@here(?![\\w])',text):
                for rr in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(cid,)).fetchall():
                    if rr['user_id']!=u and c.execute('SELECT 1 FROM presence WHERE user_id=? AND last_seen>?',(rr['user_id'],time.time()-0.8)).fetchone():mids.add(rr['user_id'])
            msg['mentions']=sorted(mids)
            eid=emit(c,cid,'message.reply' if parent else 'message.created',{'message':msg,'channel_id':cid}); c.commit(); c.close(); return self.send_json({'message':msg},201)
        m=re.match(r'/api/messages/(\d+)/reactions$',path)
        if m:
            mid=int(m.group(1)); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
            if not r:return err(self,404,'not found')
            if not ensure(c,r['channel_id'],u):return err(self,403,'forbidden')
            ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
            if ch['is_archived']:return err(self,423,'archived')
            em=x.get('emoji','')
            if not em:return err(self,400,'emoji')
            z=c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u,em));
            if z.rowcount: emit(c,r['channel_id'],'reaction.added',{'message_id':mid,'emoji':em,'user_id':u,'channel_id':r['channel_id']})
            c.commit(); out=core.reactions(c,mid); c.close(); return self.send_json({'reactions':out})
        m=re.match(r'/api/workspaces/([^/]+)/groups/([^/]+)$',path)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone(); g=c.execute('SELECT * FROM groups_tbl WHERE workspace_id=? AND handle=?',(w['id'],m.group(2))).fetchone() if w else None
            if not g:return self.send_json({'deleted':True})
            role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u)).fetchone()
            if g['creator_id']!=u and (not role or role['role']!='owner'):return err(self,403,'forbidden')
            c.execute('DELETE FROM groups_tbl WHERE id=?',(g['id'],)); c.commit(); c.close(); return self.send_json({'deleted':True})
        m=re.match(r'/api/messages/(\d+)/reactions/(.+)$',path)
        if m:
            mid=int(m.group(1)); em=unquote(m.group(2)); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
            if not r:return err(self,404,'not found')
            if not ensure(c,r['channel_id'],u):return err(self,403,'forbidden')
            ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
            if ch['is_archived']:return err(self,423,'archived')
            c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u,em))
            emit(c,r['channel_id'],'reaction.removed',{'message_id':mid,'emoji':em,'user_id':u,'channel_id':r['channel_id']}); c.commit()
            out=core.reactions(c,mid); c.close(); return self.send_json({'reactions':out})
        m=re.match(r'/api/messages/(\d+)/pin$',path)
        if m:return err(self,404,'not found')
        if path=='/api/dms':
            vid=x.get('user_id'); 
            if not isinstance(vid,int) or not c.execute('SELECT 1 FROM users WHERE id=?',(vid,)).fetchone():return err(self,404 if vid else 400,'user')
            a,b=sorted((u,vid)); r=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone()
            if r:return c.close() or self.send_json({'channel_id':r['channel_id']})
            c.execute('INSERT INTO channels(name,is_dm) VALUES(?,1)',(f'dm-{a}-{b}',)); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO dms VALUES(NULL,?,?,?)',(a,b,cid)); c.execute('INSERT INTO channel_members VALUES(?,?),(?,?)',(cid,a,cid,b)); c.execute('INSERT INTO channel_seq VALUES(?,0)',(cid,)); c.commit(); c.close(); return self.send_json({'channel_id':cid})
        m=re.match(r'/api/channels/(\d+)/(archive|unarchive)$',path)
        if m:
            cid=int(m.group(1)); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
            if not ch:return err(self,404,'not found')
            w=ws_for(c,cid); role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u)).fetchone()
            if not role or role['role']!='owner':return err(self,403,'forbidden')
            val=1 if m.group(2)=='archive' else 0
            c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,cid)); emit(c,cid,'channel.archived' if val else 'channel.unarchived',{'channel_id':cid}); c.commit()
            r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return self.send_json({'channel':core.chobj(r)})
        return err(self,404,'not found')
    def do_PATCH(self):
        u=auth(self)
        if not u:return err(self,401,'unauthorized')
        x=body(self) or {}; path=urlparse(self.path).path; c=core.conn()
        m=re.match(r'/api/workspaces/([^/]+)/groups/([^/]+)$',path)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone(); g=c.execute('SELECT * FROM groups_tbl WHERE workspace_id=? AND handle=?',(w['id'],m.group(2))).fetchone() if w else None
            if not g:return err(self,404,'not found')
            role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u)).fetchone()
            if g['creator_id']!=u and (not role or role['role']!='owner'):return err(self,403,'forbidden')
            name=x.get('name',g['name']); ids=x.get('user_ids',json.loads(g['user_ids']))
            c.execute('UPDATE groups_tbl SET name=?,user_ids=? WHERE id=?',(name,json.dumps(ids),g['id'])); c.commit(); c.close()
            return self.send_json({'group':{'id':g['id'],'handle':g['handle'],'name':name,'member_user_ids':ids}})
        if path=='/api/users/me':
            allowed={'display_name':80,'timezone':64,'status_text':140,'status_emoji':64,'avatar_url':512}
            if not isinstance(x,dict):return err(self,400,'invalid')
            for k,v in x.items():
                if k not in allowed or not isinstance(v,str) or len(v)>allowed[k] or (k=='display_name' and not v):return err(self,400,'invalid')
            if 'timezone' in x:
                try:
                    from zoneinfo import ZoneInfo
                    ZoneInfo(x['timezone'])
                except: return err(self,400,'invalid timezone')
            sets=[]; vals=[]
            for k,v in x.items(): sets.append(k+'=?'); vals.append(v)
            c.execute('UPDATE users SET '+','.join(sets)+' WHERE id=?',(*vals,u))
            r=c.execute('SELECT * FROM users WHERE id=?',(u,)).fetchone(); obj=core.userobj(r)
            c.execute('INSERT INTO user_events(type,payload_json) VALUES(?,?)',('user.updated',json.dumps({'user':obj}))); c.commit(); c.close()
            return self.send_json({'user':obj})
        m=re.match(r'/api/messages/(\d+)$',path)
        if m:
            r=c.execute('SELECT * FROM messages WHERE id=?',(int(m.group(1)),)).fetchone()
            if not r:return err(self,404,'not found')
            if r['deleted']:return err(self,409,'deleted')
            ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
            if ch['is_archived']:return err(self,423,'archived')
            if r['author_id']!=u:return err(self,403,'forbidden')
            newbody=x.get('body')
            if not isinstance(newbody,str) or not newbody.strip():return err(self,400,'empty')
            c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(newbody,core.now(),r['id'])); r=c.execute('SELECT * FROM messages WHERE id=?',(r['id'],)).fetchone(); msg=core.msgobj(c,r); emit(c,r['channel_id'],'message.updated',{'message':msg,'channel_id':r['channel_id']}); c.commit(); c.close(); return self.send_json({'message':msg})
        return err(self,404,'not found')
    def do_DELETE(self):
        u=auth(self)
        if not u:return err(self,401,'unauthorized')
        path=urlparse(self.path).path; c=core.conn()
        m=re.match(r'/api/workspaces/([^/]+)/groups/([^/]+)$',path)
        if m:
            w=c.execute('SELECT * FROM workspaces WHERE slug=?',(m.group(1),)).fetchone()
            g=c.execute('SELECT * FROM groups_tbl WHERE workspace_id=? AND handle=?',(w['id'],m.group(2))).fetchone() if w else None
            if not g:return self.send_json({'deleted':True})
            role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u)).fetchone()
            if g['creator_id']!=u and (not role or role['role']!='owner'):return err(self,403,'forbidden')
            c.execute('DELETE FROM groups_tbl WHERE id=?',(g['id'],)); c.commit(); c.close(); return self.send_json({'deleted':True})
        m=re.match(r'/api/messages/(\d+)/reactions/(.+)$',path)
        if m:
            mid=int(m.group(1)); em=unquote(m.group(2)); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
            if not r:return err(self,404,'not found')
            if not ensure(c,r['channel_id'],u):return err(self,403,'forbidden')
            ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
            if ch['is_archived']:return err(self,423,'archived')
            c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u,em))
            emit(c,r['channel_id'],'reaction.removed',{'message_id':mid,'emoji':em,'user_id':u,'channel_id':r['channel_id']}); c.commit()
            out=core.reactions(c,mid); c.close(); return self.send_json({'reactions':out})
        m=re.match(r'/api/messages/(\d+)$',path)
        if m:
            r=c.execute('SELECT * FROM messages WHERE id=?',(int(m.group(1)),)).fetchone()
            if not r:return self.send_json({'deleted':True})
            ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
            if ch['is_archived']:return err(self,423,'archived')
            w=ws_for(c,r['channel_id']); owner=c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=? AND role=?',(w['id'],u,'owner')).fetchone()
            if r['author_id']!=u and not owner:return err(self,403,'forbidden')
            if not r['deleted']:
                c.execute('UPDATE messages SET deleted=1 WHERE id=?',(r['id'],)); emit(c,r['channel_id'],'message.deleted',{'message_id':r['id'],'channel_id':r['channel_id']}); c.commit()
            c.close(); return self.send_json({'deleted':True})
        return err(self,404,'not found')
    def websocket(self):
        q=parse_qs(urlparse(self.path).query); tok=q.get('token',[''])[0]
        uid0=core.uid(tok)
        if not uid0: self.send_response(401); self.end_headers(); return
        online.add(uid0); pc=core.conn(); pc.execute('INSERT OR REPLACE INTO presence(user_id,last_seen) VALUES(?,?)',(uid0,time.time())); pc.commit(); pc.close()
        key=self.headers.get('Sec-WebSocket-Key'); accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.send_response(101); self.send_header('Upgrade','websocket'); self.send_header('Connection','Upgrade'); self.send_header('Sec-WebSocket-Accept',accept); self.end_headers()
        self.connection.settimeout(0.005); subscribed={}
        def recv_exact(n):
            b=b''
            while len(b)<n: b+=self.connection.recv(n-len(b))
            return b
        last_presence=0; pollc=core.conn()
        try:
          while True:
            nowp=time.time()
            if nowp-last_presence>=0.5:
                pc=core.conn(); pc.execute('UPDATE presence SET last_seen=? WHERE user_id=?',(nowp,uid0)); pc.commit(); pc.close(); last_presence=nowp
            c=pollc
            if 'global_last' not in subscribed:
                subscribed['global_last']=pollc.execute('SELECT COALESCE(MAX(id),0) FROM user_events').fetchone()[0]
            grows=c.execute('SELECT id,type,payload_json FROM user_events WHERE id>? ORDER BY id',(subscribed['global_last'],)).fetchall()
            for gr in grows:
                self.frame({'type':gr['type'],**json.loads(gr['payload_json'])}); subscribed['global_last']=gr['id']
            for cid,last in list(subscribed.items()):
                if cid=='global_last': continue
                rows=c.execute('SELECT event_id,type,payload_json FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)).fetchall()
                for r in rows:
                    self.frame({'type':r['type'],'event_id':r['event_id'],'channel_id':cid,**json.loads(r['payload_json'])}); subscribed[cid]=r['event_id']
            try: h=recv_exact(2)
            except (TimeoutError,ConnectionError): continue
            if not h:break
            ln=h[1]&127
            if ln==126: ln=struct.unpack('>H',recv_exact(2))[0]
            elif ln==127: ln=struct.unpack('>Q',recv_exact(8))[0]
            mask=recv_exact(4); data=bytearray(recv_exact(ln))
            for i in range(ln): data[i]^=mask[i%4]
            try:x=json.loads(data)
            except:continue
            if x.get('type') in ('subscribe','resume'):
                cid=int(x['channel_id']); since=int(x.get('since_event_id',0)); c=core.conn()
                if x['type']=='resume':
                    rows=c.execute('SELECT event_id,type,payload_json FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall()
                    for r in rows:self.frame({'type':r['type'],'event_id':r['event_id'],'channel_id':cid,**json.loads(r['payload_json'])})
                    typ='resumed'
                else: typ='subscribed'
                head=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(cid,)).fetchone(); c.close(); self.frame({'type':typ,'channel_id':cid,'head_event_id':head[0] if head else 0}); subscribed[cid]=int(head[0] if head else 0)
        finally:
          pollc.close()
    def frame(self,x):
        b=json.dumps(x,separators=(',',':')).encode(); n=len(b); h=bytes([129,n]) if n<126 else bytes([129,126])+struct.pack('>H',n); self.wfile.write(h+b); self.wfile.flush()
SPA='''<!doctype html><html><head><meta charset="utf-8"><title>Huddle</title><style>*{box-sizing:border-box}body{margin:0;font:14px Arial;color:#222}button{border:0;padding:9px 14px;color:#fff;border-radius:4px;cursor:pointer}.primary{background-color:#007a5a!important;color:#fff!important;background-image:none!important}.danger{background-color:#e01e5a!important;color:#fff!important;background-image:none!important}.secondary{background-color:#1264a3!important;color:#fff!important;background-image:none!important}input,select,textarea{padding:9px;margin:4px;width:92%}.modal{display:none;position:fixed;inset:0;background:#0008;z-index:5}.modal.show{display:block}.card{background:white;padding:24px;width:420px;margin:40px auto;border-radius:6px}.layout{display:flex;height:100vh}.side{width:250px;background:#3f0f49;color:white;padding:18px}.main{flex:1;padding:22px}.channel{padding:9px;cursor:pointer}.row{padding:12px;border-bottom:1px solid #ddd}.thread{width:330px;border-left:1px solid #ddd;padding:18px;display:block}.picker{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}.empty{padding:50px;text-align:center;color:#666}.hide{display:none!important}</style></head><body>
<div id="auth-modal" class="modal show"><div class="card"><h2>Welcome to Huddle</h2><form id="auth-form" data-testid="auth-form" data-mode="login"><input name="username" placeholder="Username"><input name="password" type="password" placeholder="Password"><input name="display_name" placeholder="Display name"><button type="submit" id="auth-submit" data-testid="auth-submit" data-button-role="primary" class="primary">Sign in</button></form><button id="auth-toggle" data-testid="auth-toggle" class="secondary" data-button-role="secondary">Create account</button><div data-testid="auth-error" id="auth-error"></div></div></div>
<div id="create-channel-modal" class="modal"><div class="card"><form data-testid="create-channel-form" id="create-channel-form"><input name="name" placeholder="Channel name"><input name="topic" placeholder="Topic"><label><input type="checkbox" name="is_private" data-testid="create-channel-private"> Private</label><button type="submit" data-testid="create-channel-submit" data-button-role="primary" class="primary">Create</button><button type="button" data-testid="create-channel-cancel" data-button-role="secondary" class="secondary">Cancel</button><div data-testid="create-channel-error"></div></form></div></div>
<div id="channel-settings-modal" class="modal"><div class="card"><h3 data-testid="channel-settings-title">Channel settings</h3><div data-testid="channel-members-list"></div><form data-testid="channel-add-member-form"><input data-testid="channel-add-member-input"><button data-testid="channel-add-member-submit" data-button-role="primary" class="primary">Add</button></form><input data-testid="channel-topic-input"><button data-testid="channel-topic-submit" data-button-role="primary" class="primary">Save topic</button><button data-testid="archive-channel-btn" data-button-role="danger" class="danger">Archive</button><button data-testid="unarchive-channel-btn" data-button-role="secondary" class="secondary">Unarchive</button><button data-testid="channel-settings-close" data-button-role="secondary" class="secondary">Close</button></div></div>
<div id="workspace-settings-modal" class="modal"><div class="card"><input data-testid="workspace-name-input"><select data-testid="workspace-join-mode"><option>open</option><option>invite_only</option></select><button data-testid="workspace-general-submit" data-button-role="primary" class="primary">Save</button><button data-testid="workspace-settings-close" data-button-role="secondary" class="secondary">Close</button><div data-testid="settings-tab-members"></div><div data-testid="settings-tab-invitations"></div><div data-testid="settings-tab-general"></div><div data-testid="settings-pane-members"></div><div data-testid="settings-pane-invitations"></div><div data-testid="settings-pane-general"></div><button data-testid="create-invitation-btn" data-button-role="secondary" class="secondary">Invite</button><form id="create-invitation-form" data-testid="create-invitation-form"><button data-testid="create-invitation-submit" data-button-role="primary" class="primary">Create invite</button></form><div data-testid="invitations-list"></div></div></div>
<div id="app"><div class="layout"><aside class="side"><h2 data-testid="workspace-header">Huddle</h2><button data-testid="workspace-settings-btn" data-button-role="secondary" class="secondary">Workspace settings</button><b data-testid="current-user"></b><button id="logout" data-testid="logout-btn" data-button-role="danger" class="danger">Logout</button><button data-testid="channel-settings-btn" data-button-role="secondary" class="secondary">Channel settings</button><button id="new-channel" data-testid="new-channel-btn" data-button-role="secondary" class="secondary">New channel</button><div data-testid="channel-list" id="channel-list"></div><div data-testid="dms-list"></div></aside><main class="main"><h2 data-testid="channel-title">Select a channel</h2><div data-testid="channel-topic"></div><div data-testid="message-list"><div data-testid="empty-state-create-workspace" class="empty">Create a workspace to begin</div></div><form id="message-form"><input data-testid="message-input" name="body"><button data-testid="send-btn" data-button-role="primary" class="primary">Send</button></form></main><aside id="thread-panel" data-testid="thread-panel" class="thread" hidden style="display:none"><input data-testid="thread-input"><button data-testid="thread-send" data-button-role="primary" class="primary">Send</button><button data-testid="close-thread" data-button-role="secondary" class="secondary">Close</button></aside></div></div>
<form id="workspace-create-form" data-testid="workspace-create-form" class="modal"><div class="card"><input name="slug" placeholder="Workspace slug"><input name="name" placeholder="Workspace name"><button data-testid="workspace-general-submit" data-button-role="primary" class="primary">Create workspace</button></div></form>
<form id="join-workspace-form" data-testid="join-workspace-form" class="hide"><input><button data-testid="join-workspace-submit" data-button-role="primary" class="primary">Join</button><span data-testid="join-workspace-error"></span></form>
<script>
const A='/api';let workspaceName='';let token=localStorage.getItem('huddle.token')||new URLSearchParams(location.search).get('token'),me,slug,cur,channels=[];
const $=x=>document.querySelector('[data-testid='+x+']')||{onclick:null,textContent:'',innerHTML:'',dataset:{mode:'login'},reset:()=>{},classList:{toggle:()=>{}}}, api=async(p,o={})=>{o.headers={...(o.headers||{}),'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})};let r=await fetch(A+p,o);let d=await r.json();return[r.status,d]};
function modal(id,v){document.getElementById(id).classList.toggle('show',v)}
async function boot(){if(!token){modal('auth-modal',true);return}let r=await api('/auth/me');if(r[0]!=200){token=null;localStorage.removeItem('huddle.token');modal('auth-modal',true);return}me=r[1].user;modal('auth-modal',false);let w=await api('/workspaces');if(!w[1].workspaces.length){modal('workspace-create-form',true);render();return}slug=w[1].workspaces[0].slug;workspaceName=w[1].workspaces[0].name;let x=await api('/workspaces/'+slug);channels=x[1].channels;render();if(channels[0])open(channels[0].id,channels[0].name)}
function render(){$('current-user').textContent=me?me.display_name:'';$('workspace-header').textContent=workspaceName||slug||'Huddle';$('channel-list').innerHTML=channels.map(c=>`<div class=channel data-testid=channel-entry data-channel-id="${c.id}" data-channel-name="${c.name}"># ${c.name}</div>`).join('');document.querySelectorAll('[data-testid=channel-entry]').forEach(e=>e.onclick=()=>open(+e.dataset.channelId,e.dataset.channelName))}
async function open(id,name){cur=id;$('channel-title').textContent=name;let r=await api('/channels/'+id+'/messages');let ms=r[1].messages||[];$('message-list').innerHTML=ms.length?ms.reverse().map(row).join(''):'<div data-testid=empty-channel class=empty>No messages yet — start the conversation.</div>';document.getElementById('message-form').onsubmit=async e=>{e.preventDefault();let b=e.target.body.value;if(!b.trim())return;await api('/channels/'+id+'/messages',{method:'POST',body:JSON.stringify({body:b})});e.target.reset();open(id,name)}}
function row(m){let rs=(m.reactions||[]).map(r=>`<button data-testid=reaction-chip onclick="toggleReaction(${m.id},'${r.emoji}')">${r.emoji} ${r.count}</button>`).join('');return `<div class=row data-testid=message data-mid="${m.id}"><span data-testid=message-avatar>●</span> <b data-testid=message-author>${m.author.display_name}</b> <small data-testid=message-time>${new Date(m.created_at).toLocaleString()}</small><div data-testid=message-body>${m.body}</div>${m.edited_at?'<span data-testid=edited-indicator> (edited)</span>':''}<span data-testid=reply-count>${m.reply_count||0} replies</span><button data-testid=open-thread-btn onclick="thread(${m.id})">Reply</button><button data-testid=reaction-button onclick="picker(${m.id})">😀</button><button data-testid=edit-message-btn onclick="editmsg(${m.id},this)">Edit</button><button data-testid=delete-message-btn onclick="delmsg(${m.id})">Delete</button><div data-testid=emoji-picker class=picker hidden style="display:grid">${['👍','🎉','❤️','😂','😮','🚀','✅','🔥'].map(e=>`<button data-testid=emoji-option onclick="toggleReaction(${m.id},'${e}')">${e}</button>`).join('')}</div>${rs}</div>`}
$('auth-toggle').onclick=()=>{let f=$('auth-form'),r=f.dataset.mode==='login';f.dataset.mode=r?'register':'login';$('auth-submit').textContent=r?'Register':'Sign in';$('auth-toggle').textContent=r?'Sign in':'Create account'};
$('auth-form').onsubmit=async e=>{e.preventDefault();let x=Object.fromEntries(new FormData(e.target));let r=await api(e.target.dataset.mode==='register'?'/auth/register':'/auth/login',{method:'POST',body:JSON.stringify(x)});if(r[0]>=400){$('auth-error').textContent=r[1].error||'Invalid input';return}token=r[1].token;me=r[1].user;localStorage.setItem('huddle.token',token);modal('auth-modal',false);render();boot()};
$('workspace-create-form').onsubmit=async e=>{e.preventDefault();let r=await api('/workspaces',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(e.target)))});if(r[0]==201){slug=r[1].workspace.slug;workspaceName=r[1].workspace.name;modal('workspace-create-form',false);let x=await api('/workspaces/'+slug);channels=x[1].channels;render();open(channels[0].id,'general')}};
$('new-channel-btn').onclick=()=>{document.querySelector('[data-testid=create-channel-error]').textContent='';document.querySelector('[data-testid=create-channel-form]').reset();modal('create-channel-modal',true)};$('create-channel-cancel').onclick=()=>modal('create-channel-modal',false);
$('create-channel-form').onsubmit=async e=>{e.preventDefault();let x=Object.fromEntries(new FormData(e.target));x.is_private=!!e.target.is_private.checked;let r=await api('/workspaces/'+slug+'/channels',{method:'POST',body:JSON.stringify(x)});if(r[0]>=400){document.querySelector('[data-testid=create-channel-error]').textContent=r[1].error||'Could not create';return}let z=await api('/workspaces/'+slug);channels=z[1].channels;render();modal('create-channel-modal',false);open(r[1].channel.id,x.name)};
$('logout-btn').onclick=()=>{localStorage.removeItem('huddle.token');token=null;slug=null;workspaceName='';channels=[];let f=$('auth-form');f.dataset.mode='login';$('auth-submit').textContent='Sign in';$('auth-toggle').textContent='Create account';$('auth-error').textContent='';modal('auth-modal',true)};
async function editmsg(id,btn){let r=btn.closest('[data-testid=message]'),i=document.createElement('input');i.setAttribute('data-testid','edit-input');i.value=r.querySelector('[data-testid=message-body]').textContent;r.querySelector('[data-testid=message-body]').replaceWith(i);i.focus();i.onkeydown=async e=>{if(e.key==='Enter'){await api('/messages/'+id,{method:'PATCH',body:JSON.stringify({body:i.value})});open(cur,$('channel-title').textContent)}}}
async function delmsg(id){await api('/messages/'+id,{method:'DELETE'});open(cur,$('channel-title').textContent)}
function picker(id){document.querySelector('[data-mid="'+id+'"] [data-testid=emoji-picker]').hidden=false}
async function toggleReaction(id,emoji){let rowel=document.querySelector('[data-mid="'+id+'"]'),pk=rowel&&rowel.querySelector('[data-testid=emoji-picker]');if(pk){pk.hidden=true;pk.style.display='none'}let chip=[...document.querySelectorAll('[data-mid="'+id+'"] [data-testid=reaction-chip]')].find(x=>x.textContent.includes(emoji));await api('/messages/'+id+'/reactions'+(chip?'/'+encodeURIComponent(emoji):''),{method:chip?'DELETE':'POST',body:chip?undefined:JSON.stringify({emoji})});open(cur,$('channel-title').textContent)}
async function thread(id){let p=document.getElementById('thread-panel');p.hidden=false;p.style.display='block';p.innerHTML='<h3>Thread</h3><div data-testid=thread-messages></div><input data-testid=thread-input><button data-testid=thread-send>Send</button><button data-testid=close-thread>Close</button>';let q=await api('/messages/'+id+'/replies');p.querySelector('[data-testid=thread-messages]').innerHTML=(q[1].replies||[]).map(m=>'<div data-testid=message><div data-testid=message-body>'+m.body+'</div></div>').join('');p.querySelector('[data-testid=close-thread]').onclick=()=>{p.hidden=true;p.style.display='none'};p.querySelector('[data-testid=thread-send]').onclick=async()=>{let v=p.querySelector('[data-testid=thread-input]').value;if(v.trim()){await api('/channels/'+cur+'/messages',{method:'POST',body:JSON.stringify({body:v,parent_id:id})});p.querySelector('[data-testid=thread-input]').value='';let q2=await api('/messages/'+id+'/replies');p.querySelector('[data-testid=thread-messages]').innerHTML=(q2[1].replies||[]).map(m=>'<div data-testid=message><div data-testid=message-body>'+m.body+'</div></div>').join('');open(cur,$('channel-title').textContent)}}}
$('close-thread').onclick=()=>document.getElementById('thread-panel').hidden=true;window.liveTimer=setInterval(async()=>{if(!cur)return;let z=await api('/channels/'+cur+'/messages');let mm=z[1].messages||[];$('message-list').innerHTML=mm.length?mm.reverse().map(row).join(''):'<div data-testid=empty-channel class=empty>No messages yet — start the conversation.</div>'},1200);if(token)boot();else modal('auth-modal',true);
</script></body></html>
'''
if __name__=='__main__': ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
