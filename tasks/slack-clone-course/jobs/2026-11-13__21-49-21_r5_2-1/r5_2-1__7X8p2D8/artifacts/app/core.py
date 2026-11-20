import sqlite3,os,json,hashlib,secrets,re,datetime,base64,time
DB='/app/huddle.db'
def conn():
 c=sqlite3.connect(DB,timeout=5,check_same_thread=False); c.row_factory=sqlite3.Row
 c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=5000'); return c
def init():
 c=conn(); c.executescript('''
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS wm(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
CREATE TABLE IF NOT EXISTS cm(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,UNIQUE(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
CREATE TABLE IF NOT EXISTS seq(channel_id INTEGER PRIMARY KEY,last_event_id INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload TEXT,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,invited_username TEXT,max_uses INTEGER DEFAULT 1,uses INTEGER DEFAULT 0,expires INTEGER);
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,data BLOB);
CREATE TABLE IF NOT EXISTS readstate(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
CREATE TABLE IF NOT EXISTS groupsx(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT UNIQUE,name TEXT,creator_id INTEGER,member_ids TEXT)
''');
 try: c.execute('ALTER TABLE invites ADD COLUMN invited_username TEXT')
 except sqlite3.OperationalError: pass
 c.commit(); c.close()
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00','Z')
def user(r):
 return dict(r) if r else None
def uobj(r):
 if not r:return None
 d=dict(r); return {k:d.get(k,'') for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def token(uid):
 t=secrets.token_urlsafe(24); c=conn(); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); c.commit();c.close();return t
def uid(t):
 if not t:return None
 c=conn();r=c.execute('SELECT user_id FROM tokens WHERE token=?',(t,)).fetchone();c.close();return r[0] if r else None
def ws(c,wid): return dict(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone())
def channel(c,cid): return c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
def member(c,cid,u): return c.execute('SELECT 1 FROM cm WHERE channel_id=? AND user_id=?',(cid,u)).fetchone() is not None
def role(c,wid,u):
 r=c.execute('SELECT role FROM wm WHERE workspace_id=? AND user_id=?',(wid,u)).fetchone();return r[0] if r else None
def emit(c,cid,typ,payload):
 c.execute('INSERT OR IGNORE INTO seq(channel_id,last_event_id) VALUES(?,0)',(cid,))
 c.execute('UPDATE seq SET last_event_id=last_event_id+1 WHERE channel_id=?',(cid,))
 n=c.execute('SELECT last_event_id FROM seq WHERE channel_id=?',(cid,)).fetchone()[0]
 c.execute('INSERT INTO events VALUES(?,?,?,?)',(cid,n,typ,json.dumps(payload)));return n
def mobj(c,r):
 d=dict(r); au=c.execute('SELECT * FROM users WHERE id=?',(d['author_id'],)).fetchone()
 rx=[]
 for x in c.execute('SELECT emoji FROM reactions WHERE message_id=? GROUP BY emoji',(d['id'],)):
  ids=[z[0] for z in c.execute('SELECT user_id FROM reactions WHERE message_id=? AND emoji=?',(d['id'],x[0]))]
  rx.append({'emoji':x[0],'count':len(ids),'user_ids':ids})
 rc=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(d['id'],)).fetchone()[0]
 mentions=[]
 for name in re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_]+)',d['body']):
  z=c.execute('SELECT u.id FROM users u JOIN wm m ON m.user_id=u.id JOIN channels ch ON ch.workspace_id=m.workspace_id WHERE ch.id=? AND u.username=?',(d['channel_id'],name)).fetchone()
  if z and z[0]!=d['author_id'] and z[0] not in mentions: mentions.append(z[0])
 return {'id':d['id'],'channel_id':d['channel_id'],'author_id':d['author_id'],'author':uobj(au),'body':d['body'],'parent_id':d['parent_id'],'reply_count':rc,'created_at':d['created_at'],'edited_at':d['edited_at'],'files':[],'reactions':rx,'mentions':mentions}
def cur(n): return base64.urlsafe_b64encode(str(n).encode()).decode()
def dec(x):
 try:return int(base64.urlsafe_b64decode(x.encode()))
 except:raise ValueError()
init()
