import socket,threading,core,json,time
nicks=set(); sessions=[]; lk=threading.Lock()
def send(s,x):
    try:s.sendall((x+'\r\n').encode()); return True
    except:return False
def lookup(name):
    z=name.lstrip('#').split('/',1)
    if len(z)!=2:return None
    c=core.conn(); r=c.execute('SELECT x.id,w.slug,x.name FROM channels x JOIN workspaces w ON w.id=x.workspace_id WHERE w.slug=? AND x.name=?',(z[0],z[1])).fetchone(); c.close(); return r
def bridge():
    last={}
    while True:
        try:
            c=core.conn(); rows=c.execute('SELECT channel_id,event_id,type,payload_json FROM events ORDER BY channel_id,event_id').fetchall(); c.close()
            for r in rows:
                if r['event_id']<=last.get(r['channel_id'],0):continue
                last[r['channel_id']]=r['event_id']; p=json.loads(r['payload_json'])
                if r['type'] not in ('message.created','message.reply'):continue
                m=p.get('message') or {}; body=m.get('body',''); aid=m.get('author_id')
                c=core.conn(); ar=c.execute('SELECT username FROM users WHERE id=?',(aid,)).fetchone(); nick=ar['username'] if ar else 'user'; c.close()
                with lk:
                    for q in list(sessions):
                        if r['channel_id'] in q['joined'] and q['uid']!=aid:
                            send(q['sock'],':'+nick+'!user@host PRIVMSG '+q['joined'][r['channel_id']]+' :'+body)
        except:pass
        time.sleep(.05)
def serve(s):
    buf=''; token=None; nick=None; uid=None; registered=False; collided=False; joined={}
    def out(x):send(s,x)
    with lk:sessions.append({'sock':s,'uid':None,'joined':joined})
    try:
        while True:
            z=s.recv(4096)
            if not z:return
            buf+=z.decode(errors='ignore')
            while '\r\n' in buf:
                line,buf=linebuf=buf.split('\r\n',1); a=line.split(); cmd=a[0].upper() if a else ''
                if not cmd:continue
                if cmd=='PASS':token=a[1] if len(a)>1 else None
                elif cmd=='NICK':
                    new=a[1] if len(a)>1 else ''
                    with lk: collision=new in nicks and new!=nick
                    if collision: collided=True; out(':huddle 433 '+new+' '+new+' :Nickname is already in use')
                    else:nick=new
                elif cmd=='USER':
                    uid=core.uid(token)
                    if not uid:out(':huddle 464 '+(nick or '*')+' :Password incorrect')
                    elif not registered:
                        registered=True; nick=nick or 'guest'
                        with lk:nicks.add(nick); [q.update(uid=uid) for q in sessions if q['sock'] is s]
                        codes=('001',) if collided else ('001','002','003','004','005','422')
                        for code in codes:out(':huddle '+code+' '+nick+' :Welcome')
                elif cmd=='PING':out('PONG '+(' '.join(a[1:]) if len(a)>1 else ''))
                elif cmd=='JOIN' and registered and len(a)>1:
                    r=lookup(a[1])
                    if not r:out(':huddle 403 '+nick+' '+a[1]+' :No such channel');continue
                    ch=r['id']; joined[ch]=a[1]; out(':'+nick+'!user@host JOIN '+a[1]); out(':huddle 353 '+nick+' = '+a[1]+' :'+nick); out(':huddle 366 '+nick+' '+a[1]+' :End of NAMES')
                elif cmd=='PRIVMSG' and registered and len(a)>2:
                    r=lookup(a[1])
                    if r:
                        body=line.split(' :',1)[1] if ' :' in line else ' '.join(a[2:]).lstrip(':')
                        c=core.conn(); c.execute('BEGIN IMMEDIATE'); c.execute('INSERT INTO messages(channel_id,author_id,body,created_at) VALUES(?,?,?,?)',(r['id'],uid,body,core.now())); mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; obj=core.msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()); core.event(c,r['id'],'message.created',{'message':obj}); c.commit(); c.close()
                elif cmd=='TOPIC' and registered:
                    out(':huddle 331 '+nick+' '+(a[1] if len(a)>1 else '*')+' :No topic is set')
                elif cmd in ('PONG','MODE','WHO','LIST','NAMES'): pass
                elif cmd=='QUIT':return
                else:out(':huddle 421 '+(nick or '*')+' '+a[0]+' :Unknown command')
    finally:
        with lk:
            sessions[:]=[q for q in sessions if q['sock'] is not s]; nicks.discard(nick)
        try:s.close()
        except:pass
def main():
    threading.Thread(target=bridge,daemon=True).start()
    x=socket.socket();x.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);x.bind(('0.0.0.0',6667));x.listen()
    while True:
        s,_=x.accept();threading.Thread(target=serve,args=(s,),daemon=True).start()
main()
