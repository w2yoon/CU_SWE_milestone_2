import sqlite3, os, json, hashlib, secrets, re
from datetime import datetime, timezone

DB='/app/huddle.db'
def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
    c=sqlite3.connect(DB, timeout=5, check_same_thread=False)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA synchronous=NORMAL'); c.execute('PRAGMA busy_timeout=5000')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS workspace_members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_seq(channel_id INTEGER PRIMARY KEY,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload_json TEXT,PRIMARY KEY(channel_id,event_id));
    '''); return c
def user_obj(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def auth(c,t):
    r=c.execute('SELECT u.* FROM tokens x JOIN users u ON u.id=x.user_id WHERE x.token=?',(t or '',)).fetchone()
    return r
def msg_obj(c,r):
    u=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    replies=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_obj(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':replies,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[],'mentions':[]}
def emit(c,channel,typ,payload):
    c.execute('INSERT OR IGNORE INTO channel_seq(channel_id,last_event_id) VALUES(?,0)',(channel,))
    c.execute('UPDATE channel_seq SET last_event_id=last_event_id+1 WHERE channel_id=?',(channel,))
    eid=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(channel,)).fetchone()[0]
    c.execute('INSERT INTO events VALUES(?,?,?,?)',(channel,eid,typ,json.dumps(payload)))
    return eid
def create_message(c,channel,uid,body,parent=None):
    if not body or not body.strip(): raise ValueError('empty')
    ch=c.execute('SELECT * FROM channels WHERE id=?',(channel,)).fetchone()
    if not ch: raise LookupError()
    if ch['is_archived']: raise PermissionError('archived')
    if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(channel,uid)).fetchone(): raise PermissionError('member')
    mentions=[]
    for x in re.findall(r'(?<!\w)@([A-Za-z0-9_]+)',body):
        if x=='channel':
            mentions += [z[0] for z in c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(channel,uid))]
        else:
            z=c.execute('SELECT u.id FROM users u JOIN workspace_members wm ON wm.user_id=u.id WHERE u.username=? AND wm.workspace_id=?',(x,ch['workspace_id'])).fetchone()
            if z and z[0]!=uid: mentions.append(z[0])
    mentions=list(dict.fromkeys(mentions))
    t=now(); cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(channel,uid,body,parent,t))
    r=c.execute('SELECT * FROM messages WHERE id=?',(cur.lastrowid,)).fetchone(); mo=msg_obj(c,r)
    mo['mentions']=mentions
    eid=emit(c,channel,'message.reply' if parent else 'message.created',{'message':mo})
    return mo,eid
