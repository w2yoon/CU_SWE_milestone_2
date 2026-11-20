import socket,threading,sqlite3,time,re,sys
sys.path.insert(0,'/app'); import core
clients=[]; lock=threading.Lock(); nicks=set()
def snd(c,x): 
    try:c.sendall((x+'\r\n').encode())
    except: pass
def num(c,nick,code,text): snd(c,f':huddle {code} {nick} {text}')
def lookup(name):
    c=core.conn(); m=re.match(r'^#([^/]+)/([^/]+)$',name or '')
    r=c.execute('SELECT c.*,w.slug FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(m.group(1),m.group(2))).fetchone() if m else None
    c.close(); return r
def bridge(st):
    while st['alive']:
        c=core.conn()
        for cid,last in list(st['last'].items()):
            rows=c.execute('SELECT event_id,type,payload_json FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)).fetchall()
            for r in rows:
                st['last'][cid]=r['event_id']; p=__import__('json').loads(r['payload_json']); msg=p.get('message') or {}
                if r['type'] in ('message.created','message.reply') and msg.get('author_id')!=st['uid']:
                    ch=c.execute('SELECT name FROM channels WHERE id=?',(cid,)).fetchone()
                    w=c.execute('SELECT slug FROM workspaces WHERE id=(SELECT workspace_id FROM channels WHERE id=?)',(cid,)).fetchone()
                    if ch and w:
                        snd(st['sock'],f":{msg.get('author',{}).get('username','user')}!user@host PRIVMSG #{w['slug']}/{ch['name']} :{msg.get('body','')}")
        c.close(); time.sleep(.05)
def client(sock):
    st={'sock':sock,'uid':None,'nick':None,'alive':True,'last':{},'joined':set()}
    sock.settimeout(.1); buf=b''; passed=False; registered=False; collision=False
    with lock: clients.append(st)
    threading.Thread(target=bridge,args=(st,),daemon=True).start()
    try:
      while st['alive']:
        try:z=sock.recv(4096)
        except socket.timeout: continue
        if not z:break
        buf+=z
        while b'\r\n' in buf:
          raw,buf=buf.split(b'\r\n',1); line=raw.decode('utf8','replace'); a=line.split(' ',2); cmd=a[0].upper(); args=a[1:]
          if cmd=='PASS':
            passed=bool(args and core.uid(args[0])); st['uid']=core.uid(args[0]) if passed else None
            if not passed:num(sock,st['nick'] or '*','464',':Password incorrect')
          elif cmd=='NICK':
            nn=args[0] if args else ''
            with lock: collision=nn in nicks and nn!=st['nick']
            if collision:num(sock,st['nick'] or '*','433',nn+' :Nickname is already in use')
            else:
              with lock:
                if st['nick']:nicks.discard(st['nick'])
                nicks.add(nn)
              st['nick']=nn
          elif cmd=='USER':
            if not passed:num(sock,st['nick'] or '*','464',':Password required'); continue
            if not registered:
              registered=True; vals=[('001',':Welcome to Huddle')]
              if not collision: vals += [('002',':Your host is huddle'),('003',':This server was created today'),('004',':huddle 1.0 oi'),('005',':CHANTYPES=# :are supported'),('422',':MOTD File is missing')]
              for code,text in vals:num(sock,st['nick'],code,text)
          elif cmd=='PING': snd(sock,':huddle PONG huddle '+(args[-1] if args else ''))
          elif cmd=='QUIT':
            st['alive']=False
            try:sock.shutdown(socket.SHUT_RDWR)
            except:pass
            break
          elif cmd=='JOIN':
            name=args[0] if args else ''; ch=lookup(name)
            if not ch:num(sock,st['nick'] or '*','403',name+' :No such channel'); continue
            st['joined'].add(ch['id']); c=core.conn(); head=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(ch['id'],)).fetchone(); st['last'][ch['id']]=head[0] if head else 0
            snd(sock,f":{st['nick']}!user@host JOIN {name}"); num(sock,st['nick'],'353',f'= {name} :{st["nick"]}'); num(sock,st['nick'],'366',f'{name} :End of NAMES list'); c.close()
          elif cmd=='PRIVMSG' and len(args)>=2:
            name=args[0]; ch=lookup(name)
            if not ch:num(sock,st['nick'] or '*','403',name+' :No such channel'); continue
            text=args[1][1:] if args[1].startswith(':') else ' '.join(args[1:])
            c=core.conn(); c.execute('BEGIN IMMEDIATE'); c.execute('INSERT INTO messages(channel_id,author_id,body,created_at) VALUES(?,?,?,?)',(ch['id'],st['uid'],text,core.now())); mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; msg=core.msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()); core.event(c,ch['id'],'message.created',{'message':msg,'channel_id':ch['id']}); c.commit(); c.close()
          elif cmd in ('PONG','MODE','WHO','LIST','TOPIC','NAMES'): 
            if cmd=='TOPIC': num(sock,st['nick'] or '*','331',(args[0] if args else '*')+' :No topic is set')
          else:num(sock,st['nick'] or '*','421',cmd+' :Unknown command')
    finally:
      st['alive']=False
      with lock:
        if st in clients:clients.remove(st)
        if st['nick']:nicks.discard(st['nick'])
      try:sock.close()
      except:pass
def main():
 s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind(('0.0.0.0',6667)); s.listen(100)
 while True:
  c,_=s.accept(); threading.Thread(target=client,args=(c,),daemon=True).start()
if __name__=='__main__':main()
