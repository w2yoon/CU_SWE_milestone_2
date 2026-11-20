import os, signal, subprocess, time
PIDFILE='/tmp/supervisor.pid'
children={}
def launch(name, cmd, env=None):
    f=open('/tmp/huddle-'+name+'.log','ab')
    e=os.environ.copy()
    if env: e.update(env)
    p=subprocess.Popen(cmd,env=e,stdout=f,stderr=f,start_new_session=True)
    children[name]=(p,f,cmd,env)
open(PIDFILE,'w').write(str(os.getpid()))
def stop(*_):
    for p,f,_,_ in list(children.values()):
        try: os.kill(p.pid,signal.SIGTERM)
        except ProcessLookupError: pass
        f.close()
    raise SystemExit
signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
launch('redis',['redis-server','--bind','127.0.0.1','--port','6379','--save','','--appendonly','no'])
for i in range(3): launch(str(i),['python3','/app/server.py'],{'NODE_ID':str(i),'PORT':str(8000+i)})
launch('irc',['python3','/app/irc.py'])
while True:
    time.sleep(.3)
    for name,(p,f,cmd,env) in list(children.items()):
        if p.poll() is not None:
            f.close(); launch(name,cmd,env)
