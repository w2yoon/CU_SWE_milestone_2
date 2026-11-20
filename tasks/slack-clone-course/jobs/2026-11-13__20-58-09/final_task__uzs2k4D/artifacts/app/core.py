import sqlite3, os, json, secrets, hashlib, re
from datetime import datetime, timezone
DB='/app/huddle.db'
def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
    c=sqlite3.connect(DB, timeout=5, check_same_thread=False)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=5000')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted_at TEXT);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS channel_seq(channel_id INTEGER PRIMARY KEY,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload_json TEXT,PRIMARY KEY(channel_id,event_id));
    ''')
    return c
def userobj(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def auth(token):
    c=db(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(token or '',)).fetchone(); c.close(); return r
def password(p): return hashlib.sha256(p.encode()).hexdigest()
def emit(c,ch,typ,payload):
    c.execute('INSERT OR IGNORE INTO channel_seq(channel_id,last_event_id) VALUES(?,0)',(ch,))
    c.execute('UPDATE channel_seq SET last_event_id=last_event_id+1 WHERE channel_id=?',(ch,))
    eid=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(ch,)).fetchone()[0]
    c.execute('INSERT INTO events VALUES(?,?,?,?)',(ch,eid,typ,json.dumps(payload,separators=(',',':')))); return eid
def member(c,ch,uid):
    return c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch,uid)).fetchone() is not None
def role(c,w,uid):
    r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w,uid)).fetchone(); return r[0] if r else None
def msgobj(c,r):
    u=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    rc=c.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted_at IS NULL',(r['id'],)).fetchone()[0]
    rs=[]
    for x in c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)):
        rs.append({'emoji':x[0],'count':x[1],'user_ids':[int(z) for z in x[2].split(',')]})
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':userobj(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':rc,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':rs,'mentions':[]}
def create_msg(c,uid,ch,body,parent=None):
    t=now(); cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch,uid,body,parent,t)); mid=cur.lastrowid
    m=msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()); emit(c,ch,'message.reply' if parent else 'message.created',{'message':m}); return m
