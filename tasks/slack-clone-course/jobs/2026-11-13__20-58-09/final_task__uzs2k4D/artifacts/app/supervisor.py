import os,signal,subprocess,time
children=[]
open('/tmp/supervisor.pid','w').write(str(os.getpid()))
def launch(cmd,name,env=None):
    f=open('/tmp/huddle-'+name+'.log','ab')
    p=subprocess.Popen(cmd,env={**os.environ,**(env or {})},stdout=f,stderr=f,start_new_session=True); children.append((p,cmd,name,env))
launch(['redis-server','--bind','127.0.0.1','--port','6379','--save','','--appendonly','no'],'redis')
for i in range(3): launch(['python3','/app/server.py'],'%d'%i,{'NODE_ID':str(i),'PORT':str(8000+i)})
launch(['python3','/app/irc.py'],'irc')
def stop(*a):
    for p,_,_,_ in children:
        try: os.kill(p.pid,signal.SIGTERM)
        except: pass
    raise SystemExit
signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
while True:
    for i,(p,cmd,n,e) in enumerate(children):
        if p.poll() is not None: children[i]=(subprocess.Popen(cmd,env={**os.environ,**(e or {})},stdout=open('/tmp/huddle-'+n+'.log','ab'),stderr=subprocess.STDOUT,start_new_session=True),cmd,n,e)
    time.sleep(.3)
