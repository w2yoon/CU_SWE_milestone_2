import os,json,re,sqlite3,urllib.parse,base64,hashlib,struct,time,threading,socket,datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from core import *
NODE=int(os.getenv('NODE_ID','0')); PORT=int(os.getenv('PORT','8000'))
def js(h,o,code=200):
    b=json.dumps(o,separators=(',',':')).encode(); h.send_response(code); h.send_header('Content-Type','application/json'); h.send_header('Content-Length',str(len(b))); h.end_headers(); h.wfile.write(b)
def err(h,code,msg='error'): js(h,{'error':msg},code)
class H(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*x): pass
    def multipart(self):
        ct=self.headers.get('Content-Type','')
        if 'multipart/form-data' not in ct:return None
        b=ct.split('boundary=',1)[-1].strip().strip('"')
        raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
        marker=('--'+b).encode(); parts={}
        for part in raw.split(marker):
            if b'Content-Disposition' not in part: continue
            head,_,val=part.partition(b'\r\n\r\n')
            if not val: head,_,val=part.partition(b'\n\n')
            m=re.search(br'name="([^"]+)"(?:; filename="([^"]*)")?',head)
            if not m: continue
            val=val.rstrip(b'\r\n-')
            parts[m.group(1).decode()]={'filename':(m.group(2) or b'').decode(),'data':val,'content_type':(re.search(br'Content-Type:\s*([^\r\n]+)',head,re.I) or [b'',b'application/octet-stream'])[1].decode()}
        return parts
    def body(self):
        try:
            n=int(self.headers.get('Content-Length','0')); x=json.loads(self.rfile.read(n) or b'{}')
            return x if isinstance(x,dict) else None
        except: return None
    def user(self,c,required=True):
        a=self.headers.get('Authorization','')
        u=auth(c,a[7:] if a.startswith('Bearer ') else '')
        if required and not u: err(self,401,'unauthorized'); return None
        return u
    def route(self):
        return urllib.parse.urlsplit(self.path).path.strip('/').split('/')
    def send(self,o,code=200): js(self,o,code)
    def do_GET(self):
        if urllib.parse.urlsplit(self.path).path == '/':
            try:
                data=open('/app/static/index.html','rb').read()
                self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            except Exception: self.send_error(500)
            return
        c=db(); p=self.route(); q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        try:
            if p==['api','health']: return self.send({'status':'ok','node_id':NODE})
            if p[:2]==['api','ws']: return self.websocket(c,urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('token',[''])[0])
            u=self.user(c)
            if not u:return
            if p==['api','auth','me']: return self.send({'user':user_obj(u)})
            if p[:2]==['api','users'] and len(p)==3:
                r=c.execute('SELECT * FROM users WHERE id=?',(p[2],)).fetchone()
                return self.send({'user':user_obj(r)} if r else {'error':'not found'},200 if r else 404)
            if p==['api','workspaces']:
                rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall()
                return self.send({'workspaces':[dict(x) for x in rows]})
            if len(p)==3 and p[:2]==['api','workspaces']:
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:return err(self,404)
                mem=c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not mem:return err(self,403)
                ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?) AND (is_private=0 OR EXISTS(SELECT 1 FROM channel_members cm WHERE cm.channel_id=channels.id AND cm.user_id=?))',(w['id'],q.get('include_archived',['0'])[0] in ('1','true'),u['id'])).fetchall()
                rs=[]
                for x in ch:
                    z=c.execute('SELECT last_read_event_id FROM read_state WHERE channel_id=? AND user_id=?',(x['id'],u['id'])).fetchone(); cur=z[0] if z else 0
                    evs=c.execute('SELECT payload_json FROM events WHERE channel_id=? AND event_id>? AND type IN ("message.created","message.reply")',(x['id'],cur)).fetchall()
                    unread=mention=0
                    for e in evs:
                        try:
                            z=json.loads(e[0]); m=z.get('message',z)
                            if m.get('author_id')!=u['id']: unread+=1
                            if u['id'] in (m.get('mentions') or []): mention+=1
                        except: pass
                    rs.append({'channel_id':x['id'],'last_read_event_id':cur,'unread_count':unread,'mention_count':mention})
                return self.send({'workspace':dict(w),'channels':[dict(x) for x in ch],'read_state':rs})
            if len(p)==4 and p[3]=='members' and p[:2]==['api','workspaces']:
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:return err(self,404)
                return self.send({'members':[dict(x) for x in c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],))]})
            if len(p)==4 and p[3]=='invitations':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:return err(self,404)
                role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not role or role[0] not in ('owner','admin'):return err(self,403)
                rows=c.execute('SELECT code,workspace_id,invited_user_id,max_uses,uses FROM invitations WHERE workspace_id=? AND uses<max_uses',(w['id'],)).fetchall()
                return self.send({'invitations':[dict(x) for x in rows]})
            if len(p)==4 and p[3]=='groups':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:return err(self,404)
                gs=[]
                for g in c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)):
                    gs.append({'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],))]})
                return self.send({'groups':gs})
            if len(p)==4 and p[1]=='channels' and p[3]=='messages':
                ch=channel(c,p[2])
                if not ch:return err(self,404)
                if not member(c,ch['id'],u['id']):return err(self,403)
                try:
                    lim=int(q.get('limit',['50'])[0])
                except: return err(self,400)
                if lim<1 or lim>200:return err(self,400)
                rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC',(ch['id'],)).fetchall()
                cur=q.get('cursor',[None])[0]
                if cur is not None:
                    try: start=int(cur)
                    except: return err(self,400)
                    rows=[x for x in rows if x['id']<start]
                page=rows[:lim]
                nxt=str(page[-1]['id']) if len(rows)>lim and page else None
                return self.send({'messages':[msg_obj(c,x) for x in page],'next_cursor':nxt})
            if len(p)==4 and p[1]=='channels' and p[3]=='members':
                ch=channel(c,p[2])
                if not ch:return err(self,404)
                if not member(c,ch['id'],u['id']):return err(self,403)
                ids=c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch['id'],)).fetchall()
                return self.send({'members':[user_obj(c.execute('SELECT * FROM users WHERE id=?',(x[0],)).fetchone()) for x in ids]})
            if len(p)==4 and p[1]=='channels' and p[3]=='pins':
                ch=channel(c,p[2])
                if not ch:return err(self,404)
                if ch['is_private'] and not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone():return err(self,403)
                if not member(c,ch['id'],u['id']):return err(self,403)
                try: lim=int(q.get('limit',['50'])[0])
                except: return err(self,400)
                if lim<1 or lim>200:return err(self,400)
                rows=c.execute('SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? ORDER BY p.message_id DESC',(ch['id'],)).fetchall()
                cur=q.get('cursor',[None])[0]
                if cur:
                    try: rows=[x for x in rows if x['message_id']<int(cur)]
                    except: return err(self,400)
                page=rows[:lim]; out=[{'message':msg_obj(c,x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in page]
                return self.send({'pins':out,'next_cursor':str(page[-1]['message_id']) if len(rows)>lim and page else None})
            if len(p)==4 and p[1]=='channels' and p[3]=='read':
                ch=channel(c,p[2])
                if not ch:return err(self,404)
                if not member(c,ch['id'],u['id']):return err(self,403)
                r=c.execute('SELECT last_read_event_id FROM read_state WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); cur=r[0] if r else 0
                evs=c.execute('SELECT payload_json FROM events WHERE channel_id=? AND event_id>? AND type IN ("message.created","message.reply")',(ch['id'],cur)).fetchall()
                unread=mention=0
                for e in evs:
                    try:
                        z=json.loads(e[0]); m=z.get('message',z)
                        if m.get('author_id')!=u['id']: unread+=1
                        if u['id'] in (m.get('mentions') or []): mention+=1
                    except: pass
                return self.send({'read_state':{'channel_id':ch['id'],'last_read_event_id':cur,'unread_count':unread,'mention_count':mention}})
            if len(p)==4 and p[1]=='messages' and p[3]=='replies':
                r=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(p[2],)).fetchone()
                if not r:return err(self,404)
                if not member(c,r['channel_id'],u['id']):return err(self,403)
                try: lim=int(q.get('limit',['50'])[0])
                except: return err(self,400)
                if lim<1 or lim>200:return err(self,400)
                rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(p[2],)).fetchall()
                cur=q.get('cursor',[None])[0]
                if cur:
                    try: rows=[x for x in rows if x['id']>int(cur)]
                    except: return err(self,400)
                page=rows[:lim]
                return self.send({'replies':[msg_obj(c,x) for x in page],'next_cursor':str(page[-1]['id']) if len(rows)>lim and page else None})
            if p[:2]==['api','search']:
                text=q.get('q',[''])[0]; args=['%'+text+'%']; where='m.deleted=0 AND m.body LIKE ?'
                if q.get('workspace_id'):
                    where+=' AND ch.workspace_id=?'; args.append(q['workspace_id'][0])
                rows=c.execute('SELECT m.* FROM messages m JOIN channels ch ON ch.id=m.channel_id JOIN channel_members cm ON cm.channel_id=ch.id AND cm.user_id=? WHERE '+where+' ORDER BY m.id DESC',[u['id']]+args).fetchall()
                return self.send({'results':[msg_obj(c,x) for x in rows],'next_cursor':None})
            if len(p)==4 and p[1]=='files' and p[3]=='download':
                r=c.execute('SELECT * FROM files WHERE id=?',(p[2],)).fetchone()
                if not r:return err(self,404)
                self.send_response(200);self.send_header('Content-Type',r['content_type']);self.send_header('Content-Disposition','attachment; filename="'+r['filename']+'"');self.send_header('Content-Length',str(r['size']));self.end_headers();self.wfile.write(r['data']);return
            if len(p)==3 and p[1]=='files':
                r=c.execute('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE id=?',(p[2],)).fetchone()
                return self.send({'file':dict(r)} if r else {'error':'not found'},200 if r else 404)
            return err(self,404)
        except Exception as e: err(self,500,str(e))
        finally:c.close()
    def do_POST(self):
        c=db(); p=self.route()
        try:
            if p==['api','auth','register']:
                d=self.body()
                if not d or not re.fullmatch(r'[A-Za-z0-9_]+',d.get('username','')) or len(d.get('password',''))<8:return err(self,400)
                if c.execute('SELECT 1 FROM users WHERE username=?',(d['username'],)).fetchone():return err(self,409)
                c.execute('INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)',(d['username'],hashlib.sha256(d['password'].encode()).hexdigest(),d.get('display_name') or d['username'],now())); uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; t=token(c,uid);c.commit()
                return self.send({'user':user_obj(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()),'token':t},201)
            if p==['api','auth','login']:
                d=self.body(); r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(d.get('username',''),hashlib.sha256(d.get('password','').encode()).hexdigest())).fetchone()
                if not r:return err(self,401)
                t=token(c,r['id']);c.commit();return self.send({'user':user_obj(r),'token':t})
            u=self.user(c)
            if not u:return
            if p==['api','files']:
                parts=self.multipart()
                if not parts or 'file' not in parts:return err(self,400)
                f=parts['file']; data=f['data']
                if len(data)>10*1024*1024:return err(self,413)
                if not f['filename']:return err(self,400)
                c.execute('INSERT INTO files(uploader_id,filename,content_type,size,data,created_at) VALUES(?,?,?,?,?,?)',(u['id'],f['filename'],f['content_type'],len(data),data,now()))
                fid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.commit()
                return self.send({'file':dict(c.execute('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE id=?',(fid,)).fetchone())},201)
            d=self.body()
            if p==['api','workspaces']:
                if not d or not re.fullmatch(r'[a-z0-9-]{2,32}',d.get('slug','')):return err(self,400)
                if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(d['slug'],)).fetchone():return err(self,409)
                c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d.get('name',''),u['id'])); wid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
                c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'));c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')); ch=c.execute('SELECT * FROM channels WHERE id=?',(c.execute('SELECT last_insert_rowid()').fetchone()[0],)).fetchone();c.execute('INSERT INTO channel_members VALUES(?,?)',(ch['id'],u['id']));c.commit()
                return self.send({'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':dict(ch)},201)
            if len(p)==4 and p[1]=='workspaces' and p[3]=='groups':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:return err(self,404)
                if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone():return err(self,403)
                if not d or not re.fullmatch(r'[a-z0-9_-]{2,32}',d.get('handle','')) or not d.get('name'):return err(self,400)
                if c.execute('SELECT 1 FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],d['handle'])).fetchone():return err(self,409)
                ids=d.get('user_ids',[])
                if not isinstance(ids,list):return err(self,400)
                c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],d['handle'],d['name'],u['id']))
                gid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
                for uid in ids:
                    if c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],uid)).fetchone():c.execute('INSERT OR IGNORE INTO group_members VALUES(?,?)',(gid,uid))
                c.commit()
                return self.send({'group':{'id':gid,'handle':d['handle'],'name':d['name'],'member_user_ids':[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(gid,))]}},201)
            if len(p)==4 and p[1]=='workspaces' and p[3]=='channels':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:return err(self,404)
                role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not role:return err(self,403)
                if role[0]=='guest' or (d.get('is_private') and role[0] not in ('owner','admin')):return err(self,403)
                if not d or not re.fullmatch(r'[a-z0-9-]{1,32}',d.get('name','')) or len(d.get('topic',''))>250:return err(self,400)
                if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],d['name'])).fetchone():return err(self,409)
                c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],d['name'],int(bool(d.get('is_private'))),d.get('topic',''))); ch=c.execute('SELECT * FROM channels WHERE id=?',(c.execute('SELECT last_insert_rowid()').fetchone()[0],)).fetchone();c.execute('INSERT INTO channel_members VALUES(?,?)',(ch['id'],u['id']));c.commit();return self.send({'channel':dict(ch)},201)
            if len(p)==4 and p[1]=='channels' and p[3]=='join':
                ch=channel(c,p[2])
                if not ch:return err(self,404)
                if ch['is_private']:return err(self,403)
                w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
                rr=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if rr and rr[0]=='guest':return err(self,403)
                if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone():
                    if w['join_mode']!='open':return err(self,403)
                    c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w['id'],u['id'],'member'))
                cur=c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
                if cur.rowcount: emit(c,ch['id'],'member.joined',{'user_id':u['id']})
                c.commit();return self.send({'channel':dict(ch)})
            if len(p)==4 and p[1]=='channels' and p[3]=='leave':
                cur=c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(p[2],u['id']))
                if cur.rowcount: emit(c,int(p[2]),'member.left',{'user_id':u['id']})
                c.commit();return self.send({'channel':dict(channel(c,p[2]))})
            if len(p)==4 and p[1]=='channels' and p[3] in ('archive','unarchive'):
                ch=channel(c,p[2])
                if not ch:return err(self,404)
                w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
                if w['owner_id']!=u['id']:return err(self,403)
                val=1 if p[3]=='archive' else 0
                c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id']))
                emit(c,ch['id'],'channel.archived' if val else 'channel.unarchived',dict(ch));c.commit()
                return self.send({'channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone())})
            if len(p)==4 and p[1]=='channels' and p[3]=='messages':
                ch=channel(c,p[2])
                if not ch:return err(self,404)
                if not member(c,ch['id'],u['id']):return err(self,403)
                body=d.get('body','')
                if ch['is_archived'] and body != '/unarchive':return err(self,423)
                if not isinstance(body,str) or not body.strip():return err(self,400)
                if body.startswith('/') and not body.startswith('//'):
                    cmd,*rest=body.split(' ',1); arg=rest[0].strip() if rest else ''
                    if cmd=='/me' and not arg:return err(self,400)
                    if cmd=='/shrug': body=(arg+' ¯\\_(ツ)_/¯').strip()
                    elif cmd=='/topic':
                        if not arg or len(arg)>250:return err(self,400)
                        c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,ch['id']));c.commit()
                        return self.send({'message':None,'channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone())},201)
                    elif cmd=='/invite':
                        m=re.fullmatch(r'@([A-Za-z0-9_]+)',arg); target=c.execute('SELECT * FROM users WHERE username=?',(m.group(1),)).fetchone() if m else None
                        if not target:return err(self,404)
                        c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],target['id']));c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(ch['workspace_id'],target['id'],'member'));c.commit()
                        return self.send({'message':None,'invited_user':user_obj(target)},201)
                    elif cmd in ('/archive','/unarchive'):
                        w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
                        if not w or w['owner_id']!=u['id']:return err(self,403)
                        val=1 if cmd=='/archive' else 0;c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id']));c.commit()
                        out=dict(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone());out['is_archived']=bool(out['is_archived'])
                        return self.send({'message':None,'channel':out},201)
                    elif cmd not in ('/me',):return err(self,400)
                fids=d.get('file_ids',[])
                if not isinstance(fids,list):return err(self,400)
                for fid in fids:
                    f=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
                    if not f or f['uploader_id']!=u['id'] or c.execute('SELECT 1 FROM message_files WHERE file_id=?',(fid,)).fetchone():return err(self,400)
                c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch['id'],u['id'],body,d.get('parent_id'),now()))
                mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
                for fid in fids:c.execute('INSERT INTO message_files VALUES(?,?)',(mid,fid))
                r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); ev=emit(c,ch['id'],'message.reply' if r['parent_id'] else 'message.created',msg_obj(c,r));c.commit();return self.send({'message':msg_obj(c,r)},201)
            if len(p)==4 and p[1]=='messages' and p[3]=='pin':
                r=c.execute('SELECT * FROM messages WHERE id=?',(p[2],)).fetchone()
                if not r:return err(self,404)
                if not member(c,r['channel_id'],u['id']):return err(self,403)
                c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(r['id'],u['id'],now()));c.commit();return self.send({'pin':{'message_id':r['id'],'pinned_by':u['id'],'pinned_at':c.execute('SELECT pinned_at FROM pins WHERE message_id=?',(r['id'],)).fetchone()[0]}})
            if len(p)==4 and p[1]=='messages' and p[3]=='reactions':
                r=c.execute('SELECT * FROM messages WHERE id=?',(p[2],)).fetchone()
                if not r:return err(self,404)
                ch=channel(c,r['channel_id'])
                if ch['is_archived']:return err(self,423)
                if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(r['channel_id'],u['id'])).fetchone():return err(self,403)
                e=d.get('emoji','')
                if not e:return err(self,400)
                cur=c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(r['id'],u['id'],e))
                if cur.rowcount:
                    emit(c,r['channel_id'],'reaction.added',{'message_id':r['id'],'emoji':e,'user_id':u['id']})
                c.commit();return self.send({'reactions':reactions(c,r['id'])})
            if p==['api','dms']:
                if 'user_id' not in d or d.get('user_id') is None:return err(self,400)
                oid=d.get('user_id'); other=c.execute('SELECT id FROM users WHERE id=?',(oid,)).fetchone()
                if not other:return err(self,404)
                a,b=sorted((u['id'],oid)); x=c.execute('SELECT id FROM channels WHERE is_dm=1 AND name=?',(f'{a}:{b}',)).fetchone()
                if not x:
                    c.execute('INSERT INTO channels(name,is_private,is_dm) VALUES(?,1,1)',(f'{a}:{b}',));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO channel_members VALUES(?,?);', (cid,a));c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,b))
                else:cid=x[0]
                c.commit();return self.send({'channel_id':cid})
            if len(p)==5 and p[1]=='workspaces' and p[3]=='groups':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:return err(self,404)
                g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],p[4])).fetchone()
                if not g:return err(self,404)
                role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not role or (g['creator_id']!=u['id'] and role[0]!='owner'):return err(self,403)
                if 'name' not in d and 'user_ids' not in d:return err(self,400)
                if 'name' in d:
                    if not d['name']:return err(self,400)
                    c.execute('UPDATE groups_ SET name=? WHERE id=?',(d['name'],g['id']))
                if 'user_ids' in d:
                    if not isinstance(d['user_ids'],list):return err(self,400)
                    c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
                    for uid in d['user_ids']:
                        if c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],uid)).fetchone():c.execute('INSERT OR IGNORE INTO group_members VALUES(?,?)',(g['id'],uid))
                c.commit(); g=c.execute('SELECT * FROM groups_ WHERE id=?',(g['id'],)).fetchone()
                return self.send({'group':{'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],))]}})
            if len(p)==3 and p[1]=='workspaces':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:return err(self,404)
                role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not role or role[0] not in ('owner','admin'):return err(self,403)
                if 'join_mode' in d and d['join_mode'] not in ('open','invite_only'):return err(self,400)
                c.execute('UPDATE workspaces SET name=?,join_mode=? WHERE id=?',(d.get('name',w['name']),d.get('join_mode',w['join_mode']),w['id']));c.commit()
                return self.send({'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
            if len(p)==4 and p[1]=='workspaces' and p[3]=='transfer_ownership':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:return err(self,404)
                own=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not own or own[0]!='owner':return err(self,403)
                target=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],d.get('new_owner_id'))).fetchone()
                if not target or target[0]!='admin':return err(self,400)
                c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],u['id']))
                c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],d['new_owner_id']))
                c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(d['new_owner_id'],w['id']));c.commit()
                return self.send({'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
            if len(p)==4 and p[1]=='channels' and p[3]=='read':
                ch=channel(c,p[2])
                if not ch:return err(self,404)
                if not member(c,ch['id'],u['id']):return err(self,403)
                val=d.get('last_read_event_id')
                if not isinstance(val,int):return err(self,400)
                old=c.execute('SELECT last_read_event_id FROM read_state WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
                val=max(val,old[0] if old else 0)
                c.execute('INSERT OR REPLACE INTO read_state VALUES(?,?,?)',(ch['id'],u['id'],val));c.commit()
                return self.send({'read_state':{'channel_id':ch['id'],'last_read_event_id':val,'unread_count':0,'mention_count':0}})
            if len(p)==4 and p[1]=='workspaces' and p[3]=='invitations':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:return err(self,404)
                role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not role or role[0] not in ('owner','admin'):return err(self,403)
                if not isinstance(d.get('max_uses',1),int) or d.get('max_uses',1)<1:return err(self,400)
                code=secrets.token_urlsafe(12); target=None; target_name=None
                if d.get('invited_user_id') is not None: target=d['invited_user_id']
                elif d.get('invited_username'):
                    target_name=d['invited_username'].lstrip('@')
                    z=c.execute('SELECT id FROM users WHERE username=?',(target_name,)).fetchone()
                    if z: target=z[0]
                mx=1 if (target is not None or target_name) else d.get('max_uses',1)
                exp=None
                if isinstance(d.get('expires_in_seconds'),int): exp=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=d['expires_in_seconds'])).isoformat()
                c.execute('INSERT INTO invitations(code,workspace_id,invited_user_id,expires_at,max_uses,uses,invited_username) VALUES(?,?,?,?,?,?,?)',(code,w['id'],target,exp,mx,0,target_name));c.commit()
                return self.send({'invitation':{'code':code,'workspace_id':w['id'],'max_uses':mx}},201)
            if len(p)==4 and p[1]=='invitations' and p[3]=='accept':
                inv=c.execute('SELECT * FROM invitations WHERE code=?',(p[2],)).fetchone()
                if not inv or inv['uses']>=inv['max_uses'] or (inv['expires_at'] and inv['expires_at']<datetime.datetime.now(datetime.timezone.utc).isoformat()):return err(self,404)
                if inv['invited_user_id'] and inv['invited_user_id']!=u['id']:return err(self,403)
                if inv['invited_username'] and inv['invited_username']!=u['username']:return err(self,403)
                c.execute('UPDATE invitations SET uses=uses+1 WHERE code=? AND uses<max_uses',(p[2],))
                if c.total_changes==0:return err(self,404)
                c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(inv['workspace_id'],u['id'],'member'));c.commit()
                return self.send({'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(inv['workspace_id'],)).fetchone())})
            if len(p)==4 and p[1]=='channels' and p[3] in ('archive','unarchive'):
                ch=channel(c,p[2])
                if not ch:return err(self,404)
                w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
                if w['owner_id']!=u['id']:return err(self,403)
                val=1 if p[3]=='archive' else 0
                c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id']))
                emit(c,ch['id'],'channel.archived' if val else 'channel.unarchived',dict(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()))
                c.commit();return self.send({'channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone())})
            return err(self,404)
        except Exception as e:err(self,500,str(e))
        finally:c.close()
    def do_PATCH(self):
        c=db();p=self.route()
        try:
            u=self.user(c)
            if not u:return
            d=self.body()
            if len(p)==3 and p[1]=='workspaces':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:return err(self,404)
                role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not role or role[0] not in ('owner','admin'):return err(self,403)
                if not isinstance(d,dict):return err(self,400)
                if 'join_mode' in d and d['join_mode'] not in ('open','invite_only'):return err(self,400)
                c.execute('UPDATE workspaces SET name=?,join_mode=? WHERE id=?',(d.get('name',w['name']),d.get('join_mode',w['join_mode']),w['id']));c.commit()
                return self.send({'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
            if len(p)==5 and p[1]=='workspaces' and p[3]=='groups':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:return err(self,404)
                g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],p[4])).fetchone()
                if not g:return err(self,404)
                role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not role or (g['creator_id']!=u['id'] and role[0]!='owner'):return err(self,403)
                if not isinstance(d,dict) or ('name' not in d and 'user_ids' not in d):return err(self,400)
                if 'name' in d:
                    if not d['name']:return err(self,400)
                    c.execute('UPDATE groups_ SET name=? WHERE id=?',(d['name'],g['id']))
                if 'user_ids' in d:
                    if not isinstance(d['user_ids'],list):return err(self,400)
                    c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
                    for uid in d['user_ids']:
                        if c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],uid)).fetchone():c.execute('INSERT OR IGNORE INTO group_members VALUES(?,?)',(g['id'],uid))
                c.commit();g=c.execute('SELECT * FROM groups_ WHERE id=?',(g['id'],)).fetchone()
                return self.send({'group':{'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],))]}})
            if len(p)==5 and p[1]=='workspaces' and p[3]=='members':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:return err(self,404)
                actor=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not str(p[4]).isdigit():return err(self,404)
                target=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],p[4])).fetchone()
                if not actor or not target:return err(self,403 if actor else 403)
                role=d.get('role')
                if role not in ('owner','admin','member','guest'):return err(self,400)
                if role=='owner':return err(self,400)
                if target[0]=='owner' or target[0]=='admin' and actor[0]!='owner' or actor[0] not in ('owner','admin'):return err(self,403)
                c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(role,w['id'],p[4]));c.commit()
                return self.send({'member':{'user_id':int(p[4]),'role':role}})
            if len(p)==4 and p[1]=='workspaces' and p[3]=='transfer_ownership':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:return err(self,404)
                own=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not own or own[0]!='owner':return err(self,403)
                target=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],d.get('new_owner_id'))).fetchone()
                if not target or target[0]!='admin':return err(self,400)
                c.execute('UPDATE members SET role=CASE WHEN user_id=? THEN ? WHEN user_id=? THEN ? ELSE role END WHERE workspace_id=?',(u['id'],'admin',d['new_owner_id'],'owner',w['id']))
                c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(d['new_owner_id'],w['id']));c.commit()
                return self.send({'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
            if len(p)==4 and p[1]=='channels' and p[3] in ('archive','unarchive'):
                ch=channel(c,p[2])
                if not ch:return err(self,404)
                w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
                if w['owner_id']!=u['id']:return err(self,403)
                val=1 if p[3]=='archive' else 0
                c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id']));emit(c,ch['id'],'channel.archived' if val else 'channel.unarchived',dict(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()));c.commit()
                return self.send({'channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone())})
            if len(p)==3 and p[1]=='channels':
                ch=channel(c,p[2])
                if not ch:return err(self,404)
                if not member(c,ch['id'],u['id']):return err(self,403)
                if 'topic' in d and len(d['topic'])>250:return err(self,400)
                c.execute('UPDATE channels SET topic=? WHERE id=?',(d.get('topic',ch['topic']),ch['id']));c.commit()
                return self.send({'channel':dict(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone())})
            if len(p)==3 and p[1]=='users' and p[2]=='me':
                allowed=('display_name','timezone','avatar_url','status_text','status_emoji')
                vals={k:d[k] for k in allowed if k in d}
                limits={'display_name':80,'status_text':140,'status_emoji':64,'avatar_url':512}
                if any(k not in allowed or not isinstance(v,str) or not v or len(v)>limits.get(k,999999) for k,v in vals.items()):return err(self,400)
                if 'timezone' in vals:
                    try: ZoneInfo(vals['timezone'])
                    except (ZoneInfoNotFoundError,ValueError): return err(self,400)
                if vals:
                    c.execute('UPDATE users SET '+','.join(k+'=?' for k in vals)+' WHERE id=?',tuple(vals.values())+(u['id'],))
                    uu=user_obj(c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone())
                    c.execute('INSERT INTO user_events(type,payload_json) VALUES(?,?)',('user.updated',json.dumps({'user':uu})))
                    c.commit()
                return self.send({'user':user_obj(c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone())})
            if len(p)==3 and p[1]=='messages':
                r=c.execute('SELECT * FROM messages WHERE id=?',(p[2],)).fetchone()
                if not r:return err(self,404)
                ch=channel(c,r['channel_id'])
                if ch and ch['is_archived']:return err(self,423)
                if r['deleted']:return err(self,409)
                if r['author_id']!=u['id']:return err(self,403)
                if not isinstance(d,dict) or not isinstance(d.get('body'),str) or not d['body'].strip():return err(self,400)
                c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(d['body'],now(),r['id']))
                r=c.execute('SELECT * FROM messages WHERE id=?',(r['id'],)).fetchone()
                ev=emit(c,r['channel_id'],'message.updated',{'message':msg_obj(c,r)});c.commit()
                return self.send({'message':msg_obj(c,r)})
            return err(self,404)
        finally:c.close()
    def do_DELETE(self):
        c=db();p=self.route()
        try:
            u=self.user(c)
            if not u:return
            if len(p)==3 and p[1]=='messages':
                r=c.execute('SELECT * FROM messages WHERE id=?',(p[2],)).fetchone()
                if not r:return err(self,404)
                ch=channel(c,r['channel_id'])
                if ch and ch['is_archived']:return err(self,423)
                w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone() if ch and ch['workspace_id'] else None
                role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone() if w else None
                if r['author_id']!=u['id'] and not (w and w['owner_id']==u['id']):return err(self,403)
                if not r['deleted']:
                    c.execute('UPDATE messages SET deleted=1 WHERE id=?',(r['id'],))
                    emit(c,r['channel_id'],'message.deleted',{'message_id':r['id']})
                    c.commit()
                return self.send({'deleted':True})
            if len(p)==5 and p[1]=='workspaces' and p[3]=='groups':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:return err(self,404)
                g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],p[4])).fetchone()
                if not g:return self.send({'deleted':True})
                role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not role or (g['creator_id']!=u['id'] and role[0]!='owner'):return err(self,403)
                c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],));c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],));c.commit()
                return self.send({'deleted':True})
            if len(p)==5 and p[1]=='workspaces' and p[3]=='invitations':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[2],)).fetchone()
                if not w:return err(self,404)
                role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
                if not role or role[0] not in ('owner','admin'):return err(self,403)
                inv=c.execute('SELECT 1 FROM invitations WHERE workspace_id=? AND code=?',(w['id'],p[4])).fetchone()
                if not inv:return err(self,404)
                c.execute('DELETE FROM invitations WHERE workspace_id=? AND code=?',(w['id'],p[4]));c.commit()
                return self.send({'deleted':True})
            if len(p)==5 and p[1]=='messages' and p[3]=='reactions':
                r=c.execute('SELECT * FROM messages WHERE id=?',(p[2],)).fetchone()
                if not r:return err(self,404)
                emoji=urllib.parse.unquote(p[4])
                cur=c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(r['id'],u['id'],emoji))
                if cur.rowcount:
                    emit(c,r['channel_id'],'reaction.removed',{'message_id':r['id'],'emoji':emoji,'user_id':u['id']})
                c.commit();return self.send({'reactions':reactions(c,r['id'])})
            if len(p)==4 and p[1]=='messages' and p[3]=='pin':
                c.execute('DELETE FROM pins WHERE message_id=?',(p[2],));c.commit();return self.send({'unpinned':True})
            return err(self,404)
        finally:c.close()
    def websocket(self,c,t):
        u=auth(c,t)
        if not u:return err(self,401)
        key=self.headers.get('Sec-WebSocket-Key',''); accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers()
        self.connection.settimeout(.25); subs={}; last={}; global_last=(c.execute('SELECT COALESCE(MAX(id),0) FROM user_events').fetchone()[0])
        c.execute('INSERT OR REPLACE INTO ws_online VALUES(?)',(u['id'],));c.commit()
        def frame(x):
            b=json.dumps(x,separators=(',',':')).encode(); n=len(b)
            h=bytes([129,n]) if n<126 else bytes([129,126])+struct.pack('!H',n)
            return h+b
        try:
            while True:
                try:
                    z=self.connection.recv(4096)
                    if not z:
                        break
                    if len(z)>6:
                        n=z[1]&127; off=6 if n<126 else 8; mask=z[2:6]; raw=bytes(z[off+i]^mask[i%4] for i in range(n)); d=json.loads(raw); ch=int(d.get('channel_id'))
                        subs[ch]=1
                        is_resume=(d.get('type')=='resume')
                        c.execute('INSERT OR REPLACE INTO ws_presence VALUES(?,?)',(ch,u['id']));c.commit()
                        if d['type']=='resume': last[ch]=int(d.get('since_event_id',0))
                        else:
                            head=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(ch,)).fetchone(); last[ch]=head[0] if head else 0;self.connection.sendall(frame({'type':'subscribed','channel_id':ch,'head_event_id':last[ch]}))
                except socket.timeout: pass
                grows=c.execute('SELECT id,type,payload_json FROM user_events WHERE id>? ORDER BY id',(global_last,)).fetchall()
                for gr in grows:
                    x=json.loads(gr['payload_json']); x['type']=gr['type']; self.connection.sendall(frame(x)); global_last=gr['id']
                for ch in list(subs):
                    rows=c.execute('SELECT event_id,type,payload_json FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,last.get(ch,0))).fetchall()
                    for r in rows:
                        x=json.loads(r['payload_json']); x.update(type=r['type'],event_id=r['event_id'],channel_id=ch);self.connection.sendall(frame(x));last[ch]=r['event_id']
                    if is_resume:
                        self.connection.sendall(frame({'type':'resumed','channel_id':ch,'head_event_id':last.get(ch,0)})); is_resume=False
        except: pass
        finally:
            try:
                for ch in list(subs):
                    c.execute('DELETE FROM ws_presence WHERE channel_id=? AND user_id=?',(ch,u['id']))
                c.execute('DELETE FROM ws_online WHERE user_id=?',(u['id'],))
                c.commit()
            except: pass
class S(ThreadingHTTPServer):
    daemon_threads=True
S(('127.0.0.1',PORT),H).serve_forever()
