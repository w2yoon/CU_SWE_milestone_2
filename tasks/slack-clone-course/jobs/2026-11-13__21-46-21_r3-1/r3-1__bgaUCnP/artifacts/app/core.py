import sqlite3,os,json,secrets,hashlib,re,datetime,time,uuid
DB='/app/huddle.db'
def db():
 c=sqlite3.connect(DB,timeout=5,check_same_thread=False); c.row_factory=sqlite3.Row
 c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=5000'); init(c); return c
def init(c):
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,dm_key TEXT);
CREATE TABLE IF NOT EXISTS cmembers(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS seq(channel_id INTEGER PRIMARY KEY,last_event_id INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload TEXT,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,data BLOB);
CREATE TABLE IF NOT EXISTS message_files(message_id INTEGER,file_id INTEGER UNIQUE);
CREATE TABLE IF NOT EXISTS groupsx(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT UNIQUE,name TEXT,creator_id INTEGER);
CREATE TABLE IF NOT EXISTS groupmembers(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));
CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,node_id INTEGER);
CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY AUTOINCREMENT,payload TEXT);
'''); c.commit()
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def user(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def auth(c,t):
 r=c.execute('select u.* from users u join tokens t on t.user_id=u.id where t.token=?',(t or '',)).fetchone(); return r
def emit(c,ch,typ,payload):
 c.execute('insert or ignore into seq(channel_id,last_event_id) values(?,0)',(ch,))
 n=c.execute('update seq set last_event_id=last_event_id+1 where channel_id=? returning last_event_id',(ch,)).fetchone()[0]
 c.execute('insert into events values(?,?,?,?)',(ch,n,typ,json.dumps(payload,separators=(',',':')))); return n
def reactions(c,mid):
 out=[]
 for r in c.execute('select emoji,user_id from reactions where message_id=? order by rowid',(mid,)):
  x=next((z for z in out if z['emoji']==r['emoji']),None)
  if not x: x={'emoji':r['emoji'],'count':0,'user_ids':[]};out.append(x)
  x['count']+=1;x['user_ids'].append(r['user_id'])
 return out
def mention_ids(c,ch,author,body):
 out=[]
 names=re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)',body)
 for name in names:
  u=c.execute('select id from users where username=?',(name,)).fetchone()
  if u and u['id']!=author and c.execute('select 1 from cmembers where channel_id=? and user_id=?',(ch,u['id'])).fetchone():out.append(u['id'])
  g=c.execute('select g.id from groupsx g join channels c2 on c2.workspace_id=g.workspace_id where c2.id=? and g.handle=?',(ch,name)).fetchone()
  if g:
   out += [x['user_id'] for x in c.execute('select user_id from groupmembers where group_id=?',(g['id'],)) if x['user_id']!=author and c.execute('select 1 from cmembers where channel_id=? and user_id=?',(ch,x['user_id'])).fetchone()]
 if '@here' in body:
  out += [x['user_id'] for x in c.execute('select distinct user_id from presence') if x['user_id']!=author and c.execute('select 1 from cmembers where channel_id=? and user_id=?',(ch,x['user_id'])).fetchone()]
 if '@channel' in body:
  out += [x['user_id'] for x in c.execute('select user_id from cmembers where channel_id=?',(ch,)) if x['user_id']!=author]
 return list(dict.fromkeys(out))

def msg(c,r):
 if not r:return None
 u=c.execute('select * from users where id=?',(r['author_id'],)).fetchone()
 return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':c.execute('select count(*) from messages where parent_id=? and deleted=0',(r['id'],)).fetchone()[0],'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[dict(x) for x in c.execute('select f.id,f.uploader_id,f.filename,f.content_type,f.size,f.created_at from files f join message_files mf on mf.file_id=f.id where mf.message_id=?',(r['id'],)).fetchall()],'reactions':reactions(c,r['id']),'mentions':mention_ids(c,r['channel_id'],r['author_id'],r['body'])}
def member(c,ch,uid):
 return c.execute('select 1 from cmembers where channel_id=? and user_id=?',(ch,uid)).fetchone() is not None
def channel(c,i): return c.execute('select * from channels where id=?',(i,)).fetchone()
