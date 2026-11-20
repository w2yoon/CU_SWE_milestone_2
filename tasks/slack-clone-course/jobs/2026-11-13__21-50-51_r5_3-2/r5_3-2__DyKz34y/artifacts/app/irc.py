import socket,threading,sqlite3,time
from core import DB,token_user,add_event,message_obj,now
NICKS=set();NLOCK=threading.Lock()
def send(s,x):
    try:s.sendall((x+'\r\n').encode());return True
    except:return False
def client(sock,addr):
    c=sqlite3.connect(DB,timeout=5);c.row_factory=sqlite3.Row
    token=None;uid=None;nick=None;registered=False;collision=False;joined={};last={}
    sock.settimeout(.2);buf=b''
    def numeric(n,text):
        send(sock,':huddle %s %s :%s'%(n,nick or '*',text))
    def lookup(name):
        if not name.startswith('#') or '/' not in name:return None
        z=name[1:].split('/',1)
        return c.execute('SELECT ch.*,w.slug FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE w.slug=? AND ch.name=?',(z[0],z[1])).fetchone()
    def deliver():
        for name,cid in list(joined.items()):
            rows=c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last.get(cid,0))).fetchall()
            for r in rows:
                last[cid]=r['event_id']
                if r['type']!='message.created':continue
                try:
                    m=json.loads(r['payload_json'])
                except: continue
                if m.get('author_id')==uid:continue
                au=m.get('author') or {}
                send(sock,':%s!%s@host PRIVMSG %s :%s'%(au.get('username','user'),au.get('username','user'),name,m.get('body','')))
    import json
    try:
        while True:
            deliver()
            try:x=sock.recv(4096)
            except socket.timeout:x=None
            if x is None:continue
            if not x:break
            buf+=x
            while b'\r\n' in buf or b'\n' in buf:
                sep=b'\r\n' if b'\r\n' in buf else b'\n';raw,buf=buf.split(sep,1)
                line=raw.decode(errors='replace'); parts=line.split(' ',1);cmd=parts[0].upper();rest=parts[1] if len(parts)>1 else ''
                if cmd=='PASS':
                    token=rest.strip();u=token_user(c,token);uid=u['id'] if u else None
                    if not u: numeric('464','Password incorrect')
                elif cmd=='NICK':
                    nn=rest.split()[0] if rest else ''
                    with NLOCK: used=nn in NICKS
                    if c.execute('SELECT 1 FROM users WHERE username=?',(nn,)).fetchone() or used:
                        if not registered: collision=True;send(sock,':huddle 433 * %s :Nickname is already in use'%nn)
                    else:nick=nn
                elif cmd=='USER':
                    if not registered and token and uid and nick:
                        with NLOCK:NICKS.add(nick)
                        registered=True
                        if collision:numeric('001','Welcome')
                        else:
                            for n in ('001','002','003','004','005'):numeric(n,'Welcome')
                            numeric('422','MOTD File is missing')
                    elif not uid:numeric('464','Password incorrect')
                elif cmd=='PING':send(sock,':huddle PONG huddle '+rest)
                elif cmd=='QUIT':return
                elif cmd=='JOIN':
                    name=rest.split()[0] if rest else '';ch=lookup(name)
                    if not ch:numeric('403','No such channel');continue
                    cid=ch['id'];joined[name]=cid
                    last[cid]=c.execute('SELECT COALESCE(last_event_id,0) FROM channel_seq WHERE channel_id=?',(cid,)).fetchone()[0]
                    send(sock,':%s!%s@host JOIN %s'%(nick,nick,name))
                    names=[x['username'] for x in c.execute('SELECT u.username FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?',(cid,))]
                    send(sock,':huddle 353 %s = %s :%s'%(nick,name,' '.join(names)));send(sock,':huddle 366 %s %s :End of /NAMES list.'%(nick,name))
                elif cmd=='PRIVMSG':
                    a=rest.split(' :',1);target=a[0].strip() if a else '';text=a[1] if len(a)>1 else ''
                    ch=lookup(target)
                    if ch and uid:
                        c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch['id'],uid,text,None,now()))
                        mid=c.execute('SELECT last_insert_rowid()').fetchone()[0];m=message_obj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone());add_event(c,ch['id'],'message.created',m);c.commit()
                elif cmd=='NAMES':
                    ch=lookup(rest.split()[0] if rest else ''); 
                    if ch:names=[x['username'] for x in c.execute('SELECT u.username FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?',(ch['id'],))];send(sock,':huddle 353 %s = %s :%s'%(nick,rest.split()[0],' '.join(names)));send(sock,':huddle 366 %s %s :End of /NAMES list.'%(nick,rest.split()[0]))
                elif cmd=='TOPIC':
                    ch=lookup(rest.split()[0] if rest else '')
                    if ch:
                        if ch['topic']:send(sock,':huddle 332 %s %s :%s'%(nick,rest.split()[0],ch['topic']))
                        else:send(sock,':huddle 331 %s %s :No topic is set'%(nick,rest.split()[0]))
                elif cmd in ('PONG','MODE','WHO','LIST'):pass
                else:numeric('421',cmd+' :Unknown command')
    except:pass
    finally:
        if registered:
            with NLOCK:NICKS.discard(nick)
        try:sock.close()
        except:pass
        c.close()
def main():
    s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('0.0.0.0',6667));s.listen(100)
    while True:
        x,a=s.accept();threading.Thread(target=client,args=(x,a),daemon=True).start()
if __name__=='__main__':main()
