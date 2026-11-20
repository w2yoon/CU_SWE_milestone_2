import os,signal,subprocess,time
children={}
start_redis=True
def start(name,cmd,env=None):
    f=open('/tmp/huddle-'+name+'.log','ab');p=subprocess.Popen(cmd,env={**os.environ,**(env or {})},stdout=f,stderr=f,start_new_session=True);children[name]=(p,f)
start('redis',['redis-server','--bind','127.0.0.1','--port','6379','--save','', '--appendonly','no'])
for i in range(3):start(str(i),['python3','/app/server.py'],{'NODE_ID':str(i),'PORT':str(8000+i)})
start('irc',['python3','/app/irc.py'])
def stop(*a):
    for p,f in children.values():
        try:p.terminate()
        except:pass
    raise SystemExit
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
open('/tmp/supervisor.pid','w').write(str(os.getpid()))
while True:
    for n,(p,f) in list(children.items()):
        if p.poll() is not None:
            f.close()
            if n=='redis': start(n,['redis-server','--bind','127.0.0.1','--port','6379','--save','', '--appendonly','no'])
            elif n.isdigit(): start(n,['python3','/app/server.py'],{'NODE_ID':n,'PORT':str(8000+int(n))})
            else: start(n,['python3','/app/irc.py'])
    time.sleep(.5)
