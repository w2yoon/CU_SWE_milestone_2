import sqlite3,os,json,secrets,hashlib,re,datetime,time
DB='/app/huddle.db'
def conn():
 c=sqlite3.connect(DB,timeout=8,check_same_thread=False); c.row_factory=sqlite3.Row
 c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=8000'); init(c); return c
def init(c):
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
 CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
 CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
 CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
 CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
 CREATE TABLE IF NOT EXISTS cmembers(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
 CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted_at TEXT);
 CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
 CREATE TABLE IF NOT EXISTS seq(channel_id INTEGER PRIMARY KEY,last_event_id INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload TEXT,PRIMARY KEY(channel_id,event_id));''')
 c.commit()
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def user(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def token_uid(c,t):
 r=c.execute('SELECT user_id FROM tokens WHERE token=?',(t or '',)).fetchone(); return r['user_id'] if r else None
def emit(c,ch,typ,payload):
 c.execute('INSERT OR IGNORE INTO seq(channel_id,last_event_id) VALUES(?,0)',(ch,))
 c.execute('UPDATE seq SET last_event_id=last_event_id+1 WHERE channel_id=?',(ch,))
 n=c.execute('SELECT last_event_id FROM seq WHERE channel_id=?',(ch,)).fetchone()[0]
 c.execute('INSERT INTO events VALUES(?,?,?,?)',(ch,n,typ,json.dumps(payload)))
 return n
def members(c,ch):
 return [r['user_id'] for r in c.execute('SELECT user_id FROM cmembers WHERE channel_id=?',(ch,))]
def msgobj(c,r):
 u=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
 rc=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted_at IS NULL',(r['id'],)).fetchone()[0]
 rr={}
 for x in c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? AND EXISTS(SELECT 1 FROM messages WHERE id=? AND deleted_at IS NULL)',(r['id'],r['id'])): rr.setdefault(x['emoji'],[]).append(x['user_id'])
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':rc,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[{'emoji':e,'count':len(v),'user_ids':v} for e,v in rr.items()],'mentions':[]}
def make_msg(c,uid,ch,body,parent=None):
 t=now(); cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch,uid,body,parent,t)); mid=cur.lastrowid
 o=msgobj(c,c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()); emit(c,ch,'message.reply' if parent else 'message.created',{'message':o}); return o
