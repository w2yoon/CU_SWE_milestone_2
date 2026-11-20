import sqlite3, os, json, hashlib, secrets, re, datetime, threading, time, mimetypes, uuid

DB='/app/huddle.db'
_lock=threading.RLock()
def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
    c=sqlite3.connect(DB,timeout=5,check_same_thread=False)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=5000')
    return c
def init():
    c=db()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS channel_seq(channel_id INTEGER PRIMARY KEY,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload_json TEXT,created_at TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,data BLOB);
    CREATE TABLE IF NOT EXISTS message_files(message_id INTEGER,file_id INTEGER,PRIMARY KEY(message_id,file_id));
    CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT UNIQUE,name TEXT,creator_id INTEGER,user_ids TEXT);
    CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,creator_id INTEGER,expires_at TEXT,max_uses INTEGER,uses INTEGER DEFAULT 0,invited_user_id INTEGER,revoked INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS reads(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
    CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,node_id INTEGER);
    CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY AUTOINCREMENT,payload TEXT);
    '''); c.commit(); c.close()
init()
def rowdict(r): return dict(r) if r else None
def user_obj(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def token_user(tok):
    c=db(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(tok or '',)).fetchone(); c.close(); return r
def register(u,p,d=''):
    if not re.fullmatch(r'[A-Za-z0-9_]+',u or '') or len(p or '')<8: return None,'bad'
    try:
        c=db(); c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(u,p,d or u)); c.commit(); r=c.execute('SELECT * FROM users WHERE username=?',(u,)).fetchone(); t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id'])); c.commit(); c.close(); return (user_obj(r),t),None
    except sqlite3.IntegrityError: return None,'dup'
def login(u,p):
    c=db(); r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(u,p)).fetchone()
    if not r: c.close(); return None
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id'])); c.commit(); c.close(); return user_obj(r),t
def wsrow(slug):
    c=db(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def member(c,w,u):
    return c.execute('SELECT * FROM members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone()
def chan(c,i): return c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
def accessible(c,ch,u):
    if not ch:return False
    if ch['is_dm']: return bool(c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u)).fetchone())
    m=member(c,ch['workspace_id'],u)
    return bool(m and (not ch['is_private'] or c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u)).fetchone()))
def add_event(c,cid,typ,payload):
    c.execute('INSERT OR IGNORE INTO channel_seq(channel_id,last_event_id) VALUES(?,0)',(cid,))
    c.execute('UPDATE channel_seq SET last_event_id=last_event_id+1 WHERE channel_id=?',(cid,))
    eid=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(cid,)).fetchone()[0]
    c.execute('INSERT INTO events VALUES(?,?,?,?,?)',(cid,eid,typ,json.dumps(payload),now())); return eid
def msg_obj(c,r):
    if not r:return None
    a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    rr=[]
    for x in c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)):
        rr.append({'emoji':x['emoji'],'count':x['n'],'user_ids':[int(z) for z in x['ids'].split(',')]})
    n=c.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]
    fs=[]
    for f in c.execute('SELECT f.* FROM files f JOIN message_files mf ON mf.file_id=f.id WHERE mf.message_id=?',(r['id'],)):
        fs.append({k:f[k] for k in ('id','uploader_id','filename','content_type','size','created_at')})
    mentions=[]; text=r['body'] or ''
    names=set(re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_@-]+)',text))
    members=[z['user_id'] for z in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(r['channel_id'],))]
    for name in names:
        if name=='channel':
            mentions += [z for z in members if z!=r['author_id']]
        elif name=='here':
            mentions += [z['user_id'] for z in c.execute('SELECT DISTINCT user_id FROM presence') if z['user_id'] in members and z['user_id']!=r['author_id']]
        else:
            ur=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
            if ur and ur['id'] in members and ur['id']!=r['author_id']: mentions.append(ur['id'])
            gr=c.execute('SELECT user_ids FROM groups_ WHERE workspace_id=(SELECT workspace_id FROM channels WHERE id=?) AND handle=?',(r['channel_id'],name)).fetchone()
            if gr:
                try: mentions += [z for z in json.loads(gr['user_ids']) if z in members and z!=r['author_id']]
                except: pass
    mentions=list(dict.fromkeys(mentions))
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_obj(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':n,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':fs,'reactions':rr,'mentions':mentions}
def channel_obj(r):
    d={k:r[k] for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
    d['is_private']=bool(d['is_private']); d['is_dm']=bool(d['is_dm']); d['is_archived']=bool(d['is_archived'])
    return d
def workspace_obj(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
