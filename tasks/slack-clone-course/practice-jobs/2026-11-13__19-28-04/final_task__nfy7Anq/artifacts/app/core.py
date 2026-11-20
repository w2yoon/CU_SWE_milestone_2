import sqlite3, os, json, secrets, hashlib, re, datetime, time, uuid
DB='/app/huddle.db'
DATA='/app/data/files'
os.makedirs(DATA,exist_ok=True)
def db():
 c=sqlite3.connect(DB,timeout=5,check_same_thread=False); c.row_factory=sqlite3.Row
 c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=5000'); init(c); return c
def init(c):
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
 CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
 CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
 CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
 CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS cmembers(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
 CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
 CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT);
 CREATE TABLE IF NOT EXISTS msgfiles(message_id INTEGER,file_id INTEGER,PRIMARY KEY(message_id,file_id));
 CREATE TABLE IF NOT EXISTS channel_seq(channel_id INTEGER PRIMARY KEY,last_event_id INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload_json TEXT,PRIMARY KEY(channel_id,event_id));
 CREATE TABLE IF NOT EXISTS dms(a INTEGER,b INTEGER,channel_id INTEGER,UNIQUE(a,b));
 CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,UNIQUE(workspace_id,handle));
 CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));
 CREATE TABLE IF NOT EXISTS readstate(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id)); CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,node_id INTEGER); CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY AUTOINCREMENT,payload TEXT);''')
 c.commit()
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def user(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def ws(r): return dict(r)
def ch(r): 
 d=dict(r); d['is_private']=bool(d['is_private']); d['is_dm']=bool(d['is_dm']); d['is_archived']=bool(d['is_archived']); return d
def token_user(c,t):
 if not t:return None
 r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone()
 return r
def emit(c,cid,typ,payload):
 c.execute('INSERT OR IGNORE INTO channel_seq(channel_id,last_event_id) VALUES(?,0)',(cid,))
 n=c.execute('SELECT last_event_id+1 FROM channel_seq WHERE channel_id=?',(cid,)).fetchone()[0]
 c.execute('UPDATE channel_seq SET last_event_id=? WHERE channel_id=?',(n,cid))
 c.execute('INSERT INTO events VALUES(?,?,?,?)',(cid,n,typ,json.dumps(payload)))
 return n
def reactions(c,mid):
 rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY emoji,user_id',(mid,)).fetchall(); out=[]
 for e in sorted(set(x['emoji'] for x in rows)):
  ids=[x['user_id'] for x in rows if x['emoji']==e]; out.append({'emoji':e,'count':len(ids),'user_ids':ids})
 return out
def fileobj(r): return {k:r[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}
def msg(c,r):
 d=dict(r); d['author']=user(c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()); d['reply_count']=c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]; d['files']=[fileobj(x) for x in c.execute('SELECT f.* FROM files f JOIN msgfiles m ON m.file_id=f.id WHERE m.message_id=?',(r['id'],))]; d['reactions']=reactions(c,r['id']); d['mentions']=mention_ids(c,r['channel_id'],r['body'],r['author_id']); return d
def group_obj(c,r):
 return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':[x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=? ORDER BY user_id',(r['id'],))]}
def member(c,cid,uid): return c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,uid)).fetchone() is not None
def wsrole(c,wid,uid): 
 r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(wid,uid)).fetchone(); return r['role'] if r else None
def mention_ids(c,cid,body,author):
 r=c.execute('SELECT workspace_id FROM channels WHERE id=?',(cid,)).fetchone(); ids=[]
 for name in re.findall(r'(?<![\\w@])@([A-Za-z0-9_-]+)',body):
  if name=='channel':
   ids += [x['user_id'] for x in c.execute('SELECT user_id FROM cmembers WHERE channel_id=? AND user_id!=?',(cid,author))]
  elif name=='here':
   ids += [x['user_id'] for x in c.execute('SELECT DISTINCT p.user_id FROM presence p JOIN cmembers m ON m.user_id=p.user_id WHERE m.channel_id=? AND p.user_id!=?',(cid,author))]
  else:
   g=c.execute('SELECT id FROM groups_ WHERE workspace_id=? AND handle=?',(r['workspace_id'],name)).fetchone()
   if g:
    ids += [x['user_id'] for x in c.execute('SELECT gm.user_id FROM group_members gm JOIN cmembers cm ON cm.user_id=gm.user_id WHERE gm.group_id=? AND cm.channel_id=? AND gm.user_id!=?',(g['id'],cid,author))]
   else:
    x=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
    if x and x['id']!=author and c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(cid,x['id'])).fetchone(): ids.append(x['id'])
 return list(dict.fromkeys(ids))
