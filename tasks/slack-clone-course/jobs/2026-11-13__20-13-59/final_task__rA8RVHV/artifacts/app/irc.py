import socketserver,threading,time,traceback
from core import db,auth,create_message,member
nicks=set(); clients=[]; lock=threading.Lock()
def send(h,s):
 try:
  h.wfile.write((':'+s+'\r\n').encode()); h.wfile.flush()
 except: pass
def resolve(name):
 if not name.startswith('#') or '/' not in name[1:]: return None
 slug,cn=name[1:].split('/',1); c=db()
 r=c.execute('SELECT c.* FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(slug,cn)).fetchone(); c.close(); return r
def ircname(conn,ch):
 r=conn.execute('SELECT slug FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
 return '#'+r['slug']+'/'+ch['name']
class H(socketserver.StreamRequestHandler):
 def setup(self):
  super().setup(); self.nick=None; self.token=None; self.user=None; self.registered=False
  self.collided=False; self.joined={}; self.last={}; self.write_lock=threading.Lock(); self.alive=True
 def out(self,s):
  with self.write_lock: send(self,s)
 def poll(self):
  while self.alive:
   time.sleep(.05)
   if not self.registered: continue
   try:
    c=db()
    for cid in list(self.joined):
     rows=list(c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,self.last.get(cid,0))))
     for r in rows:
      self.last[cid]=r['event_id']
      if r['type'] not in ('message.created','message.reply'): continue
      import json
      m=json.loads(r['payload_json']).get('message') or {}
      if m.get('author_id')==self.user['id']: continue
      a=c.execute('SELECT username FROM users WHERE id=?',(m.get('author_id'),)).fetchone()
      ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
      if a and ch:self.out(f"{a['username']}!{a['username']}@huddle PRIVMSG {ircname(c,ch)} :{m.get('body','')}")
    c.close()
   except Exception:
    traceback.print_exc()
 def handle(self):
  threading.Thread(target=self.poll,daemon=True).start()
  try:
   while True:
    line=self.rfile.readline()
    if not line: break
    z=line.decode(errors='ignore').strip('\r\n'); a=z.split(' ',1); cmd=a[0].upper(); rest=a[1] if len(a)>1 else ''
    if cmd=='PASS': self.token=rest.lstrip(':'); continue
    if cmd=='NICK':
     nn=rest.split()[0] if rest else ''
     with lock:
      if nn in nicks:
       self.out('huddle 433 * '+nn+' :Nickname is already in use'); self.collided=True; continue
      if self.nick:nicks.discard(self.nick)
      self.nick=nn;nicks.add(nn)
    elif cmd=='USER':
     if not self.nick: self.out('huddle 431 * :No nickname given'); continue
     c=db();u=auth(c,self.token);c.close()
     if not u:self.out('huddle 464 * :Password incorrect');break
     self.user=u;self.registered=True; nick=str(self.nick)
     nums=[('001','Welcome')] if self.collided else [('001','Welcome'),('002','Your host is huddle'),('003','This server'),('004','huddle'),('005','CHANTYPES=#')]
     for num,text in nums:self.out('huddle '+num+' '+nick+' :'+text)
     if not self.collided:self.out('huddle 422 '+nick+' :MOTD File is missing')
    elif cmd=='PING': self.out('huddle PONG huddle '+rest)
    elif cmd=='QUIT': break
    elif not self.registered: continue
    elif cmd=='JOIN':
     name=rest.split()[0] if rest else ''; ch=resolve(name)
     if not ch:self.out(f'huddle 403 {self.nick} {name} :No such channel');continue
     c=db()
     if not member(c,ch['id'],self.user['id']):
      w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
      c.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(w['id'],self.user['id'],'member'))
      c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],self.user['id']));c.commit()
     self.joined[ch['id']]=name; row=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(ch['id'],)).fetchone();self.last[ch['id']]=row['last_event_id'] if row else 0
     names=[x['username'] for x in c.execute('SELECT u.username FROM channel_members m JOIN users u ON u.id=m.user_id WHERE m.channel_id=?',(ch['id'],))]
     self.out(f'{self.nick}!{self.user["username"]}@huddle JOIN {name}');self.out(f'huddle 353 {self.nick} = {name} :{" ".join(names)}');self.out(f'huddle 366 {self.nick} {name} :End of /NAMES list.')
     c.close()
    elif cmd=='PRIVMSG':
     if ' :' not in rest: continue
     target,body=rest.split(' :',1);ch=resolve(target)
     if ch and ch['id'] in self.joined:
      c=db(); create_message(c,self.user['id'],ch['id'],body); c.close()
    elif cmd=='NAMES':
     ch=resolve(rest.split()[0] if rest else ''); 
     if ch:self.out(f'huddle 353 {self.nick} = {rest.split()[0]} :');self.out(f'huddle 366 {self.nick} {rest.split()[0]} :End of /NAMES list.')
    elif cmd in ('PONG','WHO','LIST','MODE'): continue
    elif cmd=='TOPIC':
     ch=resolve(rest.split()[0] if rest else '')
     if ch:self.out(f'huddle 331 {self.nick} {rest.split()[0]} :No topic is set')
    else:self.out(f'huddle 421 {self.nick} {cmd} :Unknown command')
  finally:
   self.alive=False
   if self.nick:
    with lock:nicks.discard(self.nick)
class S(socketserver.ThreadingTCPServer): allow_reuse_address=True
S(('0.0.0.0',6667),H).serve_forever()
