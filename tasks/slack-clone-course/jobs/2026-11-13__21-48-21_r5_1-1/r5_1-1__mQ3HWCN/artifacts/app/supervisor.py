import os,signal,subprocess,time,sys
children={}
def launch(name,cmd,env=None):
    f=open('/tmp/huddle-'+name+'.log','ab')
    p=subprocess.Popen(cmd,env={**os.environ,**(env or {})},stdout=f,stderr=f,start_new_session=True)
    children[name]=(p,f,cmd,env or {})
def stop(*_):
    for p,f,_,_ in list(children.values()):
        try: os.kill(p.pid,signal.SIGTERM)
        except ProcessLookupError: pass
        f.close()
    sys.exit(0)
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
open('/tmp/supervisor.pid','w').write(str(os.getpid()))
launch('redis',['redis-server','--bind','127.0.0.1','--port','6379','--save','','--appendonly','no'])
for i in range(3): launch(str(i),['python3','/app/server.py'],{'NODE_ID':str(i),'PORT':str(8000+i)})
launch('irc',['python3','/app/irc.py'])
while True:
    for name,(p,f,cmd,env) in list(children.items()):
        if p.poll() is not None:
            f.close(); launch(name,cmd,env)
    time.sleep(.5)
