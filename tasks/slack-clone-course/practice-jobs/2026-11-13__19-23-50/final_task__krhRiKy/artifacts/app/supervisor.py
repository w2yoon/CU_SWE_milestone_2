import os,signal,subprocess,time,sys
children={}
def spawn(name,cmd,env=None):
    f=open('/tmp/huddle-'+name+'.log','ab')
    e=os.environ.copy(); e.update(env or {})
    children[name]=subprocess.Popen(cmd,env=e,stdout=f,stderr=f,start_new_session=True)
def stop(*_):
    for p in list(children.values()):
        try: os.kill(p.pid,signal.SIGTERM)
        except OSError: pass
    time.sleep(.3)
    for p in list(children.values()):
        try: os.kill(p.pid,signal.SIGKILL)
        except OSError: pass
    raise SystemExit
open('/tmp/supervisor.pid','w').write(str(os.getpid()))
signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
for i in range(3): spawn(str(i),['python3','/app/server.py'],{'NODE_ID':str(i),'PORT':str(8000+i)})
spawn('redis',['redis-server','--bind','127.0.0.1','--port','6379','--save','','--appendonly','no'])
while True:
    time.sleep(.25)
    for name,p in list(children.items()):
        if p.poll() is not None:
            if name=='redis': cmd=['redis-server','--bind','127.0.0.1','--port','6379','--save','','--appendonly','no']; env={}
            else: i=int(name); cmd=['python3','/app/server.py']; env={'NODE_ID':str(i),'PORT':str(8000+i)}
            spawn(name,cmd,env)
