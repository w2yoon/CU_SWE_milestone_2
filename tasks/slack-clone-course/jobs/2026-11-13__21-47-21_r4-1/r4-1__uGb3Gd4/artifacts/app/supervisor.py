import os,signal,subprocess,time,sys
children={}
def start(name,cmd,env=None):
    f=open('/tmp/huddle-'+name+'.log','ab')
    children[name]=subprocess.Popen(cmd,env={**os.environ,**(env or {})},stdout=f,stderr=f,start_new_session=True)
def stop(*_):
    for p in children.values():
        try: os.kill(p.pid,signal.SIGTERM)
        except: pass
    sys.exit(0)
open('/tmp/supervisor.pid','w').write(str(os.getpid()))
signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
start('redis',['redis-server','--bind','127.0.0.1','--port','6379','--save','','--appendonly','no'])
for i in range(3): start(str(i),['python3','/app/server.py'],{'NODE_ID':str(i),'PORT':str(8000+i)})
start('irc',['python3','/app/irc.py'])
while True:
    time.sleep(.5)
    for n,p in list(children.items()):
        if p.poll() is not None:
            if n=='redis': start(n,['redis-server','--bind','127.0.0.1','--port','6379','--save','','--appendonly','no'])
            elif n=='irc': start(n,['python3','/app/irc.py'])
            elif n.isdigit(): start(n,['python3','/app/server.py'],{'NODE_ID':n,'PORT':str(8000+int(n))})
