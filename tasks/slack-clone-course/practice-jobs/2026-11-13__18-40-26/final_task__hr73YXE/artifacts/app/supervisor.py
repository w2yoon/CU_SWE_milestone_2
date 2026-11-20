import os,signal,subprocess,time,sys
os.chdir('/app')
open('/tmp/supervisor.pid','w').write(str(os.getpid()))
spec=[('redis',['redis-server','--bind','127.0.0.1','--port','6379','--save','','--appendonly','no'],{}),
      ('http0',['python3','/app/server.py'],{'NODE_ID':'0','PORT':'8000'}),
      ('http1',['python3','/app/server.py'],{'NODE_ID':'1','PORT':'8001'}),
      ('http2',['python3','/app/server.py'],{'NODE_ID':'2','PORT':'8002'}),
      ('irc',['python3','/app/irc.py'],{})]
procs={}
def spawn(name,cmd,env):
    f=open('/tmp/huddle-'+name+'.log','ab')
    e=os.environ.copy(); e.update(env)
    procs[name]=subprocess.Popen(cmd,env=e,stdout=f,stderr=f,start_new_session=True)
def stop(*_):
    for p in list(procs.values()):
        try: os.kill(p.pid,signal.SIGTERM)
        except: pass
    time.sleep(.2); sys.exit(0)
signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
for x in spec: spawn(*x)
while True:
    for name,cmd,env in spec:
        if procs[name].poll() is not None: spawn(name,cmd,env)
    time.sleep(.3)
