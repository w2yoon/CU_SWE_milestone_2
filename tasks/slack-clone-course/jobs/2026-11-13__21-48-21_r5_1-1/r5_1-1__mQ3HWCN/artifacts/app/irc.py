import socketserver,threading,time,json,core
NICKS=set(); CLIENTS=[]; LOCK=threading.RLock()
def send(c,line):
    try:c.sendall((line+'\r\n').encode())
    except OSError:pass
def irc_name(slug,name): return '#'+slug+'/'+name
def resolve(name):
    if not name.startswith('#') or '/' not in name:return None
    slug,ch=name[1:].split('/',1); c=core.db()
    try:
        w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
        return (w,c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],ch)).fetchone()) if w else None
    finally:c.close()
def poll():
    seen={}
    while True:
        try:
            c=core.db()
            rows=c.execute("SELECT e.*,ch.workspace_id,ch.name cn,w.slug FROM events e JOIN channels ch ON ch.id=e.channel_id JOIN workspaces w ON w.id=ch.workspace_id WHERE e.type IN ('message.created','message.reply') ORDER BY e.channel_id,e.event_id").fetchall()
            with LOCK:
                clients=list(CLIENTS)
            for r in rows:
                key=(r['channel_id'],r['event_id'])
                if r['event_id']<=seen.get(r['channel_id'],0):continue
                seen[r['channel_id']]=r['event_id']; p=json.loads(r['payload_json']); m=p.get('message',{})
                for h in clients:
                    if r['channel_id'] in h.joined and m.get('author_id')!=h.uid:
                        send(h.request,':%s!%s@huddle PRIVMSG %s :%s'%(m.get('author',{}).get('username','user'),m.get('author',{}).get('username','user'),irc_name(r['slug'],r['cn']),m.get('body','')))
            c.close()
        except Exception:pass
        time.sleep(.05)
threading.Thread(target=poll,daemon=True).start()
class S(socketserver.StreamRequestHandler):
 def handle(self):
  self.nick=None;self.uid=None;self.registered=False;self.authed=False;self.joined=set()
  with LOCK:CLIENTS.append(self)
  try:
   while True:
    raw=self.rfile.readline()
    if not raw:break
    line=raw.decode('utf8','replace').rstrip('\r\n'); a=line.split(' ',1);cmd=a[0].upper();rest=a[1] if len(a)>1 else ''
    if cmd=='PASS':
     self.authed=bool(core.token_user(rest.strip()))
     if self.authed:self.uid=core.token_user(rest.strip())['id']
     else:send(self.request,':huddle 464 * :Password incorrect');return
    elif cmd=='NICK':
     n=rest.split()[0] if rest.split() else ''
     with LOCK:
      if n in NICKS and n!=self.nick:send(self.request,f':huddle 433 * {n} :Nickname is already in use');continue
      if self.nick:NICKS.discard(self.nick)
      self.nick=n;NICKS.add(n)
     self.reg()
    elif cmd=='USER':self.reg()
    elif cmd=='PING':send(self.request,'PONG huddle :'+(rest[1:] if rest.startswith(':') else rest))
    elif cmd=='QUIT':return
    elif not self.registered:continue
    elif cmd=='JOIN':self.join(rest.split()[0] if rest.split() else '')
    elif cmd=='PRIVMSG':self.privmsg(rest)
    elif cmd=='NAMES':self.names(rest.strip() or next(iter(self.joined),''))
    elif cmd=='TOPIC':self.topic(rest.strip())
    elif cmd in ('WHO','LIST','MODE','PONG'):pass
    else:send(self.request,f':huddle 421 {self.nick} {cmd} :Unknown command')
  except (OSError,ConnectionError):pass
 def reg(self):
  if self.registered or not self.nick or not self.authed:return
  self.registered=True;n=self.nick
  lines=[f':huddle {x} {n} :{t}' for x,t in [('001',f'Welcome to Huddle, {n}'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle 1.0 o o'),('005','CHANTYPES=#')]]
  try:self.request.sendall(('\r\n'.join(lines)+'\r\n').encode())
  except OSError:return
  threading.Timer(.15,lambda:send(self.request,f':huddle 422 {n} :MOTD File is missing')).start()
 def join(self,name):
  z=resolve(name)
  if not z or z[1]['is_private']:return send(self.request,f':huddle 403 {self.nick} {name} :No such channel')
  w,x=z;c=core.db()
  try:
   if not core.member(c,w['id'],self.uid):c.execute('INSERT INTO members VALUES(?,?,?)',(w['id'],self.uid,'member'))
   c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(x['id'],self.uid));c.commit();self.joined.add(x['id'])
   send(self.request,f':{self.nick}!{self.nick}@huddle JOIN {name}')
   self.names(name)
  finally:c.close()
 def names(self,name):
  z=resolve(name)
  if not z:return send(self.request,f':huddle 403 {self.nick} {name} :No such channel')
  c=core.db(); ns=[c.execute('SELECT username FROM users WHERE id=?',(r['user_id'],)).fetchone()['username'] for r in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(z[1]['id'],))]
  send(self.request,f':huddle 353 {self.nick} = {name} :{" ".join(ns)}');send(self.request,f':huddle 366 {self.nick} {name} :End of /NAMES list.');c.close()
 def topic(self,name):
  z=resolve(name)
  if z:send(self.request,f':huddle 331 {self.nick} {name} :No topic')
  else:send(self.request,f':huddle 403 {self.nick} {name} :No such channel')
 def privmsg(self,rest):
  if ' :' not in rest:return
  name,body=rest.split(' :',1);z=resolve(name.strip())
  if not z or z[1]['id'] not in self.joined:return send(self.request,f':huddle 403 {self.nick} {name} :No such channel')
  c=core.db();x=z[1]
  try:
   c.execute('BEGIN IMMEDIATE');c.execute('INSERT INTO messages(channel_id,author_id,body,created_at) VALUES(?,?,?,?)',(x['id'],self.uid,body,core.now()));i=c.execute('SELECT last_insert_rowid()').fetchone()[0];r=c.execute('SELECT * FROM messages WHERE id=?',(i,)).fetchone();core.add_event(c,x['id'],'message.created',{'message':core.msg_obj(c,r),'message_id':i});c.commit()
  finally:c.close()
 def finish(self):
  with LOCK:
   if self in CLIENTS:CLIENTS.remove(self)
   if self.nick:NICKS.discard(self.nick)
  super().finish()
class Server(socketserver.ThreadingTCPServer):allow_reuse_address=True
if __name__=='__main__':
 with Server(('0.0.0.0',6667),S) as srv:srv.serve_forever()
