import sqlite3, os, json, secrets, hashlib, re
from datetime import datetime, timezone
DB='/app/huddle.db'
def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
    c=sqlite3.connect(DB,timeout=5,check_same_thread=False)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=5000')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS memberships(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted_at TEXT);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS channel_seq(channel_id INTEGER PRIMARY KEY,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload_json TEXT,PRIMARY KEY(channel_id,event_id));
    ''')
    return c
def user_obj(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_obj(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def channel_obj(r):
    return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'] or '','is_archived':bool(r['is_archived'])}
def token_user(c,t):
    r=c.execute('SELECT u.* FROM tokens x JOIN users u ON u.id=x.user_id WHERE x.token=?',(t or '',)).fetchone()
    return r
def emit(c,ch,typ,payload):
    c.execute('INSERT OR IGNORE INTO channel_seq(channel_id,last_event_id) VALUES(?,0)',(ch,))
    c.execute('UPDATE channel_seq SET last_event_id=last_event_id+1 WHERE channel_id=?',(ch,))
    n=c.execute('SELECT last_event_id FROM channel_seq WHERE channel_id=?',(ch,)).fetchone()[0]
    c.execute('INSERT INTO events VALUES(?,?,?,?)',(ch,n,typ,json.dumps(payload)))
    return n
def msg_obj(c,r):
    if not r:return None
    a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    rx=[]
    for x in c.execute('SELECT emoji, user_id FROM reactions WHERE message_id=?',(r['id'],)):
        z=next((q for q in rx if q['emoji']==x['emoji']),None)
        if not z:z={'emoji':x['emoji'],'count':0,'user_ids':[]};rx.append(z)
        z['count']+=1;z['user_ids'].append(x['user_id'])
    cnt=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted_at IS NULL',(r['id'],)).fetchone()[0]
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_obj(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':cnt,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':rx,'mentions':[]}
def member(c,ch,uid):
    return c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch,uid)).fetchone() is not None
def role(c,w,uid):
    r=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(w,uid)).fetchone()
    return r[0] if r else None
def create_message(c,uid,ch,body,parent=None):
    r=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
    if not r:return None,'missing'
    if r['is_archived']:return None,'archived'
    if not member(c,ch,uid):return None,'forbidden'
    if not body or not body.strip():return None,'blank'
    t=now(); cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch,uid,body,parent,t))
    m=msg_obj(c,c.execute('SELECT * FROM messages WHERE id=?',(cur.lastrowid,)).fetchone())
    emit(c,ch,'message.reply' if parent else 'message.created',{'message':m})
    c.commit(); return m,None
