import os,signal,subprocess,time
children={}
open('/tmp/supervisor.pid','w').write(str(os.getpid()))
def launch(k,cmd,env=None):
    f=open('/tmp/huddle-'+k+'.log','ab')
    p=subprocess.Popen(cmd,env=env,start_new_session=True,stdout=f,stderr=f);children[k]=(p,f)
for i in range(3): launch(str(i),['python3','/app/server.py'],dict(os.environ,NODE_ID=str(i),PORT=str(8000+i)))
launch('redis',['redis-server','--bind','127.0.0.1','--port','6379','--save','','--appendonly','no'])
launch('irc',['python3','/app/irc.py'])
def stop(*x):
    for p,f in children.values():
        try:p.terminate()
        except:pass
    raise SystemExit
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
while True:
    for k,(p,f) in list(children.items()):
        if p.poll() is not None:
            if k=='redis': launch(k,['redis-server','--bind','127.0.0.1','--port','6379','--save','','--appendonly','no'])
            elif k=='irc': launch(k,['python3','/app/irc.py'])
            elif k.isdigit(): launch(k,['python3','/app/server.py'],dict(os.environ,NODE_ID=k,PORT=str(8000+int(k))))
    time.sleep(.3)
