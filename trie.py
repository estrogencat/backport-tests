"""dyld export trie builder."""

def uleb(v):
    out = bytearray()
    while True:
        b = v & 0x7f
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


class Node:
    __slots__ = ('term', 'kids')

    def __init__(self):
        self.term = None       # (flags, addr)
        self.kids = {}         # edge str -> Node


def insert(root, name, flags, addr):
    node, s = root, name
    while True:
        if not s:
            node.term = (flags, addr)
            return
        for edge in list(node.kids):
            if edge[0] != s[0]:
                continue
            # longest common prefix of edge and s
            n = 0
            while n < len(edge) and n < len(s) and edge[n] == s[n]:
                n += 1
            if n == len(edge):
                node, s = node.kids[edge], s[n:]
                break
            # split the edge
            child = node.kids.pop(edge)
            mid = Node()
            mid.kids[edge[n:]] = child
            node.kids[edge[:n]] = mid
            node, s = mid, s[n:]
            break
        else:
            leaf = Node()
            node.kids[s] = leaf
            leaf.term = (flags, addr)
            return


def build(entries):
    """entries: iterable of (name, flags, address). returns trie bytes."""
    root = Node()
    for name, flags, addr in entries:
        insert(root, name, flags, addr)

    order = []
    stack = [root]
    while stack:
        n = stack.pop()
        order.append(n)
        for e in sorted(n.kids):
            stack.append(n.kids[e])

    off = {id(n): 0 for n in order}
    for _ in range(64):
        blobs = []
        cur = 0
        newoff = {}
        for n in order:
            newoff[id(n)] = cur
            if n.term is None:
                body = b'\x00'
            else:
                t = uleb(n.term[0]) + uleb(n.term[1])
                body = uleb(len(t)) + t
            body += bytes([len(n.kids)])
            for e in sorted(n.kids):
                body += e.encode() + b'\x00' + uleb(off[id(n.kids[e])])
            blobs.append(body)
            cur += len(body)
        if newoff == off:
            break
        off = newoff
    blob = b''.join(blobs)
    return blob + b'\x00' * ((8 - len(blob) % 8) % 8)
