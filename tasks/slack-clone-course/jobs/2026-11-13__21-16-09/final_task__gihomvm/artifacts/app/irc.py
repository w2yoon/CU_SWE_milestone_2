import socketserver, threading, time, json, re, socket
from core import db, token_user, create_message

clients=set(); nick_lock=threading.Lock(); nicks={}
def send(w,line):
    try:w.request.sendall((line+'\r\n').encode())
    except: pass
def chan_for(name):
    if not name.startswith('#') or '/' not in name:return None
    slug,cname=name[1:].split('/',1); c=db()
    return c.execute('SELECT c.*,w.slug FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(slug,cname)).fetchone()
def cname(r): return '#'+r['slug']+'/'+r['name']
def user_prefix(h):
    return (h.nick or '*')+'!'+str(h.uid)+'@host'
def poll(h):
    while h.alive:
        try:
            c=db()
            for name,chid in list(h.joined.items()):
                last=h.last.get(chid,0)
                for r in c.execute('SELECT event_id,type,payload_json FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(chid,last)):
                    h.last[chid]=r['event_id']
                    if r['type'] not in ('message.created','message.reply'):continue
                    z=json.loads(r['payload_json']);m=z.get('message') or {}
                    if m.get('author_id')==h.uid:continue
                    a=m.get('author') or {}; nick=a.get('username') or 'user'
                    send(h,f':{nick}!{m.get("author_id")}@host PRIVMSG {name} :{m.get("body","")}')
        except: pass
        time.sleep(.05)
class H(socketserver.StreamRequestHandler):
    def setup(self):
        super().setup();self.nick=None;self.uid=None;self.token=None;self.passok=False;self.registered=False;self.alive=True;self.joined={};self.last={}
    def finish(self):
        self.alive=False
        with nick_lock:
            if self.nick and nicks.get(self.nick) is self:nicks.pop(self.nick,None)
        super().finish()
    def register(self):
        if self.registered or not self.passok or not self.nick:return
        c=db();u=token_user(c,self.token)
        if not u:
            send(self,':huddle 464 * :Password incorrect');self.request.close();self.alive=False;return
        self.uid=u['id'];self.registered=True
        send(self,f':huddle 001 {self.nick} :Welcome to Huddle, {self.nick}')
        def registration_tail():
            time.sleep(0.12)
            if not self.alive: return
            for n,text in [('002',':Your host is huddle'),('003',':This server was created today'),('004',':huddle huddle 1'),('005',':CHANTYPES=# :are supported')]:
                send(self,f':huddle {n} {self.nick} {text}')
            time.sleep(0.03)
            if self.alive: send(self,f':huddle 422 {self.nick} :MOTD File is missing')
        threading.Thread(target=registration_tail,daemon=True).start()
        threading.Thread(target=poll,args=(self,),daemon=True).start()
    def handle_cmd(self,line):
        a=line.strip().split(' ',2); cmd=a[0].upper() if a else ''
        arg=a[1] if len(a)>1 else ''; rest=a[2] if len(a)>2 else ''
        tail=rest if rest else arg
        tail=tail[1:] if tail.startswith(':') else tail
        if cmd=='PASS':
            self.token=arg.lstrip(':');self.passok=bool(token_user(db(),self.token))
            if not self.passok: send(self,':huddle 464 * :Password incorrect')
        elif cmd=='NICK':
            n=arg
            with nick_lock:
                if nicks.get(n) not in (None,self):
                    send(self,f':huddle 433 * {n} :Nickname is already in use');return
                if self.nick:nicks.pop(self.nick,None)
                self.nick=n;nicks[n]=self
            self.register()
        elif cmd=='USER':
            if not self.passok:
                send(self,':huddle 464 * :Password incorrect');self.alive=False
                try:self.request.shutdown(socket.SHUT_RDWR)
                except:pass
            else:self.register()
        elif not self.registered:
            if cmd in ('PING',):send(self,':huddle PONG huddle '+tail)
            elif not self.passok and cmd=='USER':send(self,':huddle 464 * :Password incorrect'); self.alive=False; self.request.shutdown( socket.SHUT_RDWR )
        elif cmd=='PING':send(self,':huddle PONG huddle '+tail)
        elif cmd=='PONG':pass
        elif cmd=='QUIT':
            self.alive=False
            try:self.request.shutdown(socket.SHUT_RDWR)
            except:pass
        elif cmd=='JOIN':
            ch=chan_for(arg)
            if not ch:send(self,f':huddle 403 {self.nick} {arg} :No such channel');return
            c=db();c.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(ch['workspace_id'],self.uid,'member'));c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],self.uid));c.commit()
            self.joined[arg]=ch['id']; self.last[ch['id']]=c.execute('SELECT COALESCE(last_event_id,0) FROM channel_seq WHERE channel_id=?',(ch['id'],)).fetchone()[0]
            send(self,f':{user_prefix(self)} JOIN {arg}')
            names=' '.join((x['username'] for x in c.execute('SELECT u.username FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],))))
            send(self,f':huddle 353 {self.nick} = {arg} :{names}');send(self,f':huddle 366 {self.nick} {arg} :End of /NAMES list.')
        elif cmd=='PRIVMSG':
            ch=chan_for(arg)
            if not ch:send(self,f':huddle 403 {self.nick} {arg} :No such channel');return
            body=tail
            c=db();m,e=create_message(c,self.uid,ch['id'],body,None)
            if e:send(self,f':huddle 404 {self.nick} {arg} :Cannot send message')
        elif cmd in ('NAMES','WHO','LIST','MODE'):
            if cmd=='NAMES' and arg:
                ch=chan_for(arg)
                if ch:
                    c=db();names=' '.join(x['username'] for x in c.execute('SELECT u.username FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)))
                    send(self,f':huddle 353 {self.nick} = {arg} :{names}');send(self,f':huddle 366 {self.nick} {arg} :End of /NAMES list.')
            elif cmd=='LIST':send(self,f':huddle 323 {self.nick} :End of /LIST')
            elif cmd=='WHO':send(self,f':huddle 315 {self.nick} {arg} :End of /WHO list.')
        elif cmd=='TOPIC':
            ch=chan_for(arg or tail)
            if ch:
                c=db();send(self,f':huddle 331 {self.nick} {arg or cname(ch)} :No topic is set' if not ch['topic'] else f':huddle 332 {self.nick} {arg or cname(ch)} :{ch["topic"]}')
        else:send(self,f':huddle 421 {self.nick} {cmd} :Unknown command')
    def handle(self):
        for raw in self.rfile:
            if not self.alive:break
            self.handle_cmd(raw.decode(errors='ignore').rstrip('\r\n'))
class S(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
S(('0.0.0.0',6667),H).serve_forever()
