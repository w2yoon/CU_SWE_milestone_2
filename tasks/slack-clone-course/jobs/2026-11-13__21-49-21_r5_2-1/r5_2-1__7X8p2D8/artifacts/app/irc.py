import socket,threading,os,json,time,sqlite3
import core
HOST='0.0.0.0';PORT=6667
clients=[];lock=threading.Lock()
def send(c,s):
 try:c.sendall((s+'\r\n').encode())
 except:pass
def user_for(tok):
 return core.uid(tok)
def worker(c,addr):
 nick=None; uid=None; joined=[]
 with lock: clients.append((c,lambda:uid,lambda:joined))
 try:
  buf=b''
  while True:
   x=c.recv(4096)
   if not x:break
   buf+=x
   while b'\r\n' in buf:
    line,buf=buf.split(b'\r\n',1); line=line.decode(errors='ignore')
    z=line.split(' ',2); cmd=z[0].upper(); args=z[1:]
    if cmd=='PASS': uid=user_for(args[0] if args else '')
    elif cmd=='NICK':
     nick=args[0] if args else 'guest'
     send(c,':huddle 433 * '+nick+' :Nickname in use') if any(x[1]()==nick for x in []) else None
    elif cmd=='USER':
     if uid and nick:
      for n,msg in [('001','Welcome'),('002','Your host'),('003','Created'),('004','huddle'),('005','CHANTYPES=#')]:
       send(c,':huddle '+n+' '+nick+' :'+msg)
      send(c,':huddle 422 '+nick+' :MOTD File is missing')
    elif cmd=='PING':send(c,':huddle PONG '+(args[-1] if args else 'huddle'))
    elif cmd in ('JOIN','NAMES'):
     name=args[0].lstrip(':') if args else ''
     if not name.startswith('#') or '/' not in name: send(c,':huddle 403 '+(nick or '*')+' '+name+' :No such channel');continue
     slug,chname=name[1:].split('/',1);db=core.conn();ch=db.execute('SELECT c.*,w.slug FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(slug,chname)).fetchone()
     if not ch:send(c,':huddle 403 '+(nick or '*')+' '+name+' :No such channel');continue
     if cmd=='JOIN':
      joined.append(ch['id']);send(c,':'+(nick or '*')+'!user@localhost JOIN '+name)
     rows=db.execute('SELECT u.username FROM users u JOIN cm m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall(); names=' '.join(x[0] for x in rows)
     send(c,':huddle 353 '+(nick or '*')+' = '+name+' :'+names);send(c,':huddle 366 '+(nick or '*')+' '+name+' :End of NAMES');db.close()
    elif cmd=='PRIVMSG' and len(args)>=2:
     name=args[0];body=args[1].lstrip(':')
     if name.startswith('#') and '/' in name:
      slug,chname=name[1:].split('/',1);db=core.conn();ch=db.execute('SELECT c.* FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(slug,chname)).fetchone()
      if ch and uid:
       db.execute('INSERT OR IGNORE INTO cm VALUES(?,?)',(ch['id'],uid));cur=db.execute('INSERT INTO messages(channel_id,author_id,body,created_at) VALUES(?,?,?,?)',(ch['id'],uid,body,core.now()));m=core.mobj(db,db.execute('SELECT * FROM messages WHERE id=?',(cur.lastrowid,)).fetchone());core.emit(db,ch['id'],'message.created',m);db.commit()
      db.close()
    elif cmd=='QUIT':break
    elif cmd=='NICK' and False:pass
    elif cmd not in ('USER','PASS','NICK','PING','JOIN','NAMES','PRIVMSG','QUIT','WHO','LIST','TOPIC','MODE','PONG'):send(c,':huddle 421 '+(nick or '*')+' '+cmd+' :Unknown command')
 finally:
  try: c.close()
  except Exception: pass
def main():
 core.init();s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind((HOST,PORT));s.listen(50)
 while True:
  c,a=s.accept();threading.Thread(target=worker,args=(c,a),daemon=True).start()
if __name__=='__main__':main()
