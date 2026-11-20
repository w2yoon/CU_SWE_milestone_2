import os,signal,subprocess,time
children={}
base='/app'
def launch(name,cmd,env=None):
 f=open('/tmp/huddle-'+name+'.log','ab')
 p=subprocess.Popen(cmd,env={**os.environ,**(env or {})},stdout=f,stderr=f,start_new_session=True)
 children[name]=(p,f,cmd,env or {})
def stop(*_):
 for p,f,_,_ in list(children.values()):
  try: os.kill(p.pid,signal.SIGTERM)
  except: pass
 for p,f,_,_ in list(children.values()):
  try: p.wait(timeout=2)
  except: pass
 open('/tmp/supervisor.pid','w').close(); raise SystemExit
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
open('/tmp/supervisor.pid','w').write(str(os.getpid()))
subprocess.run(['python3','-c','import core; c=core.conn(); c.execute("PRAGMA journal_mode=WAL"); c.close(); core.init()'],cwd='/app',check=True)
launch('redis',['redis-server','--bind','127.0.0.1','--port','6379','--save','','--appendonly','no'])
for i in range(3): launch(str(i),['python3','/app/server.py'],{'NODE_ID':str(i),'PORT':str(8000+i)})
while True:
 for name,(p,f,cmd,e) in list(children.items()):
  if p.poll() is not None:
   f.close(); launch(name,cmd,e)
 time.sleep(.25)
