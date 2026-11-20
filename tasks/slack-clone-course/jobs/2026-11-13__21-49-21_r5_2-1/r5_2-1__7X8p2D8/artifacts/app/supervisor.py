import subprocess,os,signal,time
pid=os.getpid();open('/tmp/supervisor.pid','w').write(str(pid));procs={}
def start(k,cmd,env=None):
 f=open('/tmp/huddle-'+k+'.log','ab');procs[k]=subprocess.Popen(cmd,env={**os.environ,**(env or {})},stdout=f,stderr=f,start_new_session=True)
start('redis',['redis-server','--bind','127.0.0.1','--port','6379','--save','','--appendonly','no'])
for i in range(3):start(str(i),['python3','/app/server.py'],{'NODE_ID':str(i),'PORT':str(8000+i)})
start('irc',['python3','/app/irc.py'])
def stop(*x):
 for p in procs.values():
  try:os.kill(p.pid,signal.SIGTERM)
  except:pass
 raise SystemExit
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
while True:
 for i in range(3):
  if procs[str(i)].poll() is not None:start(str(i),['python3','/app/server.py'],{'NODE_ID':str(i),'PORT':str(8000+i)})
 if procs['irc'].poll() is not None:start('irc',['python3','/app/irc.py'])
 if procs['redis'].poll() is not None:start('redis',['redis-server','--bind','127.0.0.1','--port','6379','--save','','--appendonly','no'])
 time.sleep(.3)
