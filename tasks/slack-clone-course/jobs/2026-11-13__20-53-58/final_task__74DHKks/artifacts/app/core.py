import sqlite3, os, json, secrets, hashlib, re
from datetime import datetime, timezone

DB='/app/huddle.db'
def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
    c=sqlite3.connect(DB,timeout=5,check_same_thread=False)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA synchronous=NORMAL'); c.execute('PRAGMA busy_timeout=5000')
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
    ''')
    return c
def user_obj(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def auth(token):
    if not token:return None
    c=db(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(token,)).fetchone();c.close();return r
def register(username,password,display_name=''):
    if not re.fullmatch(r'[A-Za-z0-9_]+',username or '') or len(password or '')<8: raise ValueError()
    c=db()
    try:
        c.execute('BEGIN IMMEDIATE'); c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(username,password,display_name or username)); uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; tok=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(tok,uid));c.commit()
    except sqlite3.IntegrityError:c.rollback();c.close();raise RuntimeError()
    r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone();c.close();return r,tok
def login(username,password):
    c=db();r=c.execute('SELECT * FROM users WHERE username=? AND password=?',(username,password)).fetchone()
    if not r:c.close();return None
    t=secrets.token_urlsafe(24);c.execute('INSERT INTO tokens VALUES(?,?)',(t,r['id']));c.commit();c.close();return r,t
def event(c,channel_id,typ,payload):
    c.execute('INSERT OR IGNORE INTO channel_seq(channel_id,last_event_id) VALUES(?,0)',(channel_id,))
    c.execute('UPDATE channel_seq SET last_event_id=last_event_id+1 WHERE channel_id=?',(channel_id,))
    eid=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(channel_id,)).fetchone()[0]
    c.execute('INSERT INTO events VALUES(?,?,?,?)',(channel_id,eid,typ,json.dumps(payload)));return eid
def workspace_obj(r): return {'id':r['id'],'slug':r['slug'],'name':r['name'],'owner_id':r['owner_id'],'join_mode':r['join_mode']}
def channel_obj(r): return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'],'is_archived':bool(r['is_archived'])}
def msg_obj(c,r):
    a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    rx=[]
    for x in c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)):
        rx.append({'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]})
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_obj(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':c.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':rx,'mentions':[]}
def create_message(uid,chid,body,parent=None):
    if not body or not body.strip(): raise ValueError()
    c=db(); c.execute('BEGIN IMMEDIATE')
    ch=c.execute('SELECT * FROM channels WHERE id=?',(chid,)).fetchone()
    if not ch:c.rollback();c.close();raise LookupError()
    if ch['is_archived']:c.rollback();c.close();raise PermissionError('archived')
    if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(chid,uid)).fetchone():c.rollback();c.close();raise PermissionError()
    t=now();c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(chid,uid,body,parent,t)); mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; m=msg_obj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()); typ='message.reply' if parent else 'message.created';eid=event(c,chid,typ,m);c.commit();c.close();m['_event_id']=eid;return m
