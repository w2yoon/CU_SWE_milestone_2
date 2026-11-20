import socket,threading,sqlite3,os,time
DB='/app/huddle.db'
def db():
 c=sqlite3.connect(DB,timeout=5);c.row_factory=sqlite3.Row;return c
clients=[];nicks=set();lock=threading.Lock()
def send(s,x):
 try:s.sendall((x+'\r\n').encode())
 except:pass
def client(sock,addr):
 token=None;nick=None;uid=None;registered=False;joined=[];nick_valid=False;retry=False
 with lock:clients.append((sock,joined))
 try:
  f=sock.makefile('r',encoding='utf8',errors='ignore')
  for line in f:
   a=line.rstrip('\r\n'); parts=a.split(' ',1); cmd=parts[0].upper(); rest=parts[1] if len(parts)>1 else ''
   if cmd=='PASS':token=rest.strip()
   elif cmd=='NICK':
    newnick=rest.strip()
    with lock: taken=newnick in nicks
    if taken:
     send(sock,':irc 433 * '+newnick+' :Nickname in use'); nick_valid=False; retry=True; continue
    if nick:
     with lock:nicks.discard(nick)
    nick=newnick
    nick_valid=True
    with lock:nicks.add(nick)
   elif cmd=='USER':
    if not nick or not nick_valid: continue
    c=db();z=c.execute('SELECT user_id FROM tokens WHERE token=?',(token,)).fetchone();c.close()
    if not z:send(sock,':irc 464 * :Password incorrect');continue
    uid=z['user_id'];registered=True
    nums=[('001','Welcome'),('002','Your host'),('003','Created'),('004','irc huddle'),('005','CHANTYPES=#')]
    if retry: nums=nums[:1]
    for n,msg in nums:send(sock,':irc '+n+' '+nick+' :'+msg)
    if not retry: send(sock,':irc 422 '+nick+' :MOTD File is missing')
   elif cmd=='PING':send(sock,'PONG '+rest)
   elif cmd=='QUIT':
    try:sock.shutdown(socket.SHUT_RDWR)
    except:pass
    break
   elif cmd=='JOIN' and registered:
    ch=rest.split()[0];c=db();z=c.execute("SELECT ch.*,w.slug FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE '#'+w.slug||'/'||ch.name=?",(ch,)).fetchone();names=[]
    if z:names=[r['username'] for r in c.execute('SELECT u.username FROM channel_members cm JOIN users u ON u.id=cm.user_id WHERE cm.channel_id=?',(z['id'],))]
    c.close()
    if not z:send(sock,':irc 403 '+nick+' '+ch+' :No such channel');continue
    joined.append(z['id']);send(sock,':'+nick+'!user@localhost JOIN '+ch);send(sock,':irc 353 '+nick+' = '+ch+' :'+(' '.join(names)));send(sock,':irc 366 '+nick+' '+ch+' :End of NAMES list')
   elif cmd=='NAMES' and registered:send(sock,':irc 366 '+nick+' '+rest+' :End of NAMES list')
   elif cmd in ('PONG','MODE','WHO','LIST','NAMES','TOPIC'):
    if cmd=='NAMES' and registered: send(sock,':irc 366 '+str(nick)+' '+rest+' :End of NAMES list')
   elif cmd=='PRIVMSG' and registered:
    q=rest.split(' ',1);ch=q[0];text=q[1].lstrip(':') if len(q)>1 else '';c=db();z=c.execute("SELECT ch.* FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE '#'+w.slug||'/'||ch.name=?",(ch,)).fetchone()
    if z:
     c.execute('INSERT INTO messages(channel_id,author_id,body,created_at) VALUES(?,?,?,?)',(z['id'],uid,text,time.strftime('%Y-%m-%dT%H:%M:%SZ')));c.commit()
    c.close()
   elif cmd not in ('PASS','NICK','USER','PING','QUIT','JOIN','NAMES','PRIVMSG','PONG','MODE','WHO','LIST','TOPIC'):
    send(sock,':irc 421 '+str(nick or "*")+' '+cmd+' :Unknown command')
 finally:
  with lock:
   try:clients.remove((sock,joined))
   except:pass
  with lock:
   if nick:nicks.discard(nick)
  sock.close()
s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',6667));s.listen()
while True:
 x,a=s.accept();threading.Thread(target=client,args=(x,a),daemon=True).start()
