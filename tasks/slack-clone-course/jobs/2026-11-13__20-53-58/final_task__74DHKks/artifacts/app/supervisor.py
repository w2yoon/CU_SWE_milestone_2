import os,signal,subprocess,time
children={}
def spawn(name,cmd,env=None):
    e=os.environ.copy();e.update(env or {})
    f=open('/tmp/huddle-'+name+'.log','ab')
    p=subprocess.Popen(cmd,env=e,stdout=f,stderr=f,start_new_session=True)
    children[name]=(p,cmd,e,f)
def start():
    for i in range(3):spawn(str(i),['python3','/app/server.py'],{'NODE_ID':str(i),'PORT':str(8000+i)})
    spawn('redis',['redis-server','--bind','127.0.0.1','--port','6379','--save','','--appendonly','no'])
def stop(*a):
    for p,cmd,e,f in list(children.values()):
        try: os.kill(p.pid,signal.SIGTERM)
        except: pass
    raise SystemExit
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
open('/tmp/supervisor.pid','w').write(str(os.getpid()))
start()
while True:
    time.sleep(.2)
    for n,(p,cmd,e,f) in list(children.items()):
        if p.poll() is not None:
            try:f.close()
            except:pass
            spawn(n,cmd,{'NODE_ID':e['NODE_ID'],'PORT':e['PORT']} if 'PORT' in e else {})
