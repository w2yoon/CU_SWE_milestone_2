import os,signal,subprocess,time,sys
children={}
def launch(name,cmd,env=None):
 f=open('/tmp/huddle-'+name+'.log','ab')
 p=subprocess.Popen(cmd,env={**os.environ,**(env or {})},stdout=f,stderr=f,start_new_session=True)
 children[name]=(p,f); return p
def stop(*_):
 for p,f in list(children.values()):
  try: os.kill(p.pid,signal.SIGTERM)
  except ProcessLookupError: pass
  f.close()
 raise SystemExit
signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
open('/tmp/supervisor.pid','w').write(str(os.getpid()))
spec=[('redis',['redis-server','--bind','127.0.0.1','--port','6379','--save','','--appendonly','no'],{}),
('0',['python3','/app/server.py'],{'NODE_ID':'0','PORT':'8000'}),
('1',['python3','/app/server.py'],{'NODE_ID':'1','PORT':'8001'}),
('2',['python3','/app/server.py'],{'NODE_ID':'2','PORT':'8002'}),
('irc',['python3','/app/irc.py'],{})]
while True:
 for n,cmd,e in spec:
  p=children.get(n,(None,None))[0]
  if p is None or p.poll() is not None:
   if p:
    try: children[n][1].close()
    except: pass
   try: launch(n,cmd,e)
   except Exception: pass
 time.sleep(.5)
