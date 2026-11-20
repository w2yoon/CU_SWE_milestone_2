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
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT "UTC",avatar_url TEXT DEFAULT "",status_text TEXT DEFAULT "",status_emoji TEXT DEFAULT "");
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT "open");
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT "",is_archived INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS channel_seq(channel_id INTEGER PRIMARY KEY,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload_json TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,data BLOB);
    CREATE TABLE IF NOT EXISTS message_files(message_id INTEGER,file_id INTEGER,PRIMARY KEY(message_id,file_id));
    CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,expires_at TEXT,max_uses INTEGER,uses INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,user_ids TEXT);
    CREATE TABLE IF NOT EXISTS reads(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
    CREATE TABLE IF NOT EXISTS presence(token TEXT, user_id INTEGER, channel_id INTEGER, seen REAL, PRIMARY KEY(token,channel_id));
    CREATE TABLE IF NOT EXISTS user_events(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,payload_json TEXT);
    ''')
    try: c.execute('ALTER TABLE invitations ADD COLUMN invited_username TEXT')
    except sqlite3.OperationalError: pass
    try: c.execute('ALTER TABLE presence ADD COLUMN seen REAL')
    except sqlite3.OperationalError: pass
    c.execute('UPDATE presence SET seen=? WHERE seen IS NULL',(0.0,))
    return c
def user_obj(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def ws_obj(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def ch_obj(r): return {k:(bool(r[k]) if k in ('is_private','is_dm','is_archived') else r[k]) for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
def token_user(c,t):
    if not t:return None
    r=c.execute('SELECT u.* FROM tokens x JOIN users u ON u.id=x.user_id WHERE x.token=?',(t,)).fetchone()
    return r
def member_role(c,w,u): 
    r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone()
    return r['role'] if r else None
def add_event(c,cid,typ,payload):
    c.execute('INSERT OR IGNORE INTO channel_seq(channel_id,last_event_id) VALUES(?,0)',(cid,))
    c.execute('UPDATE channel_seq SET last_event_id=last_event_id+1 WHERE channel_id=?',(cid,))
    eid=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(cid,)).fetchone()[0]
    c.execute('INSERT INTO events VALUES(?,?,?,?)',(cid,eid,typ,json.dumps(payload)))
    return eid
def message_obj(c,r):
    if not r:return None
    au=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    rs=[]
    for x in c.execute('SELECT emoji, user_id FROM reactions WHERE message_id=?',(r['id'],)):
        q=next((z for z in rs if z['emoji']==x['emoji']),None)
        if not q:q={'emoji':x['emoji'],'count':0,'user_ids':[]};rs.append(q)
        q['count']+=1;q['user_ids'].append(x['user_id'])
    replies=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]
    mentions=[]
    ch=c.execute('SELECT workspace_id FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
    members=[x['user_id'] for x in c.execute('SELECT cm.user_id FROM channel_members cm WHERE cm.channel_id=?',(r['channel_id'],))]
    if ch:
        users={x['username']:x['id'] for x in c.execute('SELECT u.id,u.username FROM users u JOIN members m ON m.user_id=u.id WHERE m.workspace_id=?',(ch['workspace_id'],))}
        for name,uid in users.items():
            if uid!=r['author_id'] and uid in members and re.search(r'(?<![A-Za-z0-9_@])@'+re.escape(name)+r'(?![A-Za-z0-9_])',r['body']):
                mentions.append(uid)
        for g in c.execute('SELECT handle,user_ids FROM groups_ WHERE workspace_id=?',(ch['workspace_id'],)):
            if re.search(r'(?<![A-Za-z0-9_@])@'+re.escape(g['handle'])+r'(?![A-Za-z0-9_])',r['body']):
                mentions.extend(uid for uid in json.loads(g['user_ids'] or '[]') if uid in members and uid!=r['author_id'])
        if re.search(r'(?<![A-Za-z0-9_@])@channel(?![A-Za-z0-9_])',r['body']):
            mentions.extend(uid for uid in members if uid!=r['author_id'])
        if re.search(r'(?<![A-Za-z0-9_@])@here(?![A-Za-z0-9_])',r['body']):
            mentions.extend(uid for uid in members if uid!=r['author_id'] and c.execute('SELECT 1 FROM presence WHERE user_id=? AND channel_id=?',(uid,r['channel_id'])).fetchone())
    mentions=list(dict.fromkeys(mentions))
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_obj(au),'body':r['body'],'parent_id':r['parent_id'],'reply_count':replies,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[{k:r[k] for k in ('id','uploader_id','filename','content_type','size','created_at')} for r in c.execute('SELECT f.* FROM files f JOIN message_files mf ON mf.file_id=f.id WHERE mf.message_id=?',(r['id'],))],'reactions':rs,'mentions':mentions}

def accessible(c,u,cid):
    r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    if not r:return None
    if r['is_dm']: return c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u)).fetchone() and r
    return c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u)).fetchone() and r
