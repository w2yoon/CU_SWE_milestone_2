import sqlite3, os, json, secrets, hashlib, re
from datetime import datetime, timezone
DB='/app/huddle.db'
def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def conn():
    c=sqlite3.connect(DB, timeout=5, check_same_thread=False)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=5000')
    return c
def init():
    c=conn()
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS channel_seq(channel_id INTEGER PRIMARY KEY,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload_json TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS dms(id INTEGER PRIMARY KEY,user_a INTEGER,user_b INTEGER,channel_id INTEGER,UNIQUE(user_a,user_b));
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,data BLOB);
    CREATE TABLE IF NOT EXISTS attachments(message_id INTEGER,file_id INTEGER,PRIMARY KEY(message_id,file_id));
    CREATE TABLE IF NOT EXISTS groups_tbl(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,user_ids TEXT,UNIQUE(workspace_id,handle));
    CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,uses INTEGER DEFAULT 0,max_uses INTEGER DEFAULT 1,expires_at TEXT); CREATE TABLE IF NOT EXISTS presence(user_id INTEGER PRIMARY KEY,last_seen REAL); CREATE TABLE IF NOT EXISTS user_events(id INTEGER PRIMARY KEY AUTOINCREMENT,type TEXT,payload_json TEXT);
    CREATE INDEX IF NOT EXISTS idx_events_channel_event ON events(channel_id,event_id); CREATE INDEX IF NOT EXISTS idx_messages_channel_parent ON messages(channel_id,parent_id,deleted); CREATE INDEX IF NOT EXISTS idx_messages_parent ON messages(parent_id,deleted);
    '''); c.commit(); c.close()
def userobj(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def wsobj(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def chobj(r):
    x={k:r[k] for k in ('id','workspace_id','name','is_private','is_dm','topic','is_archived')}
    x['is_private']=bool(x['is_private']); x['is_dm']=bool(x['is_dm']); x['is_archived']=bool(x['is_archived']); return x
def fileobj(r): return {k:r[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}
def reactions(c,mid):
    rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(mid,)).fetchall()
    out=[]
    for r in rows:
        x=next((z for z in out if z['emoji']==r['emoji']),None)
        if not x: x={'emoji':r['emoji'],'count':0,'user_ids':[]}; out.append(x)
        x['count']+=1; x['user_ids'].append(r['user_id'])
    return out
def msgobj(c,r):
    a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    rc=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']
    fs=[fileobj(x) for x in c.execute('SELECT f.* FROM files f JOIN attachments a ON a.file_id=f.id WHERE a.message_id=?',(r['id'],))]
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':userobj(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':rc,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':fs,'reactions':reactions(c,r['id']),'mentions':[]}
def event(c,cid,typ,payload):
    c.execute('INSERT OR IGNORE INTO channel_seq(channel_id,last_event_id) VALUES(?,0)',(cid,))
    n=c.execute('UPDATE channel_seq SET last_event_id=last_event_id+1 WHERE channel_id=? RETURNING last_event_id',(cid,)).fetchone()['last_event_id']
    c.execute('INSERT INTO events VALUES(?,?,?,?)',(cid,n,typ,json.dumps(payload,separators=(',',':')))); return n
def hashpw(p): return hashlib.sha256(p.encode()).hexdigest()
def token(uid):
    t=secrets.token_urlsafe(24); c=conn(); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); c.commit(); c.close(); return t
def uid(tok):
    if not tok:return None
    c=conn(); r=c.execute('SELECT user_id FROM tokens WHERE token=?',(tok,)).fetchone(); c.close(); return r['user_id'] if r else None
init()
