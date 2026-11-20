import sqlite3, os, json, secrets, hashlib, re
from datetime import datetime, timezone
DB='/app/huddle.db'
def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,timeout=5,check_same_thread=False); c.row_factory=sqlite3.Row
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
 '''); c.commit(); return c
def user_obj(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_obj(r): return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def channel_obj(r):
 return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'],'is_archived':bool(r['is_archived'])}
def auth(c,t):
 r=c.execute('SELECT u.* FROM tokens x JOIN users u ON u.id=x.user_id WHERE x.token=?',(t or '',)).fetchone(); return r
def pwd(p): return hashlib.sha256(p.encode()).hexdigest()
def message_obj(c,r):
 a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
 rs=c.execute('SELECT emoji,COUNT(*) count,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)).fetchall()
 reactions=[{'emoji':x['emoji'],'count':x['count'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rs]
 rc=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted_at IS NULL',(r['id'],)).fetchone()['n']
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_obj(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':rc,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':reactions,'mentions':[]}
def emit(c,cid,typ,payload):
 c.execute('INSERT OR IGNORE INTO channel_seq(channel_id,last_event_id) VALUES(?,0)',(cid,))
 n=c.execute('SELECT last_event_id+1 FROM channel_seq WHERE channel_id=?',(cid,)).fetchone()[0]
 c.execute('UPDATE channel_seq SET last_event_id=? WHERE channel_id=?',(n,cid))
 c.execute('INSERT INTO events VALUES(?,?,?,?)',(cid,n,typ,json.dumps(payload))); return n
def member(c,cid,uid):
 return c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid)).fetchone() is not None
def role(c,wid,uid):
 r=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(wid,uid)).fetchone(); return r['role'] if r else None
def get_channel(c,cid): return c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
def create_message(c,uid,cid,body,parent=None):
 ch=get_channel(c,cid); c.execute('BEGIN IMMEDIATE')
 ts=now(); cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(cid,uid,body,parent,ts)); r=c.execute('SELECT * FROM messages WHERE id=?',(cur.lastrowid,)).fetchone(); typ='message.reply' if parent else 'message.created'; emit(c,cid,typ,{'message':message_obj(c,r)}); c.commit(); return message_obj(c,r)
def change_message(c,uid,mid,body=None,delete=False):
 r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
 if not r:return None,'404'
 ch=get_channel(c,r['channel_id'])
 c.execute('BEGIN IMMEDIATE')
 if delete:
  if not r['deleted_at']: c.execute('UPDATE messages SET deleted_at=? WHERE id=?',(now(),mid))
  emit(c,r['channel_id'],'message.deleted',{'message_id':mid}); c.commit(); return True,None
 if r['deleted_at']: c.rollback(); return None,'409'
 c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(body,now(),mid)); rr=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); emit(c,r['channel_id'],'message.updated',{'message':message_obj(c,rr)}); c.commit(); return message_obj(c,rr),None
