import os,signal,subprocess,time
procs={}
def start(n,cmd,env=None):
 f=open('/tmp/huddle-'+n+'.log','ab');p=subprocess.Popen(cmd,env={**os.environ,**(env or {})},stdout=f,stderr=f,start_new_session=True);procs[n]=(p,f)
def stop(*a):
 for p,f in procs.values():
  try: os.kill(p.pid,signal.SIGTERM)
  except: pass
 raise SystemExit
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
open('/tmp/supervisor.pid','w').write(str(os.getpid()))
start('redis',['redis-server','--bind','127.0.0.1','--port','6379','--save','','--appendonly','no'])
for i in range(3):start(str(i),['python3','/app/server.py'],{'NODE_ID':str(i),'PORT':str(8000+i)})
start('irc',['python3','/app/irc.py'])
while True:
 time.sleep(.5)
 for n,(p,f) in list(procs.items()):
  if p.poll() is not None:
   f.close()
   if n=='redis':start(n,['redis-server','--bind','127.0.0.1','--port','6379','--save','','--appendonly','no'])
   elif n in ('0','1','2'):start(n,['python3','/app/server.py'],{'NODE_ID':n,'PORT':str(8000+int(n))})
   elif n=='irc':start(n,['python3','/app/irc.py'])
