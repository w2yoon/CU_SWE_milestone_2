import os,json,re,secrets,hashlib,urllib.parse,base64,hashlib,struct,threading,socket,time
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from datetime import datetime,timezone
from core import *
NODE=int(os.environ.get('NODE_ID','0')); PORT=int(os.environ.get('PORT','8000'))
def err(h,n,m): h.send_json(n,{'error':m})
def body(h):
    try:
        n=int(h.headers.get('Content-Length','0')); x=json.loads(h.rfile.read(n) or b'{}')
        return x if isinstance(x,dict) else None
    except:return None
def bearer(h):
    x=h.headers.get('Authorization','')
    return x[7:] if x.startswith('Bearer ') else ''
def multipart_file(h):
    ct=h.headers.get('Content-Type','')
    if 'multipart/form-data' not in ct or 'boundary=' not in ct:return None
    n=int(h.headers.get('Content-Length','0'))
    if n>10*1024*1024+1024:return 'TOO_BIG'
    raw=h.rfile.read(n); boundary=ct.split('boundary=',1)[1].strip().strip('"').encode()
    for part in raw.split(b'--'+boundary):
        if b'Content-Disposition:' not in part:continue
        head,data=part.split(b'\r\n\r\n',1)
        if b'name="file"' not in head:continue
        data=data.rstrip(b'\r\n-')
        if len(data)>10*1024*1024:return 'TOO_BIG'
        m=re.search(br'filename="([^"]*)"',head); filename=(m.group(1).decode(errors='ignore') if m else '')
        m=re.search(br'Content-Type:\s*([^\r\n]+)',head,re.I); typ=(m.group(1).decode().strip() if m else 'application/octet-stream')
        return filename,typ,data
    return None
class H(BaseHTTPRequestHandler):
    def log_message(self,*x): pass
    def send_json(self,n,x):
        b=json.dumps(x).encode();self.send_response(n);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def auth(self,c):
        u=token_user(c,bearer(self)); 
        if not u: err(self,401,'unauthorized')
        return u
    def parse_path(self):
        q=urllib.parse.urlsplit(self.path);return q.path.split('/'),urllib.parse.parse_qs(q.query)
    def do_GET(self):
        c=db(); p,q=self.parse_path()
        try:
            if p==[''] or p==['','']:
                b=open('/app/index.html','rb').read();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
            if p==['','api','health']: return self.send_json(200,{'status':'ok','node_id':NODE})
            if p[:3]==['','api','ws']: return self.ws(c)
            u=self.auth(c)
            if not u:return
            if p[:4]==['','api','auth','me']:return self.send_json(200,{'user':user_obj(u)})
            if p[:3]==['','api','users'] and len(p)==4:
                r=c.execute('SELECT * FROM users WHERE id=?',(int(p[3]),)).fetchone()
                return self.send_json(200 if r else 404,{'user':user_obj(r)} if r else {'error':'not found'})
            if len(p)==4 and p[1:3]==['api','files']:
                try: fid=int(p[3])
                except:return err(self,404,'not found')
                f=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
                if not f:return err(self,404,'not found')
                return self.send_json(200,{'file':{k:f[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}})
            if len(p)==5 and p[1:3]==['api','files'] and p[4]=='download':
                try: fid=int(p[3])
                except:return err(self,404,'not found')
                f=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
                if not f:return err(self,404,'not found')
                self.send_response(200);self.send_header('Content-Type',f['content_type']);self.send_header('Content-Disposition','attachment; filename="'+f['filename']+'"');self.send_header('Content-Length',str(f['size']));self.end_headers();self.wfile.write(f['data']);return
            if p==['','api','workspaces']:
                a=[]
                for r in c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)):a.append(ws_obj(r))
                return self.send_json(200,{'workspaces':a})
            if len(p)==4 and p[:3]==['','api','workspaces'] and p[3]:
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
                if not w:return err(self,404,'not found')
                include = q.get("include_archived", ["0"])[0] in ("1", "true")
                cs=[ch_obj(x) for x in c.execute("SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?) AND (is_private=0 OR EXISTS (SELECT 1 FROM channel_members cm WHERE cm.channel_id=channels.id AND cm.user_id=?))",(w["id"],int(include),u["id"]))]
                return self.send_json(200,{'workspace':ws_obj(w),'channels':cs,'read_state':[]})
            if len(p)==5 and p[1:3]==['api','workspaces'] and p[4]=='members':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
                if not w:return err(self,404,'not found')
                a=[]
                for m in c.execute('SELECT m.*,u.username,u.display_name FROM members m JOIN users u ON u.id=m.user_id WHERE workspace_id=?',(w['id'],)):a.append(dict(m))
                return self.send_json(200,{'members':a})
            if len(p)==5 and p[1:3]==['api','channels'] and p[4]=='members':
                a=[]
                for x in c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(int(p[3]),)):a.append(user_obj(x))
                return self.send_json(200,{'members':a})
            if len(p)==5 and p[1:3]==['api','channels'] and p[4]=='read':
                try: cid=int(p[3])
                except:return err(self,404,'not found')
                ch=accessible(c,u['id'],cid)
                if not ch:return err(self,404,'not found')
                row=c.execute('SELECT last_read_event_id FROM reads WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone()
                last=row['last_read_event_id'] if row else 0
                head=c.execute('SELECT COALESCE(last_event_id,0) FROM channel_seq WHERE channel_id=?',(cid,)).fetchone()
                head=head[0] if head else 0
                unread=c.execute('SELECT count(*) FROM events e JOIN messages m ON m.id=json_extract(e.payload_json,"$.id") WHERE e.channel_id=? AND e.event_id>? AND m.author_id!=? AND e.type IN ("message.created","message.reply")',(cid,last,u['id'])).fetchone()[0]
                mention=0
                for er in c.execute('SELECT payload_json FROM events WHERE channel_id=? AND event_id>? AND type IN ("message.created","message.reply")',(cid,last)):
                    try:
                        if u['id'] in json.loads(er[0]).get('mentions',[]):mention+=1
                    except:pass
                return self.send_json(200,{'read_state':{'channel_id':cid,'last_read_event_id':last,'unread_count':unread,'mention_count':mention}})
            if len(p)>=5 and p[1:3]==['api','channels'] and p[4]=='messages':
                cid=int(p[3]); ch=accessible(c,u['id'],cid)
                if not ch:
                    raw=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
                    return err(self,403,'forbidden') if raw and raw['is_dm'] else err(self,404,'not found')
                try:
                    lim=int(q.get('limit',['50'])[0])
                    if lim<1 or lim>200: return err(self,400,'invalid limit')
                except: return err(self,400,'invalid limit')
                cur=q.get('cursor',[None])[0]
                if cur is not None:
                    try: boundary=int(base64.urlsafe_b64decode(cur.encode()+b'===').decode())
                    except: return err(self,400,'invalid cursor')
                    rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 AND id<? ORDER BY id DESC LIMIT ?',(cid,boundary,lim+1)).fetchall()
                else:
                    rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 ORDER BY id DESC LIMIT ?',(cid,lim+1)).fetchall()
                more=len(rows)>lim; rows=rows[:lim]
                nxt=base64.urlsafe_b64encode(str(rows[-1]['id']).encode()).decode().rstrip('=') if more and rows else None
                return self.send_json(200,{'messages':[message_obj(c,x) for x in rows],'next_cursor':nxt})
            if len(p)==5 and p[1:3]==['api','channels'] and p[4]=='read':
                try: cid=int(p[3])
                except:return err(self,404,'not found')
                ch=accessible(c,u['id'],cid)
                if not ch:return err(self,404,'not found')
                d=body(self) or {}
                try: new=int(d.get('last_read_event_id'))
                except:return err(self,400,'invalid')
                row=c.execute('SELECT last_read_event_id FROM reads WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone()
                old=row['last_read_event_id'] if row else 0
                if new<old:new=old
                c.execute('INSERT INTO reads(user_id,channel_id,last_read_event_id) VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=excluded.last_read_event_id',(u['id'],cid,new));c.commit()
                return self.send_json(200,{'read_state':{'channel_id':cid,'last_read_event_id':new,'unread_count':0,'mention_count':0}})
            if len(p)==5 and p[1:3]==['api','messages'] and p[4]=='replies':
                try:
                    lim=int(q.get('limit',['50'])[0])
                    if lim<1 or lim>200:return err(self,400,'invalid limit')
                except:return err(self,400,'invalid limit')
                cur=q.get('cursor',[None])[0]
                try: parent=int(p[3])
                except:return err(self,404,'not found')
                if cur is None: rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id LIMIT ?',(parent,lim+1)).fetchall()
                else:
                    try: boundary=int(base64.urlsafe_b64decode(cur.encode()+b'===').decode())
                    except:return err(self,400,'invalid cursor')
                    rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 AND id>? ORDER BY id LIMIT ?',(parent,boundary,lim+1)).fetchall()
                more=len(rows)>lim;rows=rows[:lim]
                nxt=base64.urlsafe_b64encode(str(rows[-1]['id']).encode()).decode().rstrip('=') if more and rows else None
                return self.send_json(200,{'replies':[message_obj(c,x) for x in rows],'next_cursor':nxt})
            if len(p)==5 and p[1:3]==['api','channels'] and p[4]=='pins':
                try: cid=int(p[3])
                except:return err(self,404,'not found')
                ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
                if not ch:return err(self,404,'not found')
                if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone():return err(self,403,'forbidden')
                try: lim=int(q.get('limit',['50'])[0])
                except:return err(self,400,'invalid limit')
                if lim<1 or lim>200:return err(self,400,'invalid limit')
                rows=c.execute('SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? ORDER BY p.pinned_at DESC LIMIT ?',(cid,lim+1)).fetchall()
                more=len(rows)>lim;rows=rows[:lim]
                return self.send_json(200,{'pins':[{'message':message_obj(c,x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rows],'next_cursor':('x' if more else None)})
            if len(p)==3 and p[1:3]==['api','search']:
                term=q.get('q',[''])[0]; ws=q.get('workspace_id',[None])[0]
                rows=c.execute('SELECT m.*,ch.workspace_id FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.deleted=0 AND m.body LIKE ? ORDER BY m.id DESC',(f'%{term}%',)).fetchall()
                out=[]
                for r in rows:
                    if ws is not None and str(r['workspace_id'])!=str(ws):continue
                    if accessible(c,u['id'],r['channel_id']):out.append(message_obj(c,r))
                return self.send_json(200,{'results':out,'next_cursor':None})
            if len(p)==5 and p[1:3]==['api','workspaces'] and p[4]=='transfer_ownership':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
                if not w:return err(self,404,'not found')
                if member_role(c,w['id'],u['id'])!='owner':return err(self,403,'forbidden')
                d=body(self); target=d.get('new_owner_id') if d else None
                if not isinstance(target,int):return err(self,400,'invalid')
                if member_role(c,w['id'],target)!='admin':return err(self,400,'invalid')
                c.execute('UPDATE members SET role="admin" WHERE workspace_id=? AND user_id=?',(w['id'],u['id']))
                c.execute('UPDATE members SET role="owner" WHERE workspace_id=? AND user_id=?',(w['id'],target))
                c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id']));c.commit()
                return self.send_json(200,{'workspace':ws_obj(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
            if len(p)==5 and p[1:3]==['api','workspaces'] and p[4]=='invitations':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
                if not w:return err(self,404,'not found')
                if member_role(c,w['id'],u['id']) not in ('owner','admin'):return err(self,403,'forbidden')
                a=[]
                for r in c.execute('SELECT * FROM invitations WHERE workspace_id=? AND uses<max_uses AND CAST(expires_at AS REAL)>?',(w['id'],time.time())):a.append(dict(r))
                return self.send_json(200,{'invitations':a})
            if len(p)==5 and p[1:3]==['api','workspaces'] and p[4]=='groups':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
                if not w:return err(self,404,'not found')
                if not member_role(c,w['id'],u['id']):return err(self,403,'forbidden')
                out=[]
                for g in c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)):
                    out.append({'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':json.loads(g['user_ids'] or '[]')})
                return self.send_json(200,{'groups':out})
            return err(self,404,'not found')
        except Exception as e: err(self,400,str(e))
        finally:c.close()
    def do_POST(self):
        c=db();p,q=self.parse_path()
        try:
            if p[:4]==['','api','auth','register']:
                d=body(self)
                if not d or not re.fullmatch(r'[A-Za-z0-9_]+',d.get('username','')) or len(d.get('password',''))<8:return err(self,400,'invalid')
                if c.execute('SELECT 1 FROM users WHERE username=?',(d['username'],)).fetchone():return err(self,409,'duplicate')
                c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(d['username'],hashlib.sha256(d['password'].encode()).hexdigest(),d.get('display_name') or d['username']))
                uid=c.execute('SELECT last_insert_rowid()').fetchone()[0];t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid));c.commit()
                return self.send_json(201,{'user':user_obj(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()),'token':t})
            if p[:4]==['','api','auth','login']:
                d=body(self);r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(d.get('username',''),hashlib.sha256(d.get('password','').encode()).hexdigest())).fetchone()
                if not r:return err(self,401,'wrong password')
                t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id']));c.commit();return self.send_json(200,{'user':user_obj(r),'token':t})
            u=self.auth(c)
            if not u:return
            if p==['','api','files']:
                part=multipart_file(self)
                if part=='TOO_BIG':return err(self,413,'too large')
                if not part:return err(self,400,'file required')
                filename,typ,data=part
                c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at,data) VALUES(?,?,?,?,?,?)',(u['id'],filename,typ,len(data),now(),data));fid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.commit()
                f=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
                return self.send_json(201,{'file':{k:f[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}})
            if p==['','api','workspaces']:
                d=body(self)
                if not d or not re.fullmatch(r'[a-z0-9-]{2,32}',d.get('slug','')) or not d.get('name'):return err(self,400,'invalid')
                if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(d['slug'],)).fetchone():return err(self,409,'duplicate')
                c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d['name'],u['id']));wid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'));c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general'));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']));c.commit()
                return self.send_json(201,{'workspace':ws_obj(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':ch_obj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
            if len(p)==5 and p[1:3]==['api','channels'] and p[4]=='read':
                try: cid=int(p[3])
                except:return err(self,404,'not found')
                ch=accessible(c,u['id'],cid)
                if not ch:return err(self,404,'not found')
                d=body(self) or {}
                try: new=int(d.get('last_read_event_id'))
                except:return err(self,400,'invalid')
                row=c.execute('SELECT last_read_event_id FROM reads WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone()
                old=row['last_read_event_id'] if row else 0
                if new<old:new=old
                c.execute('INSERT INTO reads(user_id,channel_id,last_read_event_id) VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=excluded.last_read_event_id',(u['id'],cid,new))
                c.commit()
                return self.send_json(200,{'read_state':{'channel_id':cid,'last_read_event_id':new,'unread_count':0,'mention_count':0}})
            if len(p)==5 and p[1:3]==['api','channels'] and p[-1] in ('leave','archive','unarchive'):
                try: cid=int(p[3])
                except:return err(self,404,'not found')
                ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
                if not ch:return err(self,404,'not found')
                w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
                if p[4]=='leave':
                    c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id']));add_event(c,cid,'member.left',{'user_id':u['id']});c.commit()
                    return self.send_json(200,{'channel':ch_obj(ch)})
                if member_role(c,w['id'],u['id'])!='owner':return err(self,403,'forbidden')
                val=1 if p[4]=='archive' else 0
                c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,cid));add_event(c,cid,'channel.archived' if val else 'channel.unarchived',ch_obj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()));c.commit()
                return self.send_json(200,{'channel':ch_obj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
            if len(p)==5 and p[1:3]==['api','workspaces'] and p[4]=='groups':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
                if not w:return err(self,404,'not found')
                if not member_role(c,w['id'],u['id']):return err(self,403,'forbidden')
                d=body(self) or {}; h=d.get('handle'); name=d.get('name')
                if not isinstance(h,str) or not re.fullmatch(r'[a-z0-9_-]+',h) or not isinstance(name,str) or not name:return err(self,400,'invalid')
                if c.execute('SELECT 1 FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],h)).fetchone():return err(self,409,'duplicate')
                ids=d.get('user_ids',[])
                if not isinstance(ids,list):return err(self,400,'invalid')
                ids=[int(x) for x in ids if isinstance(x,int) and member_role(c,w['id'],x)]
                c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id,user_ids) VALUES(?,?,?,?,?)',(w['id'],h,name,u['id'],json.dumps(ids)));gid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.commit()
                return self.send_json(201,{'group':{'id':gid,'handle':h,'name':name,'member_user_ids':ids}})
            if len(p)==5 and p[1:3]==['api','channels'] and p[-1]=='join':
                try: cid=int(p[3])
                except: return err(self,404,'not found')
                ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
                if not ch:return err(self,404,'not found')
                w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
                role=member_role(c,w['id'],u['id'])
                if role=='guest': return err(self,403,'forbidden')
                if ch['is_private'] and (not role or not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone()):return err(self,403,'forbidden')
                if not role:
                    if w['join_mode']!='open': return err(self,403,'invite required')
                    c.execute('INSERT INTO members VALUES(?,?,?)',(w['id'],u['id'],'member'))
                c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,u['id']));add_event(c,cid,'member.joined',{'user_id':u['id']});c.commit();return self.send_json(200,{'channel':ch_obj(ch)})
            if len(p)==5 and p[1:3]==['api','workspaces'] and p[4]=='channels':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
                if not w:return err(self,404,'not found')
                d=body(self); role=member_role(c,w['id'],u['id'])
                if not role:return err(self,403,'forbidden')
                if role=='guest':return err(self,403,'forbidden')
                if not d or not re.fullmatch(r'[a-z0-9-]{1,32}',d.get('name','')) or len(d.get('topic',''))>250:return err(self,400,'invalid')
                private=bool(d.get('is_private'))
                if private and role not in ('owner','admin'):return err(self,403,'forbidden')
                if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],d['name'])).fetchone():return err(self,409,'duplicate')
                c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],d['name'],int(private),d.get('topic','')));cid=c.execute('SELECT last_insert_rowid()').fetchone()[0];c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']));c.commit();return self.send_json(201,{'channel':ch_obj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
            if len(p)==5 and p[1:3]==['api','messages'] and p[4]=='reactions':
                try: mid=int(p[3])
                except:return err(self,404,'not found')
                m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
                if not m:return err(self,404,'not found')
                ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
                if not ch:return err(self,404,'not found')
                if ch['is_archived']:return err(self,423,'archived')
                if not accessible(c,u['id'],ch['id']):return err(self,403,'forbidden')
                d=body(self) or {}; emoji=d.get('emoji')
                if not isinstance(emoji,str) or not emoji:return err(self,400,'invalid')
                c.execute('INSERT OR IGNORE INTO reactions(message_id,user_id,emoji) VALUES(?,?,?)',(mid,u['id'],emoji))
                if c.execute('SELECT changes()').fetchone()[0]:
                    add_event(c,ch['id'],'reaction.added',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
                c.commit()
                rr=[]
                for x in c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=?',(mid,)):
                    q=next((z for z in rr if z['emoji']==x['emoji']),None)
                    if q is None:
                        q={'emoji':x['emoji'],'count':0,'user_ids':[]}
                        rr.append(q)
                    q['count']+=1
                    q['user_ids'].append(x['user_id'])
                return self.send_json(200,{'reactions':rr})
            if len(p)==5 and p[1:3]==['api','messages'] and p[4]=='pin':
                try: mid=int(p[3])
                except:return err(self,404,'not found')
                m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
                if not m:return err(self,404,'not found')
                if not accessible(c,u['id'],m['channel_id']):return err(self,403,'forbidden')
                c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(mid,u['id'],now()));c.commit()
                r=c.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone()
                return self.send_json(200,{'pin':dict(r)})
            if len(p)==3 and p[1:3]==['api','dms']:
                d=body(self) or {}
                if 'user_id' not in d:return err(self,400,'invalid')
                try: other=int(d['user_id'])
                except:return err(self,400,'invalid')
                if not c.execute('SELECT 1 FROM users WHERE id=?',(other,)).fetchone():return err(self,404,'not found')
                row=c.execute('SELECT ch.id FROM channels ch JOIN channel_members a ON a.channel_id=ch.id AND a.user_id=? JOIN channel_members b ON b.channel_id=ch.id AND b.user_id=? WHERE ch.is_dm=1',(u['id'],other)).fetchone()
                if row:return self.send_json(200,{'channel_id':row[0]})
                c.execute('INSERT INTO channels(workspace_id,name,is_dm) VALUES(NULL,?,1)',('dm-'+str(min(u['id'],other))+'-'+str(max(u['id'],other)),))
                cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
                c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']));c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,other));c.commit()
                return self.send_json(200,{'channel_id':cid})
            if len(p)==5 and p[1:3]==['api','channels'] and p[4]=='messages':
                cid=int(p[3]);ch=accessible(c,u['id'],cid);d=body(self)
                if not ch:
                    raw=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
                    return err(self,403,'forbidden') if raw and raw['is_dm'] else err(self,404,'not found')
                if not d or not isinstance(d.get('body'),str) or not d['body'].strip():return err(self,400,'empty')
                text=d['body']
                w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone() if ch['workspace_id'] else None
                role=member_role(c,w['id'],u['id']) if w else None
                if text.startswith('/') and not text.startswith('//'):
                    parts=text.split(' ',1); cmd=parts[0]; arg=parts[1].strip() if len(parts)>1 else ''
                    if cmd=='/me':
                        if not arg:return err(self,400,'missing text')
                    elif cmd=='/shrug':
                        text=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
                    elif cmd=='/topic':
                        if not arg or len(arg)>250:return err(self,400,'invalid')
                        if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone() and role!='owner':return err(self,403,'forbidden')
                        c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,cid));add_event(c,cid,'channel.updated',ch_obj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()));c.commit()
                        return self.send_json(201,{'message':None,'channel':ch_obj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
                    elif cmd=='/invite':
                        if not arg.startswith('@') or not role:return err(self,404,'not found')
                        name=arg[1:]; target=c.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone()
                        if not target:return err(self,404,'not found')
                        if role not in ('owner','admin'):return err(self,403,'forbidden')
                        c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w['id'],target['id'],'member'));c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,target['id']));c.commit()
                        return self.send_json(201,{'message':None,'invited_user':user_obj(target)})
                    elif cmd in ('/archive','/unarchive'):
                        if role!='owner':return err(self,403,'forbidden')
                        val=1 if cmd=='/archive' else 0
                        if ch['is_archived'] and cmd=='/archive':return err(self,423,'archived')
                        c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,cid));add_event(c,cid,'channel.archived' if val else 'channel.unarchived',ch_obj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()));c.commit()
                        return self.send_json(201,{'message':None,'channel':ch_obj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
                    else:return err(self,400,'unknown command')
                if ch['is_archived']:return err(self,423,'archived')
                fids=d.get('file_ids',[])
                if not isinstance(fids,list):return err(self,400,'invalid files')
                for fid in fids:
                    if not isinstance(fid,int) or not c.execute('SELECT 1 FROM files WHERE id=? AND uploader_id=?',(fid,u['id'])).fetchone() or c.execute('SELECT 1 FROM message_files WHERE file_id=?',(fid,)).fetchone():return err(self,400,'invalid files')
                c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,u['id'],text,d.get('parent_id'),now()));mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
                for fid in fids:c.execute('INSERT INTO message_files VALUES(?,?)',(mid,fid))
                m=message_obj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone());eid=add_event(c,cid,'message.reply' if d.get('parent_id') else 'message.created',m);c.commit();return self.send_json(201,{'message':m})
            if len(p)==5 and p[1:3]==['api','workspaces'] and p[4]=='transfer_ownership':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
                if not w:return err(self,404,'not found')
                if member_role(c,w['id'],u['id'])!='owner':return err(self,403,'forbidden')
                d=body(self); target=d.get('new_owner_id') if d else None
                if not isinstance(target,int):return err(self,400,'invalid')
                if member_role(c,w['id'],target)!='admin':return err(self,400,'invalid')
                c.execute('UPDATE members SET role="admin" WHERE workspace_id=? AND user_id=?',(w['id'],u['id']))
                c.execute('UPDATE members SET role="owner" WHERE workspace_id=? AND user_id=?',(w['id'],target))
                c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id']));c.commit()
                return self.send_json(200,{'workspace':ws_obj(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
            if len(p)==5 and p[1:3]==['api','workspaces'] and p[4]=='invitations':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
                if not w:return err(self,404,'not found')
                if member_role(c,w['id'],u['id']) not in ('owner','admin'):return err(self,403,'forbidden')
                d=body(self) or {}; mi=d.get('invited_user_id')
                invname=d.get('invited_username')
                if mi is None and invname:
                    z=c.execute('SELECT id FROM users WHERE username=?',(invname,)).fetchone()
                    if z:mi=z[0]
                if mi is not None:
                    try: mi=int(mi)
                    except:return err(self,400,'invalid')
                mx=d.get('max_uses',1)
                if not isinstance(mx,int) or mx<1:return err(self,400,'invalid')
                if mi is not None:mx=1
                ex=time.time()+int(d.get('expires_in_seconds',604800))
                if ex<=time.time():return err(self,400,'invalid')
                code=secrets.token_urlsafe(18)
                c.execute('INSERT INTO invitations(code,workspace_id,invited_user_id,expires_at,max_uses,uses,invited_username) VALUES(?,?,?,?,?,?,?)',(code,w['id'],mi,str(ex),mx,0,invname));c.commit()
                return self.send_json(201,{'invitation':{'code':code,'workspace_id':w['id'],'invited_user_id':mi,'expires_at':ex,'max_uses':mx,'uses':0}})
            if len(p)==5 and p[1:3]==['api','invitations'] and p[4]=='accept':
                row=c.execute('SELECT * FROM invitations WHERE code=?',(p[3],)).fetchone()
                if not row:return err(self,404,'not found')
                if (row['invited_user_id'] is not None and row['invited_user_id']!=u['id']) or (row['invited_username'] is not None and row['invited_username']!=u['username']):return err(self,403,'wrong user')
                c.execute('BEGIN IMMEDIATE')
                row=c.execute('SELECT * FROM invitations WHERE code=?',(p[3],)).fetchone()
                if not row or row['uses']>=row['max_uses'] or float(row['expires_at'])<time.time():
                    c.rollback();return err(self,404,'not found')
                c.execute('UPDATE invitations SET uses=uses+1 WHERE code=? AND uses<max_uses',(p[3],))
                if c.execute('SELECT changes()').fetchone()[0]!=1:c.rollback();return err(self,404,'not found')
                c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(row['workspace_id'],u['id'],'member'));c.commit()
                return self.send_json(200,{'workspace':ws_obj(c.execute('SELECT * FROM workspaces WHERE id=?',(row['workspace_id'],)).fetchone())})
            return err(self,404,'not found')
        except Exception as e: err(self,400,str(e))
        finally:c.close()
    def do_PATCH(self):
        c=db();p,q=self.parse_path()
        try:
            u=self.auth(c)
            if not u:return
            if len(p)==6 and p[1:3]==['api','workspaces'] and p[4]=='groups':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
                if not w:return err(self,404,'not found')
                g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],p[5])).fetchone()
                if not g:return err(self,404,'not found')
                if u['id']!=g['creator_id'] and member_role(c,w['id'],u['id'])!='owner':return err(self,403,'forbidden')
                d=body(self) or {}; name=d.get('name',g['name']); ids=d.get('user_ids',json.loads(g['user_ids'] or '[]'))
                if not isinstance(name,str) or not name or not isinstance(ids,list):return err(self,400,'invalid')
                ids=[int(x) for x in ids if isinstance(x,int) and member_role(c,w['id'],x)]
                c.execute('UPDATE groups_ SET name=?,user_ids=? WHERE id=?',(name,json.dumps(ids),g['id']));c.commit()
                return self.send_json(200,{'group':{'id':g['id'],'handle':g['handle'],'name':name,'member_user_ids':ids}})
            if p==['','api','users','me']:
                d=body(self) or {}
                allowed=('display_name','timezone','status_text','status_emoji','avatar_url')
                if any(k not in allowed for k in d):return err(self,400,'invalid')
                if 'timezone' in d and (not isinstance(d['timezone'],str) or d['timezone'] not in ('UTC','Europe/Paris','America/New_York','Asia/Tokyo')):return err(self,400,'invalid')
                limits={'display_name':80,'status_text':140,'status_emoji':64,'avatar_url':512}
                for k,v in d.items():
                    if not isinstance(v,str) or (k in limits and (len(v)==0 or len(v)>limits[k])):return err(self,400,'invalid')
                if d:
                    sets=','.join(k+'=?' for k in d); vals=list(d.values())+[u['id']]
                    c.execute('UPDATE users SET '+sets+' WHERE id=?',vals)
                    r=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
                    c.execute('INSERT INTO user_events(user_id,payload_json) VALUES(?,?)',(u['id'],json.dumps(user_obj(r))));c.commit()
                    return self.send_json(200,{'user':user_obj(r)})
                return self.send_json(200,{'user':user_obj(u)})
            if len(p)==4 and p[1:3]==['api','workspaces']:
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
                if not w:return err(self,404,'not found')
                role=member_role(c,w['id'],u['id'])
                if role not in ('owner','admin'):return err(self,403,'forbidden')
                d=body(self) or {}
                if 'join_mode' in d and d['join_mode'] not in ('open','invite_only'):return err(self,400,'invalid')
                if 'name' in d and not isinstance(d['name'],str):return err(self,400,'invalid')
                c.execute('UPDATE workspaces SET name=COALESCE(?,name),join_mode=COALESCE(?,join_mode) WHERE id=?',(d.get('name'),d.get('join_mode'),w['id']));c.commit()
                return self.send_json(200,{'workspace':ws_obj(c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone())})
            if len(p)==4 and p[1:3]==['api','channels']:
                try: cid=int(p[3])
                except:return err(self,404,'not found')
                ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
                if not ch:return err(self,404,'not found')
                w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
                if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone() and member_role(c,w['id'],u['id'])!='owner':return err(self,403,'forbidden')
                d=body(self) or {}
                if 'topic' not in d or not isinstance(d['topic'],str) or len(d['topic'])>250:return err(self,400,'invalid')
                c.execute('UPDATE channels SET topic=? WHERE id=?',(d['topic'],cid));c.commit()
                return self.send_json(200,{'channel':ch_obj(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())})
            if len(p)==6 and p[1:4]==['api','workspaces',''+p[3]] and p[4]=='members':
                try: target=int(p[5])
                except:return err(self,404,'not found')
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
                if not w:return err(self,404,'not found')
                caller=member_role(c,w['id'],u['id']); old=member_role(c,w['id'],target)
                if caller not in ('owner','admin'):return err(self,403,'forbidden')
                if not old:return err(self,404,'not found')
                if old=='owner':return err(self,403,'forbidden')
                if caller=='admin' and old=='admin':return err(self,403,'forbidden')
                d=body(self); role=d.get('role') if d else None
                if role=='owner' or role not in ('admin','member','guest'):return err(self,400,'invalid')
                c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(role,w['id'],target));c.commit()
                r=c.execute('SELECT m.*,u.username,u.display_name FROM members m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? AND m.user_id=?',(w['id'],target)).fetchone()
                return self.send_json(200,{'member':dict(r)})
            if len(p)==4 and p[1:3]==['api','messages']:
                try: mid=int(p[3])
                except:return err(self,404,'not found')
                m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
                if not m:return err(self,404,'not found')
                chm=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
                if chm and chm['is_archived']:return err(self,423,'archived')
                if m['deleted']:return err(self,409,'deleted')
                if m['author_id']!=u['id']:return err(self,403,'forbidden')
                d=body(self) or {}
                if not str(d.get('body','')).strip():return err(self,400,'invalid')
                c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(d['body'],now(),mid))
                mm=message_obj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone());add_event(c,m['channel_id'],'message.updated',{'message':mm});c.commit()
                return self.send_json(200,{'message':mm})
            return err(self,404,'not found')
        except Exception as e:err(self,400,str(e))
        finally:c.close()
    def do_DELETE(self):
        c=db();p,q=self.parse_path()
        try:
            u=self.auth(c)
            if not u:return
            if len(p)==6 and p[1:3]==['api','workspaces'] and p[4]=='invitations':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
                if not w:return err(self,404,'not found')
                if member_role(c,w['id'],u['id']) not in ('owner','admin'):return err(self,403,'forbidden')
                if not c.execute('SELECT 1 FROM invitations WHERE code=? AND workspace_id=?',(p[5],w['id'])).fetchone():return err(self,404,'not found')
                c.execute('DELETE FROM invitations WHERE code=?',(p[5],));c.commit();return self.send_json(200,{'deleted':True})
            if len(p)==6 and p[1:3]==['api','messages'] and p[4]=='reactions':
                try: mid=int(p[3])
                except:return err(self,404,'not found')
                m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
                if not m:return err(self,404,'not found')
                ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
                if not ch:return err(self,404,'not found')
                if ch['is_archived']:return err(self,423,'archived')
                if not accessible(c,u['id'],ch['id']):return err(self,403,'forbidden')
                emoji=urllib.parse.unquote(p[5])
                c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji))
                add_event(c,ch['id'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':u['id']});c.commit()
                rr=[]
                for x in c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=?',(mid,)):
                    q=next((z for z in rr if z['emoji']==x['emoji']),None)
                    if q is None:q={'emoji':x['emoji'],'count':0,'user_ids':[]};rr.append(q)
                    q['count']+=1;q['user_ids'].append(x['user_id'])
                return self.send_json(200,{'reactions':rr})
            if len(p)==5 and p[1:3]==['api','messages'] and p[4]=='pin':
                try:mid=int(p[3])
                except:return err(self,404,'not found')
                if not c.execute('SELECT 1 FROM pins WHERE message_id=?',(mid,)).fetchone():return self.send_json(200,{'unpinned':True})
                c.execute('DELETE FROM pins WHERE message_id=?',(mid,));c.commit();return self.send_json(200,{'unpinned':True})
            if len(p)==4 and p[1:3]==['api','messages']:
                try: mid=int(p[3])
                except:return err(self,404,'not found')
                m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
                if not m:return self.send_json(200,{'deleted':True})
                chm=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
                if chm and chm['is_archived']:return err(self,423,'archived')
                if m['deleted']:return self.send_json(200,{'deleted':True})
                allowed=m['author_id']==u['id']
                if not allowed and chm and chm['workspace_id']:
                    ww=c.execute('SELECT * FROM workspaces WHERE id=?',(chm['workspace_id'],)).fetchone()
                    allowed=ww and ww['owner_id']==u['id']
                if not allowed:return err(self,403,'forbidden')
                c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,));add_event(c,m['channel_id'],'message.deleted',{'message_id':mid});c.commit()
                return self.send_json(200,{'deleted':True})
            if len(p)==6 and p[1:3]==['api','workspaces'] and p[4]=='groups':
                w=c.execute('SELECT * FROM workspaces WHERE slug=?',(p[3],)).fetchone()
                if not w:return err(self,404,'not found')
                g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],p[5])).fetchone()
                if not g:return self.send_json(200,{'deleted':True})
                if u['id']!=g['creator_id'] and member_role(c,w['id'],u['id'])!='owner':return err(self,403,'forbidden')
                c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],));c.commit();return self.send_json(200,{'deleted':True})
            return err(self,404,'not found')
        finally:c.close()
    def ws(self,c):
        tok=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('token',[''])[0]
        if not token_user(c,tok):
            self.send_response(401);self.end_headers();return
        key=self.headers.get('Sec-WebSocket-Key','')
        if not key:
            self.send_response(400);self.end_headers();return
        accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.send_response(101,'Switching Protocols')
        self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade');self.send_header('Sec-WebSocket-Accept',accept);self.end_headers()
        self.connection.settimeout(.2); uid=token_user(c,tok)['id']; subs={}; sent={}; global_seen=0; presence_token=tok+'-'+str(id(self))
        c.execute('INSERT OR REPLACE INTO presence(token,user_id,channel_id,seen) SELECT ?,?,channel_id,? FROM channel_members WHERE user_id=?',(presence_token,uid,time.time(),uid));c.commit()
        def frame(obj):
            raw=json.dumps(obj,separators=(',',':')).encode(); n=len(raw)
            if n<126:h=b'\x81'+bytes([n])
            elif n<65536:h=b'\x81\x7e'+struct.pack('>H',n)
            else:h=b'\x81\x7f'+struct.pack('>Q',n)
            self.connection.sendall(h+raw)
        def recvmsg():
            try:
                h=self.connection.recv(2)
                if not h:return None
                n=h[1]&127; masked=h[1]&128
                if n==126:n=struct.unpack('>H',self.connection.recv(2))[0]
                elif n==127:n=struct.unpack('>Q',self.connection.recv(8))[0]
                mask=self.connection.recv(4) if masked else b''
                data=self.connection.recv(n)
                while len(data)<n:data+=self.connection.recv(n-len(data))
                if masked:data=bytes(v^mask[i%4] for i,v in enumerate(data))
                return json.loads(data)
            except (socket.timeout,BlockingIOError):return '__timeout__'
            except:return None
        while True:
            msg=recvmsg()
            if msg is None:break
            if msg!='__timeout__':
                try:
                    typ=msg.get('type'); cid=int(msg.get('channel_id'))
                    if typ=='subscribe':
                        c.execute('INSERT OR REPLACE INTO presence(token,user_id,channel_id,seen) VALUES(?,?,?,?)',(presence_token,uid,cid,time.time()));c.commit()
                        head=c.execute('SELECT COALESCE(last_event_id,0) FROM channel_seq WHERE channel_id=?',(cid,)).fetchone()
                        head=head[0] if head else 0;subs[cid]=head;sent[cid]=head
                        frame({'type':'subscribed','channel_id':cid,'head_event_id':head})
                    elif typ=='resume':
                        since=int(msg.get('since_event_id',0)); rows=c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall()
                        for r in rows:
                            x=json.loads(r['payload_json']);x.update({'type':r['type'],'event_id':r['event_id'],'channel_id':cid});frame(x);sent[cid]=r['event_id']
                        head=c.execute('SELECT COALESCE(last_event_id,0) FROM channel_seq WHERE channel_id=?',(cid,)).fetchone()
                        head=head[0] if head else since;subs[cid]=head
                        frame({'type':'resumed','channel_id':cid,'head_event_id':head})
                except Exception as ex:
                    open('/tmp/ws-debug.log','a').write(repr(ex)+'\n')
            if subs:
                c.executemany('UPDATE presence SET seen=? WHERE token=? AND channel_id=?',[(time.time(),presence_token,x) for x in subs]);c.commit()
            grows=c.execute('SELECT id,user_id,payload_json FROM user_events WHERE id>? ORDER BY id',(global_seen,)).fetchall()
            for gr in grows:
                frame({'type':'user.updated','user':json.loads(gr['payload_json'])});global_seen=gr['id']
            for cid in list(subs):
                last=sent.get(cid,0)
                rows=c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)).fetchall()
                for r in rows:
                    x=json.loads(r['payload_json']);x.update({'type':r['type'],'event_id':r['event_id'],'channel_id':cid});frame(x);sent[cid]=r['event_id']
        time.sleep(0.35); c.execute('DELETE FROM presence WHERE token=?',(presence_token,));c.commit();c.close()
ThreadingHTTPServer.daemon_threads=True
ThreadingHTTPServer(('127.0.0.1',PORT),H).serve_forever()
