import socketserver,socket,select,threading,json,urllib.parse
from core import *
clients={}; lock=threading.Lock()
class S(socketserver.BaseRequestHandler):
 def handle(self):
  s=self.request; s.setblocking(False); nick=None; uid=None; passwd=None; reg=False; collision=False; joined=set(); cursors={}; buf=b''
  def out(x):
   try:s.sendall((x+'\r\n').encode())
   except OSError: raise
  def events():
   c=conn()
   for cid in list(joined):
    last=cursors.get(cid,0)
    for e in c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)):
     cursors[cid]=e['event_id']
     if e['type'] not in ('message.created','message.reply'): continue
     try:
      m=json.loads(e['payload']).get('message',{}); au=c.execute('SELECT username FROM users WHERE id=?',(m.get('author_id'),)).fetchone()
      ch=c.execute('SELECT w.slug,ch.name FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE ch.id=?',(cid,)).fetchone()
      if au and ch and m.get('author_id')!=uid: out(':'+au['username']+'!'+str(m.get('author_id'))+'@huddle PRIVMSG #'+ch['slug']+'/'+ch['name']+' :'+m.get('body',''))
     except OSError: raise
     except Exception: pass
  try:
   while True:
    events()
    ready,_,_=select.select([s],[],[],0.05)
    if not ready: continue
    data=s.recv(8192)
    if not data: break
    buf+=data
    while b'\n' in buf:
     line,buf=buf.split(b'\n',1); z=line.decode(errors='ignore').strip('\r'); a=z.split(); cmd=a[0].upper() if a else ''
     if cmd=='PASS': passwd=' '.join(a[1:])
     elif cmd=='NICK':
      n=a[1] if len(a)>1 else '*'
      with lock:
       if n in clients: collision=True; out(':huddle 433 * '+n+' :Nickname is already in use'); continue
       nick=n; clients[n]=self
     elif cmd=='USER':
      if not nick: continue
      uid=token_uid(conn(),passwd)
      if not uid: out(':huddle 464 * :Password incorrect'); return
      out(':huddle 001 '+nick+' :Welcome')
      if not collision:
       out(':huddle 002 '+nick+' :Your host is huddle'); out(':huddle 003 '+nick+' :Server created'); out(':huddle 004 '+nick+' huddle'); out(':huddle 005 '+nick+' NETWORK=huddle'); out(':huddle 422 '+nick+' :MOTD File is missing')
      reg=True
     elif cmd=='PING': out(':huddle PONG huddle '+(' '.join(a[1:]) if len(a)>1 else ''))
     elif cmd=='QUIT': return
     elif cmd=='JOIN' and reg and len(a)>1:
      name=a[1]; parts=name[1:].split('/',1) if name.startswith('#') else []; c=conn(); r=c.execute('SELECT w.slug,ch.* FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE w.slug=? AND ch.name=?',tuple(parts)).fetchone() if len(parts)==2 else None
      if not r: out(':huddle 403 '+nick+' '+name+' :No such channel'); continue
      joined.add(r['id']); cursors[r['id']]=c.execute('SELECT COALESCE(MAX(event_id),0) FROM events WHERE channel_id=?',(r['id'],)).fetchone()[0]
      out(':'+nick+'!'+str(uid)+'@huddle JOIN '+name); out(':huddle 353 '+nick+' = '+name+' :'+nick); out(':huddle 366 '+nick+' '+name+' :End of NAMES')
     elif cmd=='PRIVMSG' and reg and len(a)>2:
      name=a[1]; text=z.split(' :',1)[1] if ' :' in z else ' '.join(a[2:]); parts=name[1:].split('/',1); c=conn(); r=c.execute('SELECT ch.* FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE w.slug=? AND ch.name=?',tuple(parts)).fetchone() if len(parts)==2 else None
      if r and uid: c.execute('BEGIN IMMEDIATE'); make_msg(c,uid,r['id'],text); c.commit()
     elif cmd in ('PONG','MODE','WHO','LIST','NAMES','TOPIC'):
      if cmd=='TOPIC': out(':huddle 331 '+nick+' :No topic')
     elif reg: out(':huddle 421 '+nick+' '+cmd+' :Unknown command')
  except (OSError,ConnectionError): pass
  finally:
   with lock:
    if nick in clients and clients[nick] is self: del clients[nick]
class Server(socketserver.ThreadingTCPServer):
 allow_reuse_address=True
 daemon_threads=True
Server(('0.0.0.0',6667),S).serve_forever()
