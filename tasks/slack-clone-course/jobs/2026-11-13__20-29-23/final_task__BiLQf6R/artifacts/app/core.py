import sqlite3, os, json, secrets, hashlib, re
from datetime import datetime, timezone
DB='/app/huddle.db'
os.makedirs('/app/data/files',exist_ok=True)
def now(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,timeout=5,isolation_level=None); c.row_factory=sqlite3.Row
 c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=5000'); return c
def init():
 c=db()
 c.executescript('''
 CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
 CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
 CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
 CREATE TABLE IF NOT EXISTS wm(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
 CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS cm(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
 CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
 CREATE TABLE IF NOT EXISTS channel_seq(channel_id INTEGER PRIMARY KEY,last_event_id INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload_json TEXT,PRIMARY KEY(channel_id,event_id));
 CREATE TABLE IF NOT EXISTS dm(a INTEGER,b INTEGER,channel_id INTEGER,UNIQUE(a,b));
 CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT);
 CREATE TABLE IF NOT EXISTS message_files(message_id INTEGER,file_id INTEGER UNIQUE);
 CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,UNIQUE(workspace_id,handle));
 CREATE TABLE IF NOT EXISTS group_users(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));
 CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,node_id INTEGER);
 CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY AUTOINCREMENT,payload TEXT);
 '''); c.close()
def user(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def auth(c,t):
 if not t:return None
 r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(t,)).fetchone(); return r
def emit(c,ch,typ,payload):
 c.execute('BEGIN IMMEDIATE'); c.execute('INSERT OR IGNORE INTO channel_seq(channel_id,last_event_id) VALUES(?,0)',(ch,))
 n=c.execute('SELECT last_event_id+1 FROM channel_seq WHERE channel_id=?',(ch,)).fetchone()[0]
 c.execute('UPDATE channel_seq SET last_event_id=? WHERE channel_id=?',(n,ch))
 p=dict(payload); p.update(type=typ,event_id=n,channel_id=ch)
 c.execute('INSERT INTO events VALUES(?,?,?,?)',(ch,n,typ,json.dumps(p))); c.commit(); return p
def channel(c,i): return c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
def member(c,ch,u): return bool(c.execute('SELECT 1 FROM cm WHERE channel_id=? AND user_id=?',(ch,u)).fetchone())
def wmember(c,w,u): return c.execute('SELECT role FROM wm WHERE workspace_id=? AND user_id=?',(w,u)).fetchone()
def chobj(r): return dict(r) | {'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'is_archived':bool(r['is_archived'])}
def reactions(c,mid):
 rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(mid,)); d={}
 for r in rows:d.setdefault(r['emoji'],[]).append(r['user_id'])
 return [{'emoji':e,'count':len(x),'user_ids':x} for e,x in d.items()]
def mentions(c,ch,body,author):
 cr=channel(c,ch); ids=[]
 for x in re.findall(r'(?<![\w@])@([A-Za-z0-9_+-]+)',body):
  if x=='channel': q=c.execute('SELECT user_id FROM cm WHERE channel_id=?',(ch,)).fetchall(); ids += [r[0] for r in q]
  elif x=='here':
   q=c.execute('SELECT DISTINCT user_id FROM presence WHERE user_id IN (SELECT user_id FROM cm WHERE channel_id=?)',(ch,)); ids += [r[0] for r in q]
  else:
   q=c.execute('SELECT id FROM users WHERE username=?',(x,)).fetchone()
   if q and member(c,ch,q[0]): ids.append(q[0])
   if cr:
    q=c.execute('SELECT gu.user_id FROM groups_ g JOIN group_users gu ON gu.group_id=g.id WHERE g.workspace_id=? AND g.handle=? AND gu.user_id IN (SELECT user_id FROM cm WHERE channel_id=?)',(cr['workspace_id'],x,ch)); ids += [r[0] for r in q]
 return list(dict.fromkeys(i for i in ids if i!=author))
def msg(c,r):
 if not r:return None
 a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[dict(x) for x in c.execute('SELECT f.* FROM files f JOIN message_files mf ON mf.file_id=f.id WHERE mf.message_id=?',(r['id'],))],'reactions':reactions(c,r['id']),'mentions':mentions(c,r['channel_id'],r['body'],r['author_id'])}
def group(c,r):
 ids=[x[0] for x in c.execute('SELECT user_id FROM group_users WHERE group_id=?',(r['id'],))]
 return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':ids}
