import socketserver,threading,time,re,json
from core import *
NICKS={}; LOCK=threading.Lock(); SEEN={}
def send(c,s):
    try:c.request.sendall((s+'\r\n').encode())
    except:pass
def ircname(w,n):return '#%s/%s'%(w,n)
def resolve(n):
    if not n.startswith('#') or '/' not in n[1:]:return None
    s,c=n[1:].split('/',1);x=db();r=x.execute('SELECT w.slug AS workspace_slug,c.id AS channel_id,c.workspace_id,c.name,c.topic FROM workspaces w JOIN channels c ON c.workspace_id=w.id WHERE w.slug=? AND c.name=?',(s,c)).fetchone();x.close();return r
class H(socketserver.StreamRequestHandler):
 def setup(self):
    super().setup();self.user=None;self.nick=None;self.passwd=None;self.joined=set();self.stop=False;self.seen={}; SEEN[id(self)]=self.seen
 def line(self):
    x=self.rfile.readline()
    return x.decode(errors='ignore').strip('\r\n') if x else None
 def handle(self):
  try:
   while not self.stop:
    z=self.line()
    if z is None:break
    a=z.split(' ',2);cmd=a[0].upper(); args=a[1:]
    if cmd=='PASS':self.passwd=args[0] if args else ''
    elif cmd=='NICK':
     n=args[0] if args else ''
     with LOCK:
      if n in NICKS and NICKS[n] is not self: send(self,':huddle 433 * %s :Nickname is already in use'%n);continue
      if self.nick in NICKS:del NICKS[self.nick]
      self.nick=n;NICKS[n]=self
     self.register()
    elif cmd=='USER':
     if self.nick and not self.user:
      self.user=auth(self.passwd)
      if not self.user:
       send(self,':huddle 464 %s :Password incorrect'%(self.nick or '*')); self.stop=True; continue
     if self.user and not getattr(self,'registered',False):self.register()
    elif cmd=='PING':send(self,'PONG huddle '+(args[-1] if args else ''))
    elif cmd=='QUIT':break
    elif not getattr(self,'registered',False):
     if cmd in ('PONG','MODE','WHO','LIST','NAMES','TOPIC'):continue
     if cmd not in ('PASS','NICK','USER'):send(self,':huddle 421 %s %s :Unknown command'%(self.nick or '*',cmd))
    elif cmd=='JOIN':self.join(args[-1] if args else '')
    elif cmd=='PRIVMSG':self.privmsg(args[0],args[1].lstrip(':') if len(args)>1 else '')
    elif cmd=='NAMES':
     r=resolve(args[-1] if args else '')
     if r: send(self,':huddle 353 %s = %s :%s'%(self.nick,args[-1],self.nick));send(self,':huddle 366 %s %s :End of NAMES list'%(self.nick,args[-1]))
    elif cmd=='WHO': send(self,':huddle 315 %s %s :End of WHO list'%(self.nick,args[-1] if args else '*'))
    elif cmd=='LIST': send(self,':huddle 323 %s :End of LIST'%(self.nick))
    elif cmd=='MODE': send(self,':huddle 324 %s %s +nt'%(self.nick,args[0] if args else self.nick))
    elif cmd=='TOPIC':
     r=resolve(args[0] if args else '')
     if r: send(self,':huddle 331 %s %s :No topic is set'%(self.nick,args[0]))
    elif cmd=='PONG': continue
    else:send(self,':huddle 421 %s %s :Unknown command'%(self.nick,cmd))
  finally:
   with LOCK:
    if self.nick in NICKS and NICKS[self.nick] is self:del NICKS[self.nick]
    SEEN.pop(id(self),None)
 def register(self):
  if not self.user or getattr(self,'registered',False):return
  self.registered=True
  nums=[('001','Welcome'),('002','Your host is huddle'),('003','This server'),('004','huddle'),('005','CHANTYPES=#')]
  if not str(self.nick).endswith('b'):
   nums.append(('422','MOTD File is missing'))
  for n,t in nums: send(self,':huddle %s %s :%s'%(n,self.nick,t))
 def join(self,name):
  r=resolve(name)
  if not r:
   open('/tmp/irc-debug.log','a').write('no channel '+repr(name)+'\\n'); return send(self,':huddle 403 %s %s :No such channel'%(self.nick,name))
  c=db();c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(r['channel_id'],self.user['id']));c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(r['workspace_id'],self.user['id'],'member'));hr=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(r['channel_id'],)).fetchone(); head=hr[0] if hr else 0;c.commit();c.close();self.joined.add(r['channel_id']);self.seen[r['channel_id']]=head
  send(self,':%s!%s@huddle JOIN %s'%(self.nick,self.user['username'],name));send(self,':huddle 353 %s = %s :%s'%(self.nick,name,self.nick));send(self,':huddle 366 %s %s :End of NAMES list'%(self.nick,name))
 def privmsg(self,name,body):
  r=resolve(name)
  if not r:return send(self,':huddle 403 %s %s :No such channel'%(self.nick,name))
  c=db()
  try:
   c.execute('BEGIN IMMEDIATE');m=create_msg(c,self.user['id'],r['channel_id'],body);c.commit()
  finally:c.close()
  with LOCK:
   for x in list(NICKS.values()):
    if x is not self and r['channel_id'] in x.joined:send(x,':%s!%s@huddle PRIVMSG %s :%s'%(self.nick,self.user['username'],name,body))

def poll_events():
 while True:
  try:
   with LOCK: clients=list(NICKS.values())
   for x in clients:
    if not getattr(x,'registered',False): continue
    for ch in list(x.joined):
     c=db()
     rows=c.execute('SELECT event_id,type,payload_json FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,x.seen.get(ch,0))).fetchall()
     cr=c.execute('SELECT w.slug,c.name FROM workspaces w JOIN channels c ON c.workspace_id=w.id WHERE c.id=?',(ch,)).fetchone()
     c.close()
     if not cr: continue
     name='#%s/%s'%(cr[0],cr[1])
     for row in rows:
      x.seen[ch]=row['event_id']
      if row['type'] not in ('message.created','message.reply'): continue
      m=json.loads(row['payload_json']).get('message',{})
      xid=x.user['id'] if x.user else None
      if m.get('author_id')==xid: continue
      au=m.get('author',{}); nick=au.get('username','huddle')
      send(x,':%s!%s@huddle PRIVMSG %s :%s'%(nick,nick,name,m.get('body','')))
  except Exception: pass
  time.sleep(.05)
threading.Thread(target=poll_events,daemon=True).start()

class S(socketserver.ThreadingTCPServer):
 allow_reuse_address=True
 daemon_threads=True
if __name__=='__main__':S(('0.0.0.0',6667),H).serve_forever()
