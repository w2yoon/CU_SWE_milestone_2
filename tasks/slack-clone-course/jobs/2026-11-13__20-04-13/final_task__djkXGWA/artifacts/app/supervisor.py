import os,signal,subprocess,time,sys
children={}
def spawn(name,cmd,env=None):
    f=open('/tmp/huddle-'+name+'.log','ab')
    p=subprocess.Popen(cmd,env=env or os.environ.copy(),stdout=f,stderr=f,start_new_session=True)
    children[name]=(p,f,cmd,env)
def start():
    open('/tmp/supervisor.pid','w').write(str(os.getpid()))
    for i in range(3):
        e=os.environ.copy(); e.update(NODE_ID=str(i),PORT=str(8000+i))
        spawn(str(i),['python3','/app/server.py'],e)
    spawn('redis',['redis-server','--bind','127.0.0.1','--port','6379','--save','','--appendonly','no'])
def stop(*a):
    for p,f,_,_ in list(children.values()):
        try: os.kill(p.pid,signal.SIGTERM)
        except: pass
    for p,f,_,_ in list(children.values()):
        try: p.wait(2)
        except: pass
        f.close()
    sys.exit(0)
signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
start()
while True:
    time.sleep(.2)
    for n,(p,f,c,e) in list(children.items()):
        if p.poll() is not None:
            f.close(); spawn(n,c,e)
