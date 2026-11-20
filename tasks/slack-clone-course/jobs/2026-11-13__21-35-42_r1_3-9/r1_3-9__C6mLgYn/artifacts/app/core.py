import sqlite3,os,json,secrets,hashlib,re,datetime,time
DB='/app/huddle.db'
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def db():
 c=sqlite3.connect(DB,timeout=8,check_same_thread=False); c.row_factory=sqlite3.Row
 c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=8000')
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT "UTC",avatar_url TEXT DEFAULT "",status_text TEXT DEFAULT "",status_emoji TEXT DEFAULT "");
 CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
 CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT "open");
 CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
 CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT "",is_archived INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS cmembers(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
 CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted_at TEXT);
 CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
 CREATE TABLE IF NOT EXISTS seq(channel_id INTEGER PRIMARY KEY,last_event_id INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload TEXT,PRIMARY KEY(channel_id,event_id));''')
 return c
def user(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def token(uid):
 t=secrets.token_urlsafe(24); c=db(); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); c.commit(); c.close(); return t
def auth(t):
 if not t:return None
 c=db(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone(); c.close(); return r
def emit(c,ch,typ,payload):
 c.execute('INSERT OR IGNORE INTO seq(channel_id,last_event_id) VALUES(?,0)',(ch,))
 n=c.execute('UPDATE seq SET last_event_id=last_event_id+1 WHERE channel_id=?',(ch,)).rowcount
 eid=c.execute('SELECT last_event_id FROM seq WHERE channel_id=?',(ch,)).fetchone()[0]
 c.execute('INSERT INTO events VALUES(?,?,?,?)',(ch,eid,typ,json.dumps(payload))); return eid
def channel(r):
 return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'],'is_archived':bool(r['is_archived'])}
def msg(c,r):
 a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
 rx=[]
 for x in c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(r['id'],)):
  rx.append({'emoji':x['emoji'],'count':x['n'],'user_ids':[int(v) for v in x['ids'].split(',')]})
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':c.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted_at IS NULL',(r['id'],)).fetchone()[0],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':rx,'mentions':[]}
