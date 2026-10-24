import socket,struct,time,json,re
class G:
    def __init__(s,host,port):
        s.s=socket.create_connection((host,port),timeout=60);s.noack=False
        s.buf=b""
    def _rx(s,n=65536):
        d=s.s.recv(n)
        if not d: raise EOFError
        s.buf+=d
    def send(s,cmd):
        cs=sum(cmd.encode())&0xff
        s.s.sendall(b"$"+cmd.encode()+b"#"+b"%02x"%cs)
        if not s.noack:
            while True:
                while not s.buf: s._rx()
                c=s.buf[:1];s.buf=s.buf[1:]
                if c==b"+":break
    def recv(s):
        while True:
            i=s.buf.find(b"$")
            if i>=0:
                j=s.buf.find(b"#",i)
                if j>=0 and len(s.buf)>=j+3:
                    pkt=s.buf[i+1:j];s.buf=s.buf[j+3:]
                    if not s.noack: s.s.sendall(b"+")
                    return pkt.decode('latin1')
            s._rx()
    def cmd(s,c): s.send(c);return s.recv()
    @staticmethod
    def unesc(t):
        o=[];i=0
        while i<len(t):
            if t[i]=='}' and i+1<len(t): o.append(chr(ord(t[i+1])^0x20));i+=2
            else: o.append(t[i]);i+=1
        return ''.join(o)
    def regs(s,stop):
        r={}
        for part in stop.split(";"):
            m=re.fullmatch(r"(?:T[0-9a-f]{2})?([0-9a-f]{2}):([0-9a-f]+)",part)
            if m: r[int(m.group(1),16)]=int.from_bytes(bytes.fromhex(m.group(2)),"little")
        return r
    def mem(s,a,n):
        out=b""
        while n>0:
            k=min(n,0x400);h=s.cmd("m%x,%x"%(a,k))
            if h.startswith("E") or not h: break
            out+=bytes.fromhex(h);a+=k;n-=k
        return out
    def q64(s,a):
        m=s.mem(a,8);return int.from_bytes(m,"little") if len(m)==8 else None
    def cstr(s,a,n=96):
        m=s.mem(a,n);return m.split(b"\0")[0].decode('latin1')

def sa(x): return x.encode('ascii','replace').decode()
def objc_class(g,obj):
    isa=g.q64(obj)&0xffffffff8
    return isa
def class_info(g,cls):
    bits=g.q64(cls+0x20)&0xffffffff8
    ro=g.q64(bits+8)
    if ro&1: ro=g.q64(ro&~7)
    ro&=0xffffffff8
    name=g.cstr(g.q64(ro+0x18))
    sup=g.q64(cls+8)
    ivl=g.q64(ro+0x30)
    ivars=[]
    if ivl:
        cnt=int.from_bytes(g.mem(ivl+4,4),"little")
        for i in range(cnt):
            e=ivl+8+i*32
            offp=g.q64(e);nm=g.q64(e+8);ty=g.q64(e+16)
            off=int.from_bytes(g.mem(offp,4),"little") if offp else None
            ivars.append((off,g.cstr(nm) if nm else "",g.cstr(ty,40) if ty else ""))
    return name,sup,ivars
