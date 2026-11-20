import sqlite3, os, json, hashlib, secrets, re
from datetime import datetime, timezone
DB='/app/huddle.db'
def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
    c=sqlite3.connect(DB,timeout=5,check_same_thread=False)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=5000')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '',created_at TEXT);
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload_json TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS channel_seq(channel_id INTEGER PRIMARY KEY,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS read_state(channel_id INTEGER,user_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS ws_presence(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS ws_online(user_id INTEGER PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS user_events(id INTEGER PRIMARY KEY AUTOINCREMENT,type TEXT,payload_json TEXT);
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,data BLOB,created_at TEXT);
    CREATE TABLE IF NOT EXISTS message_files(message_id INTEGER,file_id INTEGER UNIQUE);
    CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,expires_at TEXT,max_uses INTEGER,uses INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER);
    CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));
    ''')
    try: c.execute('ALTER TABLE invitations ADD COLUMN invited_username TEXT')
    except sqlite3.OperationalError: pass
    return c
def user_obj(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def auth(c,t):
    r=c.execute('SELECT u.* FROM tokens x JOIN users u ON u.id=x.user_id WHERE x.token=?',(t or '',)).fetchone()
    return r
def token(c,uid):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); return t
def emit(c,ch,typ,payload):
    c.execute('INSERT OR IGNORE INTO channel_seq(channel_id,last_event_id) VALUES(?,0)',(ch,))
    c.execute('UPDATE channel_seq SET last_event_id=last_event_id+1 WHERE channel_id=?',(ch,))
    n=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(ch,)).fetchone()[0]
    c.execute('INSERT INTO events VALUES(?,?,?,?)',(ch,n,typ,json.dumps(payload,separators=(',',':'))))
    return n
def reactions(c,mid):
    rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY emoji,user_id',(mid,)).fetchall()
    out=[]
    for e in sorted(set(x['emoji'] for x in rows)):
        ids=[x['user_id'] for x in rows if x['emoji']==e]
        out.append({'emoji':e,'count':len(ids),'user_ids':ids})
    return out
def msg_obj(c,r):
    if not r:return None
    a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    mentions=[]
    for m in re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)',r['body']):
        u=c.execute('SELECT id FROM users WHERE username=?',(m,)).fetchone()
        if u and u['id']!=r['author_id'] and c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(r['channel_id'],u['id'])).fetchone(): mentions.append(u['id'])
    for gh in re.findall(r'(?<![\\w@])@([A-Za-z0-9_-]+)',r['body']):
        g=c.execute('SELECT id FROM groups_ WHERE workspace_id=(SELECT workspace_id FROM channels WHERE id=?) AND handle=?',(r['channel_id'],gh)).fetchone()
        if g:
            mentions += [x[0] for x in c.execute('SELECT gm.user_id FROM group_members gm JOIN channel_members cm ON cm.user_id=gm.user_id WHERE gm.group_id=? AND cm.channel_id=? AND gm.user_id!=?',(g['id'],r['channel_id'],r['author_id']))]
    if '@here' in r['body']:
        mentions += [x[0] for x in c.execute('SELECT cm.user_id FROM channel_members cm JOIN ws_online wo ON wo.user_id=cm.user_id WHERE cm.channel_id=? AND cm.user_id!=?',(r['channel_id'],r['author_id']))]
    if '@channel' in r['body']:
        mentions += [x['user_id'] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(r['channel_id'],r['author_id']))]
    mentions=list(dict.fromkeys(mentions))
    fs=[]
    for f in c.execute('SELECT f.id,f.uploader_id,f.filename,f.content_type,f.size,f.created_at FROM files f JOIN message_files mf ON mf.file_id=f.id WHERE mf.message_id=?',(r['id'],)):
        fs.append(dict(f))
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_obj(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':fs,'reactions':reactions(c,r['id']),'mentions':mentions}
def channel(c,id):
    return c.execute('SELECT * FROM channels WHERE id=?',(id,)).fetchone()
def member(c,ch,uid):
    r=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch,uid)).fetchone()
    if r:return True
    x=channel(c,ch)
    return bool(x and x['is_dm']==0 and c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(x['workspace_id'],uid)).fetchone())
