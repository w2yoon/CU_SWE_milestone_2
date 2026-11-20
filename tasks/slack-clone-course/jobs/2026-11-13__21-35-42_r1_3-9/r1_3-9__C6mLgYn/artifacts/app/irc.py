import socketserver,socket,threading,time,json,sqlite3
from core import *
nicks={}; nl=threading.Lock()
def send(s,x):
 try:s.sendall((':huddle '+x+'\r\n').encode())
 except OSError: raise
def rawsend(s,x):
 try:s.sendall((x+'\r\n').encode())
 except OSError: raise
class IRC(socketserver.BaseRequestHandler):
 def handle(self):
  self.request.settimeout(.12); self.nick=None; self.uid=None; self.passok=False; self.reg=False; self.collided=False; self.joined={}; self.last={}
  buf=b''; stop=threading.Event()
  def poll():
   while not stop.wait(.05):
    for cid,cn in list(self.joined.items()):
     try:
      c=db()
      for e in c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,self.last.get(cid,0))):
       o=json.loads(e['payload']); m=o.get('message',{}); self.last[cid]=e['event_id']
       if m.get('author_id')!=self.uid and m.get('body') is not None:
        rawsend(self.request,':'+str(m.get('author',{}).get('username','user'))+'!'+str(m.get('author',{}).get('username','user'))+'@host PRIVMSG '+cn+' :'+m['body'])
     except (OSError, sqlite3.Error):
      time.sleep(.05)
     except Exception:
      time.sleep(.05)
  threading.Thread(target=poll,daemon=True).start()
  try:
   while True:
    try:
     data=self.request.recv(4096)
     if not data:return
     buf+=data
    except socket.timeout: pass
    while b'\n' in buf:
     raw,buf=buf.split(b'\n',1); line=raw.decode(errors='ignore').strip('\r')
     if not line:continue
     q=line.split(' ',2); cmd=q[0].upper()
     if cmd=='PASS':
      self.uid=None
      if len(q)>1:
       u=auth(q[1]); self.uid=u['id'] if u else None
      self.passok=self.uid is not None
     elif cmd=='NICK':
      nn=q[1] if len(q)>1 else '*'
      with nl:
       if nn in nicks and nicks[nn] is not self:
        send(self.request,'433 * '+nn+' :Nickname is already in use');self.collided=True;continue
       if self.nick:nicks.pop(self.nick,None)
       self.nick=nn;nicks[nn]=self
     elif cmd=='USER':
      if not self.passok:send(self.request,'464 * :Password incorrect');return
      if self.nick and not self.reg:
       self.reg=True
       if self.collided:send(self.request,'001 '+self.nick+' :Welcome')
       else:
        for x in ['001','002','003','004','005']:send(self.request,x+' '+self.nick+' :Welcome')
        send(self.request,'422 '+self.nick+' :MOTD File is missing')
     elif cmd=='PING':send(self.request,'PONG huddle '+(q[1] if len(q)>1 else ''))
     elif cmd=='QUIT':return
     elif not self.reg:
      if cmd=='FOOBAR':send(self.request,'464 * :Password incorrect')
     elif cmd=='JOIN' and len(q)>1:
      name=q[1].lstrip('#'); a=name.split('/',1); c=db()
      z=c.execute('SELECT c.*,w.slug,w.id wid FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(a[0],a[1] if len(a)>1 else '')).fetchone() if len(a)>1 else None
      if not z:send(self.request,'403 '+name+' :No such channel');continue
      c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(z['wid'],self.uid,'member'));c.execute('INSERT OR IGNORE INTO cmembers VALUES(?,?)',(z['id'],self.uid));c.commit()
      self.joined[z['id']]='#'+z['slug']+'/'+z['name']; c.execute('INSERT OR IGNORE INTO seq(channel_id,last_event_id) VALUES(?,0)',(z['id'],)); c.commit(); self.last[z['id']]=c.execute('SELECT last_event_id FROM seq WHERE channel_id=?',(z['id'],)).fetchone()[0]
      send(self.request,':'+self.nick+'!'+str(self.uid)+'@host JOIN '+self.joined[z['id']]);send(self.request,'353 '+self.nick+' = '+self.joined[z['id']]+' :'+self.nick);send(self.request,'366 '+self.nick+' '+self.joined[z['id']]+' :End of NAMES')
     elif cmd=='PRIVMSG' and len(q)>2:
      name=q[1].lstrip('#').split('/',1);c=db();z=c.execute('SELECT c.* FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(name[0],name[1])).fetchone() if len(name)>1 else None
      if z and self.uid:
       body=q[2].lstrip(':');c.execute('INSERT INTO messages(channel_id,author_id,body,created_at) VALUES(?,?,?,?)',(z['id'],self.uid,body,now()));r=c.execute('SELECT * FROM messages WHERE id=last_insert_rowid()').fetchone();o=msg(c,r);eid=emit(c,z['id'],'message.created',{'message':o});c.commit();self.last[z['id']]=eid
     elif cmd in ('PONG','MODE','WHO','LIST','NAMES','TOPIC'):
      if cmd=='TOPIC':send(self.request,'331 '+self.nick+' * :No topic')
      else:send(self.request,'366 '+self.nick+' * :End of list')
     else:send(self.request,'421 '+self.nick+' '+cmd+' :Unknown command')
  except (OSError,ValueError,KeyError):pass
  finally:
   stop.set()
   if self.nick:
    with nl:nicks.pop(self.nick,None)
class S(socketserver.ThreadingTCPServer):
 allow_reuse_address=True;daemon_threads=True
if __name__=='__main__':S(('0.0.0.0',6667),IRC).serve_forever()
