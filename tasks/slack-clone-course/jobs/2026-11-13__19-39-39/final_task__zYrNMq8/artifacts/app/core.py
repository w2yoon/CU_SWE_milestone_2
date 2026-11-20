import sqlite3, os, json, hashlib, secrets, re, datetime, time
DB='/app/huddle.db'
os.makedirs('/app/data/files',exist_ok=True)
def conn():
 for attempt in range(20):
  try:
   c=sqlite3.connect(DB,timeout=5,check_same_thread=False); c.row_factory=sqlite3.Row
   c.execute('PRAGMA busy_timeout=30000')
   return c
  except sqlite3.OperationalError:
   if attempt == 19: raise
   time.sleep(0.15)
def init():
 for attempt in range(20):
  try:
   c=conn(); c.executescript('''
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,dm_a INTEGER,dm_b INTEGER);
CREATE TABLE IF NOT EXISTS cmembers(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS channel_seq(channel_id INTEGER PRIMARY KEY,last_event_id INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,type TEXT,payload_json TEXT,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT);
CREATE TABLE IF NOT EXISTS groups_(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,members TEXT DEFAULT '[]',UNIQUE(workspace_id,handle));
CREATE TABLE IF NOT EXISTS message_files(message_id INTEGER,file_id INTEGER,PRIMARY KEY(message_id,file_id));
CREATE TABLE IF NOT EXISTS presence(user_id INTEGER PRIMARY KEY,last_seen REAL);
CREATE TABLE IF NOT EXISTS user_events(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,payload TEXT,created_at REAL);
''');
   try: c.execute("ALTER TABLE messages ADD COLUMN mentions TEXT DEFAULT '[]'")
   except sqlite3.OperationalError: pass
   c.commit(); c.close(); return
  except sqlite3.OperationalError:
   try: c.close()
   except: pass
   if attempt == 19: raise
   time.sleep(0.2)
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def user(r):
 return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def auth(tok):
 if not tok:return None
 c=conn(); r=c.execute('SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=?',(tok,)).fetchone();c.close();return user(r) if r else None
def emit(c,ch,typ,payload):
 c.execute('INSERT OR IGNORE INTO channel_seq(channel_id,last_event_id) VALUES(?,0)',(ch,))
 n=c.execute('SELECT last_event_id+1 FROM channel_seq WHERE channel_id=?',(ch,)).fetchone()[0]
 c.execute('UPDATE channel_seq SET last_event_id=? WHERE channel_id=?',(n,ch))
 c.execute('INSERT INTO events VALUES(?,?,?,?)',(ch,n,typ,json.dumps(payload,separators=(',',':')))); return n
def reactions(c,mid):
 rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(mid,)).fetchall(); out=[]
 for e in dict.fromkeys(x['emoji'] for x in rows):
  ids=[x['user_id'] for x in rows if x['emoji']==e];out.append({'emoji':e,'count':len(ids),'user_ids':ids})
 return out
def msg(c,r):
 d=dict(r); a=c.execute('SELECT * FROM users WHERE id=?',(d['author_id'],)).fetchone()
 ment=json.loads(d.get('mentions','[]')) if 'mentions' in d else []
 return {'id':d['id'],'channel_id':d['channel_id'],'author_id':d['author_id'],'author':user(a),'body':d['body'],'parent_id':d['parent_id'],'reply_count':c.execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(d['id'],)).fetchone()[0],'created_at':d['created_at'],'edited_at':d['edited_at'],'files':files_for(c,d['id']),'reactions':reactions(c,d['id']),'mentions':ment}
def files_for(c,mid):
 out=[]
 for r in c.execute('SELECT f.* FROM files f JOIN message_files mf ON mf.file_id=f.id WHERE mf.message_id=?',(mid,)):out.append(fileobj(r))
 return out
def fileobj(r): return {k:r[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}
def members(c,ch): return [x['user_id'] for x in c.execute('SELECT user_id FROM cmembers WHERE channel_id=?',(ch,))]
def channel(c,ch): return c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
def accessible(c,u,ch):
 r=channel(c,ch)
 return bool(r and (r['is_dm'] and u in (r['dm_a'],r['dm_b']) or c.execute('SELECT 1 FROM cmembers WHERE channel_id=? AND user_id=?',(ch,u)).fetchone()))
def mentions(c,ch,uid,body):
 ids=set()
 if re.search(r'(?<!\\w)@channel(?!\\w)',body):
  ids.update(x for x in members(c,ch) if x!=uid)
 if re.search(r'(?<!\\w)@here(?!\\w)',body):
  ids.update(x['user_id'] for x in c.execute('SELECT user_id FROM cmembers WHERE channel_id=? AND user_id!=? AND user_id IN (SELECT user_id FROM presence WHERE last_seen>?)',(ch,uid,time.time()-5)))
 for name in re.findall(r'(?<!\\w)@([A-Za-z0-9_]+)',body):
  if name in ('channel','here'): continue
  u=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
  if u and u['id']!=uid and u['id'] in members(c,ch): ids.add(u['id'])
 for handle in re.findall(r'(?<!\\w)@([A-Za-z0-9_-]+)',body):
  g=c.execute('SELECT members FROM groups_ WHERE handle=?',(handle,)).fetchone()
  if g:
   try: ids.update(x for x in json.loads(g['members']) if x!=uid and x in members(c,ch))
   except Exception: pass
 return sorted(ids)
