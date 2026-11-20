import socket,threading,time,re,json,traceback
from core import db,auth,channel,msg,emit
clients=[];lock=threading.Lock();nicks=set()
def send(s,line):
 try:s.sendall((line+'\r\n').encode())
 except OSError:pass
def resolve(c,name):
 if not name.startswith('#') or '/' not in name:return None
 slug,cn=name[1:].split('/',1)
 return c.execute('select c.*,w.slug,w.name as workspace_name from channels c join workspaces w on w.id=c.workspace_id where w.slug=? and c.name=? and c.is_dm=0',(slug,cn)).fetchone()
def names(c,ch):
 return [r['username'] for r in c.execute('select u.username from users u join cmembers m on m.user_id=u.id where m.channel_id=?',(ch['id'],))]
def client(s):
 token=None;uid=None;nick=None;registered=False;joined={};seen={};buf=b''
 with lock:clients.append(s)
 try:
  s.settimeout(.2)
  while True:
   try:
    x=s.recv(4096)
    if not x:break
    buf+=x
   except socket.timeout: x=None
   while b'\n' in buf:
    raw,buf=buf.split(b'\n',1); line=raw.decode(errors='ignore').rstrip('\r'); a=line.split(); cmd=a[0].upper() if a else ''
    if cmd=='PASS':
     token=a[1] if len(a)>1 else '';c=db();u=auth(c,token);uid=u['id'] if u else None;c.close()
     if not uid:send(s,':irc 464 * :Password incorrect')
    elif cmd=='NICK':
     new=a[1] if len(a)>1 else 'guest'
     with lock:collision=new in nicks and new!=nick
     if collision or registered:send(s,':irc 433 * '+new+' :Nickname in use')
     else:nick=new
    elif cmd=='USER':
     if not uid:
      send(s,':irc 464 * :Password incorrect')
     elif nick and not registered:
      registered=True
      with lock:nicks.add(nick)
      for n in ('001','002','003','004','005'):send(s,':irc '+n+' '+nick+' :Huddle')
      send(s,':irc 422 '+nick+' :No MOTD')
    elif cmd=='PING':send(s,'PONG :'+(a[-1].lstrip(':') if a else ''))
    elif cmd=='QUIT':return
    elif cmd=='JOIN' and len(a)>1:
     c=db();ch=resolve(c,a[1])
     if not ch:send(s,':irc 403 '+(nick or '*')+' '+a[1]+' :No such channel')
     else:
      c.execute('insert or ignore into cmembers values(?,?)',(ch['id'],uid));c.execute('insert or ignore into seq(channel_id,last_event_id) values(?,0)',(ch['id'],));c.commit();joined[ch['id']]=a[1];seen[ch['id']]=c.execute('select last_event_id from seq where channel_id=?',(ch['id'],)).fetchone()[0]
      send(s,':'+nick+'!'+nick+'@huddle JOIN '+a[1]);send(s,':irc 353 '+nick+' = '+a[1]+' :'+ ' '.join(names(c,ch)));send(s,':irc 366 '+nick+' '+a[1]+' :End of NAMES')
     c.close()
    elif cmd=='PRIVMSG' and len(a)>=3:
     target=a[1];body=line.split(':',1)[1] if ':' in line else ' '.join(a[2:])
     c=db();ch=resolve(c,target)
     if ch and uid and c.execute('select 1 from cmembers where channel_id=? and user_id=?',(ch['id'],uid)).fetchone():
      c.execute('insert into messages(channel_id,author_id,body,created_at) values(?,?,?,?)',(ch['id'],uid,body,__import__('core').now()));r=c.execute('select * from messages where id=last_insert_rowid()').fetchone();c.commit();c.execute('begin immediate');emit(c,ch['id'],'message.created',{'message':msg(c,r)});c.commit()
     c.close()
    elif cmd=='NAMES' and len(a)>1:
     c=db();ch=resolve(c,a[1])
     if ch:send(s,':irc 353 '+nick+' = '+a[1]+' :'+ ' '.join(names(c,ch)));send(s,':irc 366 '+nick+' '+a[1]+' :End of NAMES')
     else:send(s,':irc 403 '+nick+' '+a[1]+' :No such channel')
     c.close()
    elif cmd=='TOPIC' and len(a)>1:
     c=db();ch=resolve(c,a[1])
     if ch:
      topic=ch['topic'];send(s,':irc '+('332 '+nick+' '+a[1]+' :'+topic if topic else '331 '+nick+' '+a[1]+' :No topic'))
     c.close()
    elif cmd in ('PONG','MODE','WHO','LIST'):pass
    elif cmd and cmd not in ('PASS','NICK','USER'):send(s,':irc 421 '+(nick or '*')+' '+cmd+' :Unknown command')
   c=db()
   for chid,target in list(joined.items()):
    for r in c.execute('select event_id,type,payload from events where channel_id=? and event_id>? order by event_id',(chid,seen.get(chid,0))):
     seen[chid]=r['event_id'];p=json.loads(r['payload']);m=p.get('message') or {}
     if r['type'] in ('message.created','message.reply') and m.get('author_id')!=uid:
      au=m.get('author') or {};send(s,':'+au.get('username','user')+'!'+au.get('username','user')+'@huddle PRIVMSG '+target+' :'+m.get('body',''))
   c.close();time.sleep(.05)
 except Exception:
  traceback.print_exc()
 finally:
  with lock:
   if s in clients:clients.remove(s)
   if nick:nicks.discard(nick)
  try:s.close()
  except OSError:pass
srv=socket.socket();srv.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);srv.bind(('0.0.0.0',6667));srv.listen(100)
while True:
 s,_=srv.accept();threading.Thread(target=client,args=(s,),daemon=True).start()
