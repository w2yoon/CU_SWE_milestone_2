import sqlite3, json, time, os, hashlib, secrets, re
DB='/app/huddle.db'
def conn():
    c=sqlite3.connect(DB,timeout=5,check_same_thread=False)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=5000')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS channel_seq(channel_id INTEGER PRIMARY KEY,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload_json TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS dms(user1 INTEGER,user2 INTEGER,channel_id INTEGER UNIQUE);
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,data BLOB);
    CREATE TABLE IF NOT EXISTS message_files(message_id INTEGER,file_id INTEGER UNIQUE);
    CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT UNIQUE,name TEXT,creator_id INTEGER,user_ids TEXT);
    CREATE TABLE IF NOT EXISTS ws_presence_live(session TEXT PRIMARY KEY,user_id INTEGER,channel_id INTEGER,updated_at REAL);
    CREATE TABLE IF NOT EXISTS user_events(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,payload_json TEXT);
    ''')
    return c
def now(): return time.strftime('%Y-%m-%dT%H:%M:%S.000Z',time.gmtime())
def user(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def token(uid):
    t=secrets.token_urlsafe(24); c=conn(); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); c.commit(); c.close(); return t
def uid(t):
    if not t:return None
    c=conn(); r=c.execute('SELECT user_id FROM tokens WHERE token=?',(t,)).fetchone(); c.close(); return r[0] if r else None
def event(c,channel,typ,payload):
    c.execute('INSERT OR IGNORE INTO channel_seq(channel_id,last_event_id) VALUES(?,0)',(channel,))
    n=c.execute('SELECT last_event_id+1 FROM channel_seq WHERE channel_id=?',(channel,)).fetchone()[0]
    c.execute('UPDATE channel_seq SET last_event_id=? WHERE channel_id=?',(n,channel))
    c.execute('INSERT INTO events VALUES(?,?,?,?)',(channel,n,typ,json.dumps(payload)))
    return n
def wsrow(c,slug): return c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
def channel(c,i): return c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
def member(c,ch,u):
    r=c.execute('''SELECT 1 FROM channel_members cm JOIN channels x ON x.id=cm.channel_id WHERE cm.channel_id=? AND cm.user_id=?''',(ch,u)).fetchone(); return bool(r)
def mention_ids(c,r):
    text=r['body']; author=r['author_id']; ch=r['channel_id']
    cr=c.execute('SELECT workspace_id FROM channels WHERE id=?',(ch,)).fetchone()
    if not cr:return []
    ws=cr[0]; ids=set()
    channel_users={x[0] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch,))}
    for m in re.finditer(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_]+)',text):
        token=m.group(1)
        if token=='channel': ids.update(channel_users)
        elif token=='here':
            # WebSocket subscribers are represented by active channel subscriptions in server;
            # ordinary usernames and groups remain durable and deterministic here.
            pass
        else:
            g=c.execute('SELECT user_ids FROM groups WHERE workspace_id=? AND handle=?',(ws,token)).fetchone()
            if g:
                try: ids.update(int(x) for x in json.loads(g[0]))
                except: pass
            else:
                q=c.execute('SELECT id FROM users WHERE username=?',(token,)).fetchone()
                if q: ids.add(q[0])
    return sorted(x for x in ids if x!=author and x in channel_users)
def msgobj(c,r):
    a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    rr=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)).fetchall()
    fs=c.execute('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE id IN (SELECT file_id FROM message_files WHERE message_id=?)',(r['id'],)).fetchall()
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':c.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[dict(x) for x in fs],'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(z) for z in x['ids'].split(',')]} for x in rr],'mentions':mention_ids(c,r)}
