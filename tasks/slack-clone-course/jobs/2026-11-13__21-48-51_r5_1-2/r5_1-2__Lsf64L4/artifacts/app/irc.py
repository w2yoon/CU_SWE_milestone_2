import socketserver, sqlite3, json, time, threading
from core import db, auth, emit, msg_obj, now
nicks=set()
def resolve(name):
    if not name.startswith('#') or '/' not in name[1:]: return None
    slug,cn=name[1:].split('/',1); c=db()
    r=c.execute('SELECT c.* FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=? AND c.is_archived=0',(slug,cn)).fetchone()
    c.close(); return r
class C(socketserver.StreamRequestHandler):
    def sendline(self,s):
        try: self.wfile.write((s+'\r\n').encode()); self.wfile.flush()
        except: pass
    def handle(self):
        tok=nick=uid=None; registered=False; collided=False; joined={}; alive=True
        def poll():
            while alive:
                time.sleep(.05)
                for cid,st in list(joined.items()):
                    c=None
                    try:
                        c=db()
                        rows=c.execute('SELECT event_id,type,payload_json FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,st['last'])).fetchall()
                        for r in rows:
                            st['last']=r['event_id']
                            if r['type'] not in ('message.created','message.reply'): continue
                            x=json.loads(r['payload_json']); m=x.get('message',x)
                            if not isinstance(m,dict) or m.get('author_id')==uid: continue
                            a=m.get('author') or {}; n=a.get('username','user')
                            self.sendline(f":{n}!{n}@huddle PRIVMSG {st['name']} :{m.get('body','')}")
                    except: pass
                    finally:
                        if c:
                            try: c.close()
                            except: pass
        threading.Thread(target=poll,daemon=True).start()
        try:
            while True:
                line=self.rfile.readline()
                if not line: break
                a=line.decode(errors='ignore').strip().split(' ',2); cmd=a[0].upper()
                if cmd=='PASS' and len(a)>1: tok=a[1]
                elif cmd=='NICK' and len(a)>1:
                    if a[1] in nicks:
                        collided=True; self.sendline(':huddle 433 * '+a[1]+' :Nickname is already in use')
                    else: nick=a[1]
                elif cmd=='USER':
                    if not tok: self.sendline(':huddle 464 * :Password incorrect'); break
                    c=db(); u=auth(c,tok); c.close()
                    if not u: self.sendline(':huddle 464 * :Password incorrect'); break
                    if not nick: self.sendline(':huddle 464 * :Password incorrect'); continue
                    uid=u['id']; nicks.add(nick); registered=True
                    nums=('001',) if collided else ('001','002','003','004','005')
                    lines=[':huddle '+x+' '+nick+' :huddle' for x in nums]
                    if not collided: lines.append(':huddle 422 '+nick+' :MOTD File is missing')
                    try: self.wfile.write(('\r\n'.join(lines)+'\r\n').encode()); self.wfile.flush()
                    except: break
                elif cmd=='PING': self.sendline(':huddle PONG huddle '+(a[1] if len(a)>1 else ''))
                elif cmd=='QUIT': break
                elif not registered: continue
                elif cmd=='JOIN' and len(a)>1:
                    name=a[1]; ch=resolve(name)
                    if not ch: self.sendline(f':huddle 403 {nick} {name} :No such channel'); continue
                    c=db(); w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
                    c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w['id'],uid,'member'))
                    c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],uid)); c.commit()
                    q=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(ch['id'],)).fetchone(); head=q[0] if q else 0; c.close()
                    joined[ch['id']]={'name':name,'last':head}
                    self.sendline(f':{nick}!{nick}@huddle JOIN {name}'); self.sendline(f':huddle 353 {nick} = {name} :{nick}'); self.sendline(f':huddle 366 {nick} {name} :End of NAMES list')
                elif cmd=='PRIVMSG' and len(a)>2:
                    name=a[1]; body=a[2][1:] if a[2].startswith(':') else a[2]; ch=resolve(name)
                    if not ch: self.sendline(f':huddle 403 {nick} {name} :No such channel'); continue
                    if ch['id'] not in joined: self.sendline(f':huddle 403 {nick} {name} :Not joined'); continue
                    c=db(); c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],uid))
                    c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch['id'],uid,body,None,now()))
                    mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); emit(c,ch['id'],'message.created',msg_obj(c,r)); c.commit(); c.close()
                elif cmd=='NAMES':
                    name=a[1] if len(a)>1 else ''; ch=resolve(name)
                    if ch: self.sendline(f':huddle 353 {nick} = {name} :{nick}'); self.sendline(f':huddle 366 {nick} {name} :End of NAMES list')
                    else: self.sendline(f':huddle 403 {nick} {name} :No such channel')
                elif cmd=='TOPIC':
                    name=a[1] if len(a)>1 else ''; ch=resolve(name)
                    if ch: self.sendline(f':huddle {332 if ch["topic"] else 331} {nick} {name}'+(f' :{ch["topic"]}' if ch['topic'] else ' :No topic'))
                elif cmd in ('WHO','LIST','MODE','PONG'): pass
                else: self.sendline(f':huddle 421 {nick} {a[0]} :Unknown command')
        except: pass
        finally:
            alive=False
            if nick: nicks.discard(nick)
class S(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
S(('0.0.0.0',6667),C).serve_forever()
